"""Shared private helpers for browser action mixins."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from ...runtime.browser_protocol import (
    PROTOCOL_VERSION,
    STYLE_KEYS,
    DomComponentHint,
    DomElementPayload,
    DomRect,
)
from ..constants import _USER_WAIT_TIMEOUT
from ..thread import _WORKER
from .. import observe as _bobs
from ..urls import is_shallow_site_rewind, urls_match

from .fillable import element_is_fillable, non_fillable_input_error, _PROBE_FILLABLE_JS

class BrowserActionHelpersMixin:
    def _ref_selector(sess: dict[str, Any], ref: str) -> str:
        key = str(ref or "").strip()
        sel = (sess.get("refs") or {}).get(key)
        if not sel:
            raise ValueError(
                f"unknown or stale ref '{ref}' — call browser_snapshot again "
                "to refresh element refs"
            )
        return sel

    @staticmethod
    def _counters(sess: dict[str, Any]) -> tuple[int, int]:
        return len(sess.get("console") or []), len(sess.get("network") or [])

    def _delta_diag(self, sess: dict[str, Any], c0: int, n0: int) -> dict[str, Any]:
        """Console errors / failed requests recorded since the action started."""
        with self._lock:
            logs = list(sess.get("console") or [])[c0:]
            net = list(sess.get("network") or [])[n0:]
        errs = [e for e in logs if e.get("type") in ("error", "pageerror")]
        failed = [
            e for e in net
            if e.get("failed") or int(e.get("status") or 0) >= 400
        ]
        out: dict[str, Any] = {}
        if errs:
            out["new_error_count"] = len(errs)
            out["new_console_errors"] = [
                {"type": e.get("type"), "text": (e.get("text") or "")[:300]}
                for e in errs[-3:]
            ]
        if failed:
            out["new_failed_count"] = len(failed)
            out["new_failed_requests"] = [
                {
                    "method": e.get("method"),
                    "url": (e.get("url") or "")[:200],
                    "status": e.get("status"),
                    "failure": e.get("failure") or "",
                }
                for e in failed[-3:]
            ]
        return out

    @staticmethod
    def _is_playwright_timeout(exc: BaseException) -> bool:
        if type(exc).__name__ == "TimeoutError":
            return True
        text = str(exc).lower()
        return "timeout" in text and "exceeded" in text
    def _run_action(
        self,
        uid: str,
        action: Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        observe_after: bool = False,
    ) -> dict[str, Any]:
        async def _op() -> dict[str, Any]:
            from .browser_queue import page_fingerprint, page_unchanged

            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
                before = page_fingerprint(
                    str(getattr(page, "url", "") or sess.get("url") or ""),
                    sess.get("scroll_mark") if isinstance(sess.get("scroll_mark"), dict) else None,
                )
            c0, n0 = self._counters(sess)
            try:
                result = dict(await action(page, sess))
            except Exception as exc:
                if self._is_target_closed(exc):
                    raise
                # A dialog aborts the click while the prompt is still on screen.
                # Hold this tool until the user answers; do not hand the abort
                # back to the model as if the click had finished.
                if not (self._dialog_pending(page) or dialog_interrupted(exc)):
                    raise
                result = {"action": "dialog"}
            gate = await self._await_user_gate(page, sess)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            # Give console/network events a moment to arrive, then diff.
            await asyncio.sleep(0.4)
            with self._lock:
                cur = sess.get("page") or page
                switched = cur is not page
                try:
                    cur_url = str(getattr(cur, "url", "") or sess.get("url") or "")
                except Exception:
                    cur_url = str(sess.get("url") or "")
                sess["url"] = cur_url
                sess["last_used"] = time.time()
                new_tab = switched or bool(sess.pop("new_tab", False))
                if cur_url:
                    self._last_url[uid] = cur_url
            diag = self._delta_diag(sess, c0, n0)
            out = {"url": cur_url, **result, **diag}
            if new_tab:
                out["new_tab"] = True
                out["note"] = (
                    "the action opened a NEW TAB and the sandbox switched to it — "
                    "read the observation below for fresh indices on this page"
                )
            if gate.get("needs_human"):
                out["needs_human"] = True
                if gate.get("challenge"):
                    out["challenge"] = gate["challenge"]
            if observe_after:
                try:
                    obs = await self._observe_on_page(cur, sess, limit=100)
                    note = str(out.get("action") or out.get("note") or "action")
                    packed = _bobs.observation_payload(
                        obs, tabs=obs.get("tabs"), action_note=note
                    )
                    out["observation"] = packed.get("observation")
                    out["elements"] = packed.get("elements")
                    out["scroll"] = packed.get("scroll")
                    out["title"] = packed.get("title")
                    if packed.get("needs_human"):
                        out["needs_human"] = True
                        out["challenge"] = packed.get("challenge") or out.get("challenge")
                        with self._lock:
                            sess["needs_human"] = True
                    scroll = packed.get("scroll") if isinstance(packed.get("scroll"), dict) else None
                    with self._lock:
                        sess["scroll_mark"] = scroll or {}
                    after = page_fingerprint(str(out.get("url") or ""), scroll)
                    if (
                        str(result.get("action") or "") == "click"
                        and page_unchanged(before, after)
                        and not gate.get("dialogs")
                        and not gate.get("needs_human")
                    ):
                        out["stopped"] = True
                        out["note"] = (
                            "同一次点击没有改变页面，已停止重复。请换一个元素或滚动一屏。"
                        )
                        with self._lock:
                            sess["last_click"] = {
                                "index": result.get("index"),
                                "unchanged": True,
                                "url": after[0],
                            }
                    elif str(result.get("action") or "") == "click":
                        with self._lock:
                            sess["last_click"] = {"index": result.get("index"), "unchanged": False}
                except Exception as exc:  # noqa: BLE001
                    out["observation_error"] = str(exc)
            if gate.get("dialogs"):
                out["dialogs"] = gate["dialogs"]
            gate_note = str(gate.get("note") or "").strip()
            if gate_note:
                prev = str(out.get("note") or "").strip()
                out["note"] = f"{prev} {gate_note}".strip() if prev else gate_note
            return out

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)
    async def _probe_fillable(self, page: Any, sel: str) -> dict[str, Any]:
        """Inspect whether ``sel`` resolves to a Playwright-fillable element."""
        try:
            info = await page.evaluate(_PROBE_FILLABLE_JS, sel)
        except Exception:
            info = None
        if not isinstance(info, dict):
            return {"found": False, "fillable": False}
        return info

    async def _reject_if_not_fillable(
        self,
        page: Any,
        sel: str,
        *,
        index: int | None = None,
        action: str = "input",
    ) -> dict[str, Any] | None:
        """Return an error payload if the target cannot be filled; else None."""
        info = await self._probe_fillable(page, sel)
        if not info.get("found"):
            where = f" index={int(index)}" if index is not None else f" selector={sel!r}"
            return {
                "ok": False,
                "action": action,
                "error": (
                    f"element not found{where}. Re-run browser_observe and use a "
                    "fresh index for a fillable field."
                ),
                "index": index,
                "selector": sel,
            }
        tag = str(info.get("tag") or "")
        role = str(info.get("role") or "")
        ce = bool(info.get("contenteditable"))
        if element_is_fillable(tag=tag, role=role, contenteditable=ce):
            return None
        err = non_fillable_input_error(tag=tag, role=role, index=index, selector=sel)
        out: dict[str, Any] = {
            "ok": False,
            "action": action,
            "error": err,
            "tag": tag,
            "role": role,
            "selector": sel,
        }
        if index is not None:
            out["index"] = int(index)
        return out

    def _index_selector(self, sess: dict[str, Any], index: int) -> str:
        amap = sess.get("index_map") or {}
        if int(index) in amap:
            return str(amap[int(index)])
        return f'[{_bobs.IDX_ATTR}="{int(index)}"]'

    def _agent_hold(self, uid: str, index: int | None = None) -> dict[str, Any] | None:
        """Refuse agent clicks while a person must finish, or the same click is stuck."""
        from .browser_queue import human_hold_result, repeated_click_result, skip_repeated_click

        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return None
            url = str(sess.get("url") or "")
            if sess.get("needs_human"):
                return human_hold_result(url)
            if index is not None and skip_repeated_click(sess.get("last_click"), index):
                return repeated_click_result(url, index)
        return None

