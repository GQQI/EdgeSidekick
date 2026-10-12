"""CDP-backed browser sandbox (Playwright Chromium).

Host decision: cdp_playwright — see docs/browser-sandbox.md.

Playwright runs on a dedicated worker thread with its own asyncio event loop
and the Async API (one Playwright instance for the process). Callers stay sync
via `_WORKER.call`; never touch Page/Browser from FastAPI/agent threads.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote, urlparse, urlunparse

from ..runtime.browser_protocol import (
    PROTOCOL_VERSION,
    STYLE_KEYS,
    DomComponentHint,
    DomElementPayload,
    DomRect,
)
from .tenant_context import get_user_id
from . import browser_observe as _bobs
from .browser_sandbox_worker import _WORKER
from .browser_sandbox_actions import BrowserSandboxActionsMixin
from .browser_sandbox_page import BrowserSandboxPageMixin
from .browser_sandbox_nav import BrowserSandboxNavMixin
from .browser_sandbox_dialogs import BrowserSandboxDialogsMixin
from .browser_sandbox_util import (
    _URL_EXTRACT_RE,
    sanitize_browser_url,
    _workspace_root,
    resolve_browser_target,
    _IMAGE_SUFFIXES,
    _BARE_HOST_RE,
    _LOCALHOST_RE,
    is_shallow_site_rewind,
    _CHALLENGE_CUES,
    _CHALLENGE_JS,
    challenge_js_source,
    urls_match,
    _looks_like_asset_filename,
    lookup_screenshot_page_url,
    _SCRIPT_URL_RE,
    _HUMAN_CONFIRM_S,
    _USER_WAIT_TIMEOUT,
    _DIALOG_HOOK_JS,
    script_navigation_error,
    dialog_interrupted,
    dialog_result_note,
    release_unblocked_dialogs,
    merge_dialog_log,
    public_dialog,
    coerce_navigate_target,
    _cdp_cached,
    discover_cdp_url,
    _is_workbench_url,
    _find_guest_page,
    loopback_url_candidates,
    _is_connection_refused,
    _SELECT_BOOTSTRAP,
    _LIST_ELEMENTS_JS,
    _PCT_INCOMPLETE_RE,
    _fix_url_encoding,
    _SNAPSHOT_JS,
)

HOST_KIND = "cdp_playwright"

# True inside 会话版 (social) chat turns. The agent's browser there is
# headless and auto-closed at turn end; the workbench keeps the classic
# visible-window behaviour.
SOCIAL_BROWSING: ContextVar[bool] = ContextVar("sidekick_social_browsing", default=False)

# A standalone browser window auto-closes after this much inactivity, so the
# agent never leaves one open behind it.
SESSION_IDLE_SECONDS = 300.0
_REAP_INTERVAL_SECONDS = 60.0

class BrowserSandbox(BrowserSandboxActionsMixin, BrowserSandboxPageMixin, BrowserSandboxNavMixin, BrowserSandboxDialogsMixin):
    """Per-user Chromium session; all CDP ops marshalled onto `_WORKER`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._cdp_browser: Any = None
        self._last_url: dict[str, str] = {}
        # Last good screenshot per user (survives session close) so the chat
        # bubble / fullscreen preview keeps showing the final frame instead
        # of blanking out, and in-flight dedup so 2s polling cannot pile up
        # screenshot jobs on the single worker queue.
        self._last_shots: dict[str, bytes] = {}
        self._shot_pending: dict[str, bool] = {}
        self._shot_at: dict[str, float] = {}
        # Floor between real captures — overlapping UI pollers must not burst.
        self._shot_min_interval = 1.6
        # Launch flags of the most recent session per user — a dead session can
        # then be relaunched the way it was originally created.
        self._last_flags: dict[str, dict[str, bool]] = {}
        self._start_reaper()

    def _start_reaper(self) -> None:
        """Auto-close standalone browser windows nobody used for a while.

        Without this the agent leaves a Chromium window open after finishing
        (会话版 users then close it by hand and the next tool call errors with
        "target closed"). Sessions attached to the desktop Browser panel (cdp)
        are skipped — that panel is a persistent app component, not ours.
        """

        def _loop() -> None:
            while True:
                time.sleep(_REAP_INTERVAL_SECONDS)
                try:
                    self._reap_idle()
                except Exception:
                    pass

        threading.Thread(target=_loop, name="sidekick-browser-reaper", daemon=True).start()

    def _reap_idle(self) -> None:
        now = time.time()
        stale: list[str] = []
        with self._lock:
            for uid, sess in self._sessions.items():
                if sess.get("cdp"):
                    continue
                last = float(sess.get("last_used") or sess.get("started_at") or now)
                if now - last > SESSION_IDLE_SECONDS:
                    stale.append(uid)
        for uid in stale:
            try:
                self.close(user_id=uid)
            except Exception:
                pass

    def _lane_uid(self, user_id: Optional[str] = None, lane: Optional[str] = None) -> str:
        """Separate 会话版 and workbench browsers. They must not share a page."""
        base = (user_id or get_user_id() or "default").strip()
        if base.endswith(":social") or base.endswith(":desk"):
            return base
        if lane not in ("social", "desk"):
            lane = "social" if SOCIAL_BROWSING.get() else "desk"
        return f"{base}:{lane}"

    def last_url(self, user_id: Optional[str] = None) -> str:
        uid = self._lane_uid(user_id)
        with self._lock:
            sess = self._sessions.get(uid)
            if sess and sess.get("url"):
                return str(sess.get("url") or "")
            return self._last_url.get(uid, "")

    def _remember_url(self, uid: str, url: str) -> None:
        text = (url or "").strip()
        if not text:
            return
        with self._lock:
            self._last_url[uid] = text
            sess = self._sessions.get(uid)
            if sess is not None:
                sess["url"] = text

    def host_kind(self) -> str:
        return HOST_KIND

    def playwright_available(self) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
            from playwright.async_api import async_playwright  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return False, (
                "Playwright not installed. Run: pip install playwright && playwright install chromium "
                f"({exc})"
            )
        return True, ""

    def status(self, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        ok, err = self.playwright_available()
        with self._lock:
            sess = self._sessions.get(uid)
            info = None
            if sess and sess.get("page"):
                info = {
                    "url": sess.get("url") or "about:blank",
                    "started_at": sess.get("started_at"),
                    "ready": True,
                    "host": HOST_KIND,
                }
        return {
            "host": HOST_KIND,
            "available": ok,
            "message": err,
            "session": info,
        }

    def ensure_session(
        self,
        *,
        user_id: Optional[str] = None,
        url: str = "",
        headless: bool = False,
    ) -> dict[str, Any]:
        ok, err = self.playwright_available()
        if not ok:
            raise RuntimeError(err)
        uid = self._lane_uid(user_id)
        target = ""
        if (url or "").strip():
            try:
                target = coerce_navigate_target(url)
            except ValueError:
                target = resolve_browser_target(url)

        async def _op() -> dict[str, Any]:
            with self._lock:
                sess = self._sessions.get(uid)
            if self._session_alive(sess):
                if target:
                    try:
                        await self._navigate_unlocked(sess, target)
                    except Exception as exc:
                        if not self._is_target_closed(exc):
                            raise
                        await self._dispose_session(uid, sess)
                        info = await self._ensure_on_worker(uid, target, headless=headless)
                        with self._lock:
                            current = self._sessions.get(uid)
                        return self._apply_gate(current, info)
                with self._lock:
                    info = self._public(sess)
                return self._apply_gate(sess, info)
            if sess:
                await self._dispose_session(uid, sess)
            info = await self._ensure_on_worker(uid, target, headless=headless)
            with self._lock:
                current = self._sessions.get(uid)
            return self._apply_gate(current, info)

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)

    def close(self, user_id: Optional[str] = None) -> None:
        """Drop the user's sandbox session.

        Always safe to call from UI boot (DELETE /api/browser/session). Session
        bookkeeping is synchronous so a wedged Playwright worker cannot 500 the
        API. Only agent-launched (non-CDP) browsers need a worker round-trip to
        close Chromium, and that path is best-effort.
        """
        uid = self._lane_uid(user_id)
        with self._lock:
            sess = self._sessions.pop(uid, None)
        if not sess:
            return
        if sess.get("cdp"):
            # Shared Electron BrowserView — keep the panel alive.
            return

        async def _op() -> None:
            for key in ("context", "browser"):
                obj = sess.get(key)
                if obj is None:
                    continue
                try:
                    await obj.close()
                except Exception:
                    pass

        try:
            _WORKER.call(_op, timeout=5.0)
        except Exception:
            pass

    def close_agent_owned(self, user_id: Optional[str] = None, lane: Optional[str] = None) -> None:
        """Close the browser the agent launched itself (end of a chat turn).

        Agent browsing is headless and mirrored into the chat as a live
        screenshot card, so nothing stays on screen once the turn is over.
        Sessions the user opened from the Browser panel are left untouched.
        """
        uid = self._lane_uid(user_id, lane)
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess or not sess.get("agent_owned") or sess.get("cdp"):
                return
            self._sessions.pop(uid, None)

        async def _op() -> None:
            for key in ("context", "browser"):
                obj = sess.get(key)
                if obj is None:
                    continue
                try:
                    await obj.close()
                except Exception:
                    pass

        try:
            _WORKER.call(_op, timeout=10.0)
        except Exception:
            pass




# ------------------------------------------------------------------
# Index-based observation / actions (browser-use style loop)
# ------------------------------------------------------------------

SANDBOX = BrowserSandbox()

# Re-exports for callers / tests (import path unchanged).
