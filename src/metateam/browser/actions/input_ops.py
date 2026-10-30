"""Fill / type / select actions for the browser sandbox."""
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


class BrowserInputMixin:
    def fill_ref(
        self, ref: str, value: str, *, user_id: Optional[str] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        raw = (ref or "").strip()
        if raw.isdigit() or (raw.startswith("e") and raw[1:].isdigit()):
            idx = int(raw[1:] if raw.startswith("e") else raw)
            return self.input_index(idx, value, user_id=uid)

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._ref_selector(sess, ref)
            try:
                await page.evaluate(
                    "(s) => { const el = document.querySelector(s); if (el) {"
                    " el.scrollIntoView({block:'center'});"
                    " el.style.outline='3px solid #0a84ff'; } }",
                    sel,
                )
                await page.wait_for_timeout(400)
            except Exception:
                pass
            try:
                rejected = await self._reject_if_not_fillable(
                    page, sel, action="fill"
                )
                if rejected is not None:
                    rejected["ref"] = ref
                    rejected["value"] = str(value)
                    return rejected
                await page.fill(sel, str(value), timeout=15000)
            finally:
                try:
                    await page.evaluate(
                        "(s) => { const el = document.querySelector(s);"
                        " if (el) el.style.outline=''; }",
                        sel,
                    )
                except Exception:
                    pass
                await self._clear_highlight(page)
            return {"action": "fill", "ref": ref, "value": str(value)}

        return self._run_action(uid, _do, observe_after=True)

    async def _select_on(self, page: Any, sel: str, value: str) -> list[str]:
        try:
            return list(await page.select_option(sel, str(value), timeout=10000))
        except Exception:
            return list(
                await page.select_option(sel, label=str(value), timeout=10000)
            )

    def select_ref(
        self, ref: str, value: str, *, user_id: Optional[str] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._ref_selector(sess, ref)
            selected = await self._select_on(page, sel, value)
            return {"action": "select", "ref": ref, "selected": selected}

        return self._run_action(uid, _do)

    def select_selector(
        self, selector: str, value: str, *, user_id: Optional[str] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        sel = (selector or "").strip()
        if not sel:
            raise ValueError("empty selector")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            selected = await self._select_on(page, sel, value)
            return {"action": "select", "selector": sel, "selected": selected}

        return self._run_action(uid, _do)
    def type_text(
        self,
        selector: str,
        text: str,
        *,
        user_id: Optional[str] = None,
        clear: bool = True,
    ) -> str:
        uid = self._lane_uid(user_id)
        sel = (selector or "").strip()
        if not sel:
            return "ERROR: empty selector"

        async def _op() -> str:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                rejected = await self._reject_if_not_fillable(
                    page, sel, action="type"
                )
                if rejected is not None:
                    return f"ERROR: {rejected['error']}"
                if clear:
                    await page.fill(sel, text or "", timeout=15000)
                else:
                    await page.type(sel, text or "", timeout=15000)
            except Exception as exc:
                if self._is_target_closed(exc):
                    raise
                if not (self._dialog_pending(page) or dialog_interrupted(exc)):
                    raise
            gate = await self._await_user_gate(page, sess)
            typed = f"typed into {sel}"
            gate_note = str(gate.get("note") or "").strip()
            if gate_note:
                typed += f" [{gate_note}]"
            return typed

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)
    def input_index(
        self,
        index: int,
        text: str,
        *,
        submit: bool = False,
        clear: bool = True,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid)
        if held:
            return held

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._index_selector(sess, int(index))
            await self._highlight_index(page, int(index))
            try:
                rejected = await self._reject_if_not_fillable(
                    page, sel, index=int(index), action="input"
                )
                if rejected is not None:
                    return rejected
                loc = page.locator(sel).first
                if clear:
                    try:
                        await loc.fill("")
                    except Exception:
                        pass
                await loc.fill(str(text), timeout=15000)
                if submit:
                    try:
                        await loc.press("Enter")
                    except Exception:
                        await page.keyboard.press("Enter")
            finally:
                await self._clear_highlight(page)
            return {
                "action": "input",
                "index": int(index),
                "text": str(text),
                "submit": bool(submit),
            }

        return self._run_action(uid, _do, observe_after=True)

