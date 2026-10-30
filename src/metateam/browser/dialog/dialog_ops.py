"""BrowserSandbox mixin: dialog_ops."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

from ...runtime.browser_protocol import (
    PROTOCOL_VERSION,
    STYLE_KEYS,
    DomComponentHint,
    DomElementPayload,
    DomRect,
)
from ...session.tenant_context import get_user_id
from ..session.constants import (
    HOST_KIND,
    SESSION_IDLE_SECONDS,
    SOCIAL_BROWSING,
    _HUMAN_CONFIRM_S,
    _REAP_INTERVAL_SECONDS,
    _USER_WAIT_TIMEOUT,
)
from ..session.thread import _WORKER
from .dialogs import (
    dialog_interrupted,
    dialog_result_note,
    merge_dialog_log,
    public_dialog,
    release_unblocked_dialogs,
)

_DIALOG_HOOK_JS = r"""
(() => {
  if (window.__sidekickDialogHook) return;
  window.__sidekickDialogHook = true;
  const log = [];
  window.__sidekickDialogLog = log;
  window.__sidekickDialogOpen = false;
  const nativeAlert = window.alert.bind(window);
  const nativeConfirm = window.confirm.bind(window);
  const nativePrompt = window.prompt.bind(window);
  function arm(entry) {
    log.push(entry);
    if (log.length > 20) log.shift();
    window.__sidekickDialogOpen = true;
  }
  function disarm() {
    window.__sidekickDialogOpen = log.some((e) => e && e.open);
  }
  window.alert = function (message) {
    const entry = { type: "alert", message: String(message ?? ""), open: true };
    arm(entry);
    try { nativeAlert(message); entry.accepted = true; }
    finally { entry.open = false; disarm(); }
  };
  window.confirm = function (message) {
    const entry = { type: "confirm", message: String(message ?? ""), open: true };
    arm(entry);
    let accepted = false;
    try { accepted = !!nativeConfirm(message); entry.accepted = accepted; }
    finally { entry.open = false; disarm(); }
    return accepted;
  };
  window.prompt = function (message, defaultValue) {
    const entry = {
      type: "prompt",
      message: String(message ?? ""),
      default: String(defaultValue ?? ""),
      open: true,
    };
    arm(entry);
    let value = null;
    try {
      value = nativePrompt(message, defaultValue);
      entry.value = value;
      entry.accepted = value !== null;
    } finally { entry.open = false; disarm(); }
    return value;
  };
})();
"""

__all__ = ['BrowserDialogMixin', '_DIALOG_HOOK_JS']

class BrowserDialogMixin:

    def _dialog_state_for(self, page: Any) -> dict[str, Any]:
        states = getattr(self, "_dialog_states", None)
        if states is None:
            states = {}
            self._dialog_states = states
        key = id(page)
        state = states.get(key)
        if state is None:
            state = {"open": 0, "items": [], "armed": False, "hooked": False}
            states[key] = state
        return state

    def _dialog_pending(self, page: Any) -> bool:
        state = getattr(self, "_dialog_states", {}).get(id(page))
        if not state:
            return False
        return any(it.get("open") for it in state.get("items") or [])

    @staticmethod
    def _recount_dialogs(state: dict[str, Any]) -> int:
        n = sum(1 for it in state.get("items") or [] if it.get("open"))
        state["open"] = n
        return n

    async def _arm_user_dialogs(self, page: Any) -> None:
        """Own JS dialogs so Playwright does not dismiss them before the user clicks.

        With no listener, Playwright dismisses alert/confirm/prompt and accepts
        beforeunload immediately, then aborts the click. The native dialog can
        still be on screen while the model already plans the next step.
        """
        if page is None:
            return
        state = self._dialog_state_for(page)
        if not state.get("hooked"):
            try:
                await page.evaluate(_DIALOG_HOOK_JS)
                state["hooked"] = True
            except Exception:
                pass
        ctx = getattr(page, "context", None)
        if ctx is not None and not getattr(ctx, "_sidekick_dialog_init", False):
            try:
                await ctx.add_init_script(_DIALOG_HOOK_JS)
                setattr(ctx, "_sidekick_dialog_init", True)
            except Exception:
                pass
        if state.get("armed"):
            return
        state["armed"] = True

        def _on_dialog(dialog: Any) -> None:
            try:
                url = str(getattr(page, "url", "") or "")
            except Exception:
                url = ""
            try:
                kind = str(getattr(dialog, "type", "") or "")
                message = str(getattr(dialog, "message", "") or "")
            except Exception:
                kind, message = "dialog", ""
            state["items"].append(
                {
                    "type": kind or "dialog",
                    "message": message[:500],
                    "open": True,
                    "accepted": None,
                    "_dialog": dialog,
                    "_url": url,
                }
            )
            self._recount_dialogs(state)

        def _on_dialog_closed(*args: Any) -> None:
            dialog = args[0] if args else None
            matched = False
            for item in state["items"]:
                if (
                    item.get("open")
                    and item.get("type") != "file"
                    and item.get("_dialog") is dialog
                ):
                    item["open"] = False
                    matched = True
                    break
            if not matched:
                for item in state["items"]:
                    if item.get("open") and item.get("type") != "file":
                        item["open"] = False
                        break
            self._recount_dialogs(state)

        def _on_file(chooser: Any) -> None:
            item = {
                "type": "file",
                "message": "choose a file",
                "open": True,
                "accepted": None,
            }
            state["items"].append(item)
            self._recount_dialogs(state)

            async def _watch() -> None:
                how = "cancelled"
                try:
                    how = await asyncio.wait_for(
                        chooser.element.evaluate(
                            """el => new Promise((resolve) => {
                              let done = false;
                              const finish = (v) => { if (!done) { done = true; resolve(v); } };
                              el.addEventListener('change', () => finish('chosen'), {once: true});
                              el.addEventListener('cancel', () => finish('cancelled'), {once: true});
                            })"""
                        ),
                        timeout=_HUMAN_CONFIRM_S,
                    )
                except Exception:
                    how = "closed"
                item["open"] = False
                item["accepted"] = how == "chosen"
                item["value"] = how
                self._recount_dialogs(state)

            try:
                asyncio.get_running_loop().create_task(_watch())
            except Exception:
                item["open"] = False
                self._recount_dialogs(state)

        def _rehook(frame: Any = None) -> None:
            try:
                main = getattr(page, "main_frame", None)
                if frame is not None and main is not None and frame != main:
                    return
            except Exception:
                pass
            state["hooked"] = False

            async def _again() -> None:
                try:
                    await page.evaluate(_DIALOG_HOOK_JS)
                    state["hooked"] = True
                except Exception:
                    pass

            try:
                asyncio.get_running_loop().create_task(_again())
            except Exception:
                pass

        try:
            page.on("dialog", _on_dialog)
            page.on("dialogclosed", _on_dialog_closed)
            page.on("filechooser", _on_file)
            page.on("framenavigated", _rehook)
            page.on("close", lambda *_a: getattr(self, "_dialog_states", {}).pop(id(page), None))
        except Exception:
            state["armed"] = False

    async def _wait_user_dialogs(self, page: Any) -> None:
        """Block until the user closes the dialog, or the human wait expires."""
        deadline = time.monotonic() + _HUMAN_CONFIRM_S
        while self._dialog_pending(page) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if not self._dialog_pending(page):
                break
            try:
                await asyncio.wait_for(page.evaluate("() => 1"), timeout=1.0)
            except Exception:
                continue
            try:
                url = str(getattr(page, "url", "") or "")
            except Exception:
                url = ""
            release_unblocked_dialogs(
                self._dialog_state_for(page)["items"], page_ready=True, url=url
            )
            self._recount_dialogs(self._dialog_state_for(page))
        await self._merge_hook_log(page)

    async def _merge_hook_log(self, page: Any) -> None:
        try:
            raw = await asyncio.wait_for(
                page.evaluate(
                    """() => {
                      const log = window.__sidekickDialogLog;
                      if (!Array.isArray(log) || !log.length) return [];
                      return log.splice(0, log.length);
                    }"""
                ),
                timeout=2.0,
            )
        except Exception:
            return
        if isinstance(raw, list):
            merge_dialog_log(self._dialog_state_for(page)["items"], raw)

    def _collect_dialogs(self, page: Any) -> list[dict[str, Any]]:
        state = self._dialog_state_for(page)
        items = list(state.get("items") or [])
        closed = [it for it in items if not it.get("open")]
        state["items"] = [it for it in items if it.get("open")]
        self._recount_dialogs(state)
        published = [public_dialog(it) for it in closed]
        published.extend(public_dialog(it) for it in state["items"])
        return published

    async def _await_user_gate(self, page: Any, sess: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Wait out dialogs and security checks before the tool result is delivered."""
        await self._arm_user_dialogs(page)
        await asyncio.sleep(0)
        if self._dialog_pending(page):
            await self._wait_user_dialogs(page)
        dialogs = self._collect_dialogs(page)
        note = dialog_result_note(dialogs)
        still_open = any(d.get("open") for d in dialogs)
        challenge: dict[str, Any] = {"challenged": False}
        if not still_open:
            try:
                challenge = await self._probe_challenge(page)
            except Exception:
                challenge = {"challenged": False}
            if challenge.get("challenged"):
                deadline = time.monotonic() + _HUMAN_CONFIRM_S
                while challenge.get("challenged") and time.monotonic() < deadline:
                    await asyncio.sleep(1.2)
                    try:
                        challenge = await self._probe_challenge(page)
                    except Exception:
                        challenge = {"challenged": False}
                        break
        captcha_blocked = (not still_open) and bool(challenge.get("challenged"))
        # An open dialog must not set the session hold. That hold returns the
        # next click immediately, so the model thinks again while the prompt
        # is still up. Leave the dialog open in page state; the next action
        # waits in this same gate.
        if sess is not None and (captcha_blocked or not still_open):
            sess["needs_human"] = captcha_blocked
        needs_human = still_open or captcha_blocked
        if needs_human and not still_open:
            challenge_note = (
                "页面上的安全验证还在。用户还没有完成。"
                "不要调用下一个工具，等用户完成后再继续。"
            )
            note = f"{note} {challenge_note}".strip() if note else challenge_note
        return {
            "dialogs": dialogs,
            "note": note,
            "needs_human": needs_human,
            "challenge": challenge if challenge.get("challenged") else None,
        }

    def _apply_gate(self, sess: Optional[dict[str, Any]], info: dict[str, Any]) -> dict[str, Any]:
        if not sess or not isinstance(info, dict):
            return info
        gate = sess.pop("_last_gate", None) or {}
        if not gate:
            return info
        info = dict(info)
        if gate.get("dialogs"):
            info["dialogs"] = gate["dialogs"]
        if gate.get("needs_human"):
            info["needs_human"] = True
            if gate.get("challenge"):
                info["challenge"] = gate["challenge"]
        note = str(gate.get("note") or "").strip()
        if note:
            prev = str(info.get("note") or "").strip()
            info["note"] = f"{prev} {note}".strip() if prev else note
        return info
