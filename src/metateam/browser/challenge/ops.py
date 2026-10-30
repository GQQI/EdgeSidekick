"""BrowserSandbox mixin: challenge_ops."""
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
from .. import observe as _bobs
from ..nav.urls import (
    coerce_navigate_target,
    is_chromium_unsafe_port,
    is_shallow_site_rewind,
    lookup_screenshot_page_url,
    loopback_url_candidates,
    resolve_browser_target,
    sanitize_browser_url,
    script_navigation_error,
    url_has_chromium_unsafe_port,
    url_port,
    urls_match,
    _fix_url_encoding,
    _is_connection_refused,
    _is_history_nav_error,
    _is_unsafe_port_error,
    _looks_like_asset_filename,
)
from .core import challenge_js_source
from ..actions import element_is_fillable, non_fillable_input_error

class BrowserChallengeMixin:

    async def _probe_challenge(self, page: Any) -> dict[str, Any]:
        """Detect captcha / security-check interstitial on the current page."""
        if page is None:
            return {"challenged": False}
        try:
            raw = await page.evaluate(challenge_js_source())
        except Exception:
            return {"challenged": False}
        if not isinstance(raw, dict):
            return {"challenged": False}
        out = dict(raw)
        out["challenged"] = bool(out.get("challenged"))
        return out

    def detect_challenge(self, *, user_id: Optional[str] = None) -> dict[str, Any]:
        """Sync wrapper: is the active page a human-verification gate?"""
        uid = self._lane_uid(user_id)

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            info = await self._probe_challenge(page)
            try:
                info["url"] = str(getattr(page, "url", "") or sess.get("url") or "")
            except Exception:
                info["url"] = str(sess.get("url") or "")
            return info

        return _WORKER.call(_op, timeout=20.0)

    def wait_for_human(
        self,
        *,
        user_id: Optional[str] = None,
        timeout_ms: int = 180000,
        poll_ms: int = 1500,
    ) -> dict[str, Any]:
        """Poll until a captcha/security gate clears, or timeout.

        Does not solve the challenge — the human must complete it in the
        visible browser. Returns needs_human=False when the page looks clear.
        """
        uid = self._lane_uid(user_id)
        timeout_ms = max(3000, min(int(timeout_ms or 180000), 600000))
        poll_ms = max(400, min(int(poll_ms or 1500), 10000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            deadline = time.time() + (timeout_ms / 1000.0)
            last: dict[str, Any] = {"challenged": False}
            while True:
                last = await self._probe_challenge(page)
                try:
                    cur_url = str(getattr(page, "url", "") or "")
                except Exception:
                    cur_url = str(sess.get("url") or "")
                last["url"] = cur_url
                if cur_url:
                    with self._lock:
                        sess["url"] = cur_url
                        self._last_url[uid] = cur_url
                if not last.get("challenged"):
                    last["needs_human"] = False
                    last["status"] = "cleared"
                    last["note"] = "验证已通过或未检测到验证页 — 可继续操作"
                    return last
                if time.time() >= deadline:
                    last["needs_human"] = True
                    last["status"] = "timeout"
                    last["note"] = (
                        "等待用户完成验证超时。请再给用户一些时间，或重新调用 "
                        "browser_wait_for_user。"
                    )
                    return last
                await asyncio.sleep(poll_ms / 1000.0)

        return _WORKER.call(_op, timeout=max(30.0, timeout_ms / 1000.0 + 15.0))
