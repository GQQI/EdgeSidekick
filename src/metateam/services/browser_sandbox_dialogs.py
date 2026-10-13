"""Browser sandbox mixin."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

from .browser_sandbox_worker import _WORKER
from .browser_sandbox_util import (
    HOST_KIND,
    _HUMAN_CONFIRM_S,
    _DIALOG_HOOK_JS,
    dialog_result_note,
    release_unblocked_dialogs,
    merge_dialog_log,
    public_dialog,
)

class BrowserSandboxDialogsMixin:
    @staticmethod
    def _is_nav_aborted(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "err_aborted" in msg or "(-3)" in msg or "net::err_aborted" in msg

    @staticmethod
    def _page_alive(page: Any) -> bool:
        if page is None:
            return False
        try:
            return not page.is_closed()
        except Exception:
            return False

    @staticmethod
    def _cdp_connected(browser: Any) -> bool:
        try:
            return bool(browser.is_connected())
        except Exception:
            return False

    def _session_alive(self, sess: Optional[dict[str, Any]]) -> bool:
        if not sess:
            return False
        if not self._page_alive(sess.get("page")):
            return False
        browser = sess.get("browser")
        try:
            if browser is not None and hasattr(browser, "is_connected"):
                return bool(browser.is_connected())
        except Exception:
            return False
        return True

    @staticmethod
    def _is_target_closed(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "has been closed" in msg or "target closed" in msg or "browser has been closed" in msg

    async def _dispose_session(self, uid: str, sess: Optional[dict[str, Any]]) -> None:
        with self._lock:
            current = self._sessions.get(uid)
            if current is sess or (sess is None and current is not None):
                self._sessions.pop(uid, None)
        if not sess:
            return
        if sess.get("cdp"):
            return
        for key in ("context", "browser"):
            obj = sess.get(key)
            if obj is None:
                continue
            try:
                await obj.close()
            except Exception:
                pass

    def _public(self, sess: dict[str, Any]) -> dict[str, Any]:
        return {
            "host": HOST_KIND,
            "url": sess.get("url") or "about:blank",
            "started_at": sess.get("started_at"),
            "ready": True,
        }

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

    def _hook_page_events(self, uid: str, page: Any) -> None:
        """Wire console/pageerror/network recorders onto a Playwright page once."""
        try:
            if not getattr(page, "_sidekick_hooks", False):
                page.on("console", lambda msg: self._on_console(uid, msg))
                page.on("pageerror", lambda err: self._on_pageerror(uid, err))
                page.on("response", lambda resp: self._on_response(uid, resp))
                page.on("requestfailed", lambda req: self._on_request_failed(uid, req))
                try:
                    setattr(page, "_sidekick_hooks", True)
                except Exception:
                    pass
            try:
                asyncio.get_running_loop().create_task(self._arm_user_dialogs(page))
            except Exception:
                pass
        except Exception:
            pass

    def _on_console(self, uid: str, msg: Any) -> None:
        try:
            entry = {
                "type": getattr(msg, "type", "") or "",
                "text": (getattr(msg, "text", "") or "")[:2000],
                "ts": time.time(),
            }
        except Exception:
            return
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return
            logs = sess.setdefault("console", [])
            logs.append(entry)
            if len(logs) > 300:
                del logs[: len(logs) - 300]

    def _on_pageerror(self, uid: str, err: Any) -> None:
        """Uncaught JS exception — recorded as a console-level error entry."""
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return
            logs = sess.setdefault("console", [])
            logs.append(
                {"type": "pageerror", "text": str(err)[:2000], "ts": time.time()}
            )
            if len(logs) > 300:
                del logs[: len(logs) - 300]

    def _on_response(self, uid: str, resp: Any) -> None:
        try:
            req = getattr(resp, "request", None)
            entry = {
                "method": (getattr(req, "method", "") or "")[:16],
                "url": (getattr(resp, "url", "") or "")[:300],
                "status": int(getattr(resp, "status", 0) or 0),
                "failed": False,
                "resource": (getattr(req, "resource_type", "") or "")[:24],
                "ts": time.time(),
            }
        except Exception:
            return
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return
            net = sess.setdefault("network", [])
            net.append(entry)
            if len(net) > 400:
                del net[: len(net) - 400]

    def _on_request_failed(self, uid: str, req: Any) -> None:
        try:
            entry = {
                "method": (getattr(req, "method", "") or "")[:16],
                "url": (getattr(req, "url", "") or "")[:300],
                "status": 0,
                "failed": True,
                "failure": str(getattr(req, "failure", "") or "")[:120],
                "resource": (getattr(req, "resource_type", "") or "")[:24],
                "ts": time.time(),
            }
        except Exception:
            return
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return
            net = sess.setdefault("network", [])
            net.append(entry)
            if len(net) > 400:
                del net[: len(net) - 400]
