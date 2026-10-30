"""Motion / keyboard / wait actions for the browser sandbox."""
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


class BrowserMotionMixin:
    async def _hover_on(
        self,
        page: Any,
        sel: str,
        *,
        sess: Optional[dict[str, Any]] = None,
        timeout_ms: int = 15000,
    ) -> None:
        try:
            await page.hover(sel, timeout=timeout_ms)
            return
        except Exception as exc:
            if not (
                self._is_playwright_timeout(exc)
                and sess is not None
                and sess.get("cdp")
            ):
                raise
        await page.hover(sel, timeout=timeout_ms, force=True)
    def scroll(
        self,
        *,
        user_id: Optional[str] = None,
        direction: str = "down",
        amount: Optional[int] = None,
        selector: str = "",
        content: Optional[int] = None,
        pages: Optional[float] = None,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid)
        if held:
            return held
        sel = (selector or "").strip()
        dirn = (direction or "down").strip().lower()
        content_n = int(content) if content else 0
        page_n = float(pages) if pages else 0.0

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            jumped: dict[str, Any] = {}
            if content_n > 0:
                jumped = dict(await page.evaluate(_bobs.scroll_to_content_js(), content_n) or {})
            elif sel:
                try:
                    await page.locator(sel).scroll_into_view_if_needed(timeout=15000)
                except Exception as exc:
                    # Hidden desktop BrowserView: settle via JS instead.
                    if sess.get("cdp") and self._is_playwright_timeout(exc):
                        await page.evaluate(
                            "s => { const el = document.querySelector(s);"
                            " if (el) el.scrollIntoView({block: 'center'}); }",
                            sel,
                        )
                    else:
                        raise
            elif dirn == "top":
                await page.evaluate("() => window.scrollTo(0, 0)")
            elif dirn == "bottom":
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            else:
                # Scroll the real scroller (window or the inner box that
                # actually moves). pages is in viewports; amount is pixels.
                vh = 1.0
                if page_n:
                    vh = page_n if dirn != "up" else -abs(page_n)
                elif amount:
                    vh = (abs(int(amount)) / 800.0) * (-1 if dirn == "up" else 1)
                else:
                    vh = -1.0 if dirn == "up" else 1.0
                jumped = dict(await page.evaluate(_bobs.scroll_pages_js(), vh) or {})
            pos = await page.evaluate(
                "() => ({x: window.scrollX, y: window.scrollY, "
                "maxY: document.body.scrollHeight - window.innerHeight})"
            )
            return {
                "url": str(getattr(page, "url", "") or ""),
                **(pos or {}),
                **jumped,
            }

        return _WORKER.call(_op, timeout=30.0)

    def hover_selector(self, selector: str, *, user_id: Optional[str] = None) -> str:
        uid = self._lane_uid(user_id)
        sel = (selector or "").strip()
        if not sel:
            return "ERROR: empty selector"

        async def _op() -> str:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                await self._hover_on(page, sel, sess=sess)
            except Exception as exc:
                if self._is_target_closed(exc):
                    raise
                if not (self._dialog_pending(page) or dialog_interrupted(exc)):
                    raise
            gate = await self._await_user_gate(page, sess)
            hovered = f"hovered {sel}"
            gate_note = str(gate.get("note") or "").strip()
            if gate_note:
                hovered += f" [{gate_note}]"
            return hovered

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)

    def press_key(
        self, key: str, *, user_id: Optional[str] = None, selector: str = ""
    ) -> str:
        uid = self._lane_uid(user_id)
        k = (key or "").strip()
        if not k:
            return "ERROR: empty key"
        sel = (selector or "").strip()

        async def _op() -> str:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                if sel:
                    await page.focus(sel, timeout=15000)
                    await page.press(sel, k, timeout=15000)
                else:
                    # keyboard.press() sends the key to the CURRENTLY FOCUSED
                    # element. Between tool calls focus is easily lost, so an
                    # unfocused Enter lands on <body> and silently does nothing
                    # (the model then retypes in a loop). Guard submit keys.
                    if k.lower() in ("enter", "return"):
                        try:
                            active = await page.evaluate(
                                "() => { const a = document.activeElement; return { tag: a ? "
                                "(a.tagName || '') : '', editable: !!a && (a.tagName === 'INPUT' "
                                "|| a.tagName === 'TEXTAREA' || a.isContentEditable === true) }; }"
                            )
                        except Exception:
                            active = None
                        if not (active or {}).get("editable"):
                            tag = str((active or {}).get("tag") or "body").lower()
                            return (
                                f"ERROR: Enter was NOT delivered — keyboard focus is on <{tag}>, "
                                "not a text field, so nothing was submitted and nothing changed. "
                                "Do NOT retype. Preferred: browser_click the submit control. Enter only works "
                                "immediately after browser_input focused the field."
                            )
                    await page.keyboard.press(k)
            except Exception as exc:
                if self._is_target_closed(exc):
                    raise
                if not (self._dialog_pending(page) or dialog_interrupted(exc)):
                    raise
            gate = await self._await_user_gate(page, sess)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            with self._lock:
                cur = sess.get("page") or page
                switched = cur is not page
                sess["url"] = cur.url
                sess.pop("new_tab", False)
            note = " [new tab opened — now viewing it]" if switched else ""
            tip = ""
            if k.lower() in ("enter", "return") and not switched:
                try:
                    cur_url = str(getattr(page, "url", "") or "")
                except Exception:
                    cur_url = ""
                # If we still look like a site home (no query string), Enter
                # likely did not submit — nudge toward clicking the button.
                try:
                    q = urlparse(cur_url).query if cur_url else ""
                except Exception:
                    q = ""
                if not (q or "").strip():
                    tip = (
                        " [tip: verify the page actually submitted (URL/content changed). "
                        "If not, do NOT retry Enter — browser_click the submit button]"
                    )
            gate_note = str(gate.get("note") or "").strip()
            if gate_note:
                note += f" [{gate_note}]"
            return f"pressed {k}" + (f" on {sel}" if sel else "") + note + tip

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)

    def wait_for(
        self,
        *,
        user_id: Optional[str] = None,
        selector: str = "",
        state: str = "visible",
        timeout_ms: int = 8000,
    ) -> str:
        uid = self._lane_uid(user_id)
        sel = (selector or "").strip()
        st = (state or "visible").strip().lower()
        if st not in ("visible", "hidden", "attached", "detached"):
            st = "visible"
        ms = max(200, min(int(timeout_ms or 8000), 30000))

        async def _op() -> str:
            with self._lock:
                page = await self._alive_page(uid)
            if sel:
                await page.locator(sel).wait_for(state=st, timeout=ms)
                return f"{sel} is now {st}"
            await asyncio.sleep(ms / 1000.0)
            return f"waited {ms}ms"

        return _WORKER.call(_op, timeout=(ms / 1000.0) + 15.0)

    def human_act(
        self,
        *,
        user_id: Optional[str] = None,
        lane: Optional[str] = None,
        op: str = "click",
        x: float = 0,
        y: float = 0,
        text: str = "",
        key: str = "",
    ) -> dict[str, Any]:
        """Let the person drive the sandbox page: click, type, or press a key."""
        uid = self._lane_uid(user_id, lane)
        kind = (op or "click").strip().lower()

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            if kind == "click":
                await page.mouse.click(float(x), float(y))
            elif kind == "type":
                await page.keyboard.type(text or "")
            elif kind == "key":
                await page.keyboard.press(key or "Enter")
            else:
                return {"ok": False, "error": f"unknown op {kind}"}
            url = str(getattr(page, "url", "") or "")
            with self._lock:
                if url:
                    sess["url"] = url
                sess["needs_human"] = False
                sess.pop("last_click", None)
            return {"ok": True, "op": kind, "url": url}

        return _WORKER.call(_op, timeout=20.0)
    def scroll_to_text(self, text: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        needle = (text or "").strip()
        if not needle:
            raise ValueError("empty text")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            raw = await page.evaluate(_bobs.scroll_to_text_js(), needle)
            return {"action": "scroll_to_text", "text": needle, **dict(raw or {})}

        return self._run_action(uid, _do, observe_after=True)

    def send_keys(
        self, keys: str, *, user_id: Optional[str] = None, index: Optional[int] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        key = (keys or "").strip()
        if not key:
            raise ValueError("empty keys")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            if index is not None:
                sel = self._index_selector(sess, int(index))
                await self._highlight_index(page, int(index))
                try:
                    await page.locator(sel).first.focus(timeout=8000)
                finally:
                    await self._clear_highlight(page)
            await page.keyboard.press(key)
            return {"action": "send_keys", "keys": key, "index": index}

        return self._run_action(uid, _do, observe_after=True)

