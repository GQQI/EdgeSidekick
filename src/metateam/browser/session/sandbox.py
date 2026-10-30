"""BrowserSandbox facade — session lifecycle + CDP attach; ops via mixins."""
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
from .. import observe as _bobs
from ..actions import BrowserActionsMixin, element_is_fillable, non_fillable_input_error
from .cdp import discover_cdp_url, _find_guest_page, _is_workbench_url
from ..challenge import challenge_js_source
from ..challenge.ops import BrowserChallengeMixin
from .constants import (
    HOST_KIND,
    SESSION_IDLE_SECONDS,
    SOCIAL_BROWSING,
    _HUMAN_CONFIRM_S,
    _REAP_INTERVAL_SECONDS,
    _USER_WAIT_TIMEOUT,
)
from ..dialog.dialog_ops import BrowserDialogMixin, _DIALOG_HOOK_JS
from ..dialog.dialogs import (
    dialog_interrupted,
    dialog_result_note,
    merge_dialog_log,
    public_dialog,
    release_unblocked_dialogs,
)
from ..nav.navigate import BrowserNavigateMixin
from ..observe.ops import BrowserObserveMixin
from .thread import _WORKER
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

# Re-export constants for legacy `from metateam.browser.sandbox import HOST_KIND` etc.
__all_constants__ = (
    "HOST_KIND",
    "SOCIAL_BROWSING",
    "SESSION_IDLE_SECONDS",
    "_REAP_INTERVAL_SECONDS",
    "_HUMAN_CONFIRM_S",
    "_USER_WAIT_TIMEOUT",
)

_cdp_cached = ""

_PCT_INCOMPLETE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")

class BrowserSandbox(
    BrowserNavigateMixin,
    BrowserActionsMixin,
    BrowserObserveMixin,
    BrowserChallengeMixin,
    BrowserDialogMixin,
):

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

    async def _ensure_on_worker(
        self,
        uid: str,
        url: str,
        *,
        headless: bool,
        agent_owned: bool = False,
    ) -> dict[str, Any]:
        """Create session on the Playwright worker loop (shared async Playwright)."""
        with self._lock:
            existing = self._sessions.get(uid)
        if self._session_alive(existing):
            if url:
                try:
                    await self._navigate_unlocked(existing, url)
                except Exception as exc:
                    if not self._is_target_closed(exc):
                        raise
                    await self._dispose_session(uid, existing)
                    existing = None
                else:
                    return self._public(existing)
            else:
                return self._public(existing)
        elif existing:
            agent_owned = agent_owned or bool(existing.get("agent_owned"))
            headless = headless or bool(existing.get("headless"))
            await self._dispose_session(uid, existing)

        pw = _WORKER.playwright
        if pw is None:
            raise RuntimeError("Playwright worker not ready")
        cdp = discover_cdp_url()
        # 会话版 and the workbench drive the same in-app browser.
        if cdp and not headless and not agent_owned:
            # One sandbox only: discard a prior headless Chromium so preview
            # and the fullscreen BrowserView share the same page.
            with self._lock:
                existing2 = self._sessions.get(uid)
            if existing2 and not existing2.get("cdp"):
                await self._dispose_session(uid, existing2)
            return await self._attach_desktop(uid, url)

        launch_kwargs: dict[str, Any] = {}
        channel = os.environ.get("SIDEKICK_BROWSER_CHANNEL", "").strip()
        if channel:
            launch_kwargs["channel"] = channel
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=WebRTC,WebRtcHideLocalIpsWithMdns",
                "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--disable-dev-shm-usage",
                "--no-default-browser-check",
                "--no-first-run",
            ],
            **launch_kwargs,
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            color_scheme="light",
        )
        await context.add_init_script(
            "try { Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined}); } catch (_) {}"
        )
        page = await context.new_page()
        self._hook_page_events(uid, page)
        go = url or "about:blank"
        if go != "about:blank":
            await self._goto_page(page, go)
        sess = {
            "user_id": uid,
            "browser": browser,
            "context": context,
            "page": page,
            "url": page.url,
            "started_at": time.time(),
            "last_used": time.time(),
            "headless": headless,
            "agent_owned": agent_owned,
            "console": [],
            "network": [],
            "refs": {},
            "host": HOST_KIND,
        }
        with self._lock:
            self._last_flags[uid] = {"headless": headless, "agent_owned": agent_owned}

        def _gone(_=None) -> None:
            with self._lock:
                if self._sessions.get(uid) is sess:
                    self._sessions.pop(uid, None)

        try:
            page.on("close", _gone)
            browser.on("disconnected", _gone)
        except Exception:
            pass

        def _on_ctx_page(new_page: Any) -> None:
            # target=_blank links / window.open spawn a new tab — follow the
            # user's focus there so post-click tools see the opened page.
            asyncio.ensure_future(self._adopt_popup(uid, sess, new_page))

        try:
            context.on("page", _on_ctx_page)
        except Exception:
            pass
        with self._lock:
            self._sessions[uid] = sess
            return self._public(sess)

    async def _attach_desktop(
        self, uid: str, url: str, *, restore: bool = False
    ) -> dict[str, Any]:
        """Drive the Electron BrowserView over CDP — no extra Chromium window."""
        pw = _WORKER.playwright
        cdp = discover_cdp_url()
        if pw is None or not cdp:
            raise RuntimeError("desktop CDP endpoint is not available")
        browser = self._cdp_browser
        if browser is None or not self._cdp_connected(browser):
            # A stale handle (panel restarted, old link died) must not be
            # reused forever — reconnect to the current CDP endpoint.
            try:
                browser = await pw.chromium.connect_over_cdp(cdp)
            except Exception as exc:
                raise RuntimeError(
                    f"cannot attach to the in-app browser panel ({exc}). "
                    "Open the Browser sidebar and retry."
                ) from exc
            self._cdp_browser = browser
        page = await _find_guest_page(browser)
        if page is None:
            raise RuntimeError(
                "in-app browser panel not ready. Open the Browser sidebar once and retry."
            )
        try:
            self._hook_page_events(uid, page)
        except Exception:
            pass
        go = url or ""
        if restore:
            # Reconnect after a dropped CDP link: the guest view still shows
            # the page the task was working on. Aiming it back at last_url
            # would rewind a search-results page to the homepage mid-task —
            # the current page IS the truth, adopt it instead.
            try:
                cur = (page.url or "").strip()
            except Exception:
                cur = ""
            if cur and cur not in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/"):
                go = cur
                self._last_url[uid] = cur
        if go and go != "about:blank":
            await self._sync_desktop_url(page, go)
        sess = {
            "user_id": uid,
            "browser": browser,
            "context": page.context,
            "page": page,
            "cdp": True,
            "url": page.url,
            "started_at": time.time(),
            "last_used": time.time(),
            "console": [],
            "network": [],
            "refs": {},
            "host": HOST_KIND,
        }

        def _gone(_=None) -> None:
            with self._lock:
                if self._sessions.get(uid) is sess:
                    self._sessions.pop(uid, None)

        try:
            page.on("close", _gone)
        except Exception:
            pass

        def _on_ctx_page(new_page: Any) -> None:
            # Electron routes window.open back into the same view, so this
            # rarely fires there — but a generic CDP browser does open new
            # tabs, and the session must follow them.
            asyncio.ensure_future(self._adopt_popup(uid, sess, new_page))

        try:
            ctx = getattr(page, "context", None)
            if ctx is not None:
                ctx.on("page", _on_ctx_page)
        except Exception:
            pass
        with self._lock:
            self._sessions[uid] = sess
            return self._public(sess)

    async def _alive_page(self, uid: str) -> Any:
        with self._lock:
            sess = self._sessions.get(uid)
            page = sess.get("page") if sess else None
        if self._page_alive(page):
            if sess is not None:
                sess["last_used"] = time.time()
            return page
        if discover_cdp_url():
            await self._attach_desktop(uid, self._last_url.get(uid, ""), restore=True)
            with self._lock:
                page = (self._sessions.get(uid) or {}).get("page")
            if self._page_alive(page):
                return page
        # The user (or the idle reaper) closed the window mid-task. Relaunch
        # instead of erroring — the agent then sees its page again instead of
        # reporting "浏览器有问题".
        # Prefer the session's last known page URL over a stale navigate() target
        # so a search-results page is not rewound to the site homepage.
        with self._lock:
            sess_url = ""
            dead = self._sessions.get(uid)
            if dead:
                sess_url = str(dead.get("url") or "")
            last = sess_url or self._last_url.get(uid, "")
            if last:
                self._last_url[uid] = last
        if (last or "").strip():
            flags = self._last_flags.get(uid) or {}
            try:
                await self._ensure_on_worker(
                    uid,
                    last,
                    headless=bool(flags.get("headless")),
                    agent_owned=bool(flags.get("agent_owned")),
                )
                with self._lock:
                    page = (self._sessions.get(uid) or {}).get("page")
                if self._page_alive(page):
                    return page
            except Exception:
                pass
        raise RuntimeError(
            "browser session not started — open a URL in the Browser panel first "
            "(or Ctrl+click a link → Open in sandbox)"
        )

    def _page_unlocked(self, uid: str) -> Any:
        sess = self._sessions.get(uid)
        page = sess.get("page") if sess else None
        if self._page_alive(page):
            return page
        raise RuntimeError(
            "browser session not started — open a URL in the Browser panel first "
            "(or Ctrl+click a link → Open in sandbox)"
        )

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

SANDBOX = BrowserSandbox()
