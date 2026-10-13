"""Browser sandbox mixin."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from ..runtime.browser_protocol import DomComponentHint, DomElementPayload, DomRect
from .browser_sandbox_worker import _WORKER
from .browser_sandbox_util import (
    HOST_KIND,
    is_shallow_site_rewind,
    urls_match,
    _USER_WAIT_TIMEOUT,
    dialog_interrupted,
    coerce_navigate_target,
    discover_cdp_url,
    _find_guest_page,
    loopback_url_candidates,
    _is_connection_refused,
    _SELECT_BOOTSTRAP,
    _fix_url_encoding,
)

class BrowserSandboxNavMixin:
    def navigate(
        self,
        url: str,
        *,
        user_id: Optional[str] = None,
        headless: bool = False,
        agent_owned: bool = False,
        workspace: Optional[Path] = None,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        try:
            target = coerce_navigate_target(url, workspace=workspace)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not target:
            raise ValueError(
                f"invalid url: {url!r} — browser_navigate needs a full http(s) URL "
                "(e.g. https://example.com), localhost:port, or a workspace HTML file "
                "(e.g. report.html). Do not pass javascript: or a script. "
                "Fill a form with browser_observe, browser_input, then browser_click. "
                "Do not pass screenshot filenames."
            )
        target = _fix_url_encoding(target)

        async def _op() -> dict[str, Any]:
            with self._lock:
                sess = self._sessions.get(uid)
            if not self._session_alive(sess):
                if sess:
                    await self._dispose_session(uid, sess)
                info = await self._ensure_on_worker(
                    uid, target, headless=headless, agent_owned=agent_owned
                )
                with self._lock:
                    current = self._sessions.get(uid)
                return self._apply_gate(current, info)
            # Refuse shallow same-host rewinds (results/article → site home).
            try:
                cur_url = str(getattr(sess.get("page"), "url", "") or sess.get("url") or "")
            except Exception:
                cur_url = str(sess.get("url") or "")
            if is_shallow_site_rewind(cur_url, target):
                info = self._public(sess)
                info["url"] = cur_url or info.get("url")
                info["blocked_rewind"] = True
                info["warning"] = (
                    f"refused navigate to {target}: you are already on a deeper page "
                    f"of the same site ({cur_url}). Going back to the homepage/search "
                    "home is a dead loop. Use browser_snapshot + browser_click on the "
                    "CURRENT page instead."
                )
                return info
            try:
                await self._navigate_unlocked(sess, target)
            except Exception as exc:
                if not self._is_target_closed(exc):
                    raise
                await self._dispose_session(uid, sess)
                info = await self._ensure_on_worker(
                    uid, target, headless=headless, agent_owned=agent_owned
                )
                with self._lock:
                    current = self._sessions.get(uid)
                return self._apply_gate(current, info)
            with self._lock:
                info = self._public(sess)
                # Anti-loop: re-navigating the SAME URL within 60s is a stuck
                # retry, not progress. Call it out so the model switches to
                # snapshot + click instead of bouncing back to the homepage.
                now = time.time()
                log = [e for e in sess.get("nav_log", []) if now - e[0] < 60.0]
                repeats = sum(1 for e in log if e[1] == target)
                log.append((now, target))
                sess["nav_log"] = log
                if repeats >= 1:
                    info["warning"] = (
                        f"you have navigated to {target} {repeats + 1} times in the "
                        "last minute. Re-navigating will NOT change anything — do NOT "
                        "go back to the search page/homepage. Use browser_snapshot on "
                        "the CURRENT page and browser_click the element you need."
                    )
                live = str(sess.get("url") or info.get("url") or target)
                if live:
                    self._last_url[uid] = live
            return self._apply_gate(sess, info)

        try:
            info = _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)
        except Exception as exc:
            # Desktop: let the UI/Electron loadURL the same target so the
            # in-app panel still opens even if Playwright CDP attach lags.
            self._remember_url(uid, target)
            return {
                "host": HOST_KIND,
                "url": target,
                "ready": False,
                "deferred": True,
                "note": str(exc),
            }
        self._remember_url(uid, str(info.get("url") or target))
        return info

    def screenshot_png(
        self,
        *,
        user_id: Optional[str] = None,
        full_page: bool = False,
        lane: Optional[str] = None,
    ) -> bytes:
        uid = self._lane_uid(user_id, lane)

        with self._lock:
            cached = self._last_shots.get(uid)
            last_at = float(self._shot_at.get(uid) or 0.0)
            sess = self._sessions.get(uid)
            min_gap = float(getattr(self, "_shot_min_interval", 1.6) or 1.6)
            # Live Electron BrowserView: CDP screenshots flash the visible page.
            if sess and sess.get("cdp"):
                min_gap = max(min_gap, 5.0)
            if cached is not None and (time.time() - last_at) < min_gap:
                return cached
            if self._shot_pending.get(uid):
                if cached is not None:
                    return cached
                raise RuntimeError("screenshot already in progress")
            cur = ""
            try:
                if sess and sess.get("page") is not None:
                    cur = str(getattr(sess["page"], "url", "") or "")
            except Exception:
                cur = ""
            if (not cur or cur == "about:blank") and cached is None:
                raise RuntimeError("browser is still on about:blank")
            self._shot_pending[uid] = True
        try:

            async def _op() -> bytes:
                with self._lock:
                    page = await self._alive_page(uid)
                try:
                    page_url = str(getattr(page, "url", "") or "")
                    if not page_url or page_url == "about:blank":
                        raise RuntimeError("browser is still on about:blank")
                    data = await page.screenshot(
                        full_page=full_page,
                        type="png",
                        timeout=20000,
                        animations="disabled",
                        caret="hide",
                    )
                except Exception:
                    cached = self._last_shots.get(uid)
                    if cached is not None:
                        return cached
                    raise
                with self._lock:
                    self._last_shots[uid] = data
                self._shot_at[uid] = time.time()
                return data

            return _WORKER.call(_op, timeout=8.0)
        finally:
            with self._lock:
                self._shot_pending[uid] = False

    def last_screenshot(self, *, user_id: Optional[str] = None, lane: Optional[str] = None) -> Optional[bytes]:
        """Last good screenshot for the user, even if the session is gone."""
        uid = self._lane_uid(user_id, lane)
        with self._lock:
            return self._last_shots.get(uid)

    def page_content(
        self,
        *,
        user_id: Optional[str] = None,
        max_chars: int = 12000,
        include_html: bool = False,
    ) -> dict[str, Any]:
        """Return a compact, agent-friendly snapshot of the current page.

        This intentionally exposes rendered text by default.  Full markup is
        optional and bounded so a single browser inspection cannot consume an
        entire agent context window.
        """
        uid = self._lane_uid(user_id)
        limit = max(500, min(int(max_chars or 12000), 50000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=15000)
            payload: dict[str, Any] = {
                "url": page.url,
                "title": title,
                "text": (text or "").strip()[:limit],
                "truncated": len((text or "").strip()) > limit,
            }
            if include_html:
                html = await page.content()
                payload["html"] = html[:limit]
                payload["html_truncated"] = len(html) > limit
            return payload

        return _WORKER.call(_op, timeout=30.0)

    def console_logs(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        uid = self._lane_uid(user_id)
        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return []
            logs = list(sess.get("console") or [])
            return logs[-max(1, min(limit, 200)) :]

    def pick_element(
        self,
        *,
        user_id: Optional[str] = None,
        timeout_ms: int = 60000,
        with_screenshot: bool = True,
    ) -> Optional[DomElementPayload]:
        uid = self._lane_uid(user_id)
        wait_s = max(5.0, (int(timeout_ms) / 1000.0) + 15.0)

        async def _op() -> Optional[DomElementPayload]:
            with self._lock:
                page = await self._alive_page(uid)
            await page.evaluate(_SELECT_BOOTSTRAP)
            raw = await page.evaluate(
                "(timeoutMs) => window.__sidekickSelectArm(timeoutMs)",
                int(timeout_ms),
            )
            if not raw or not isinstance(raw, dict):
                return None
            payload = DomElementPayload.model_validate(raw)
            if with_screenshot:
                try:
                    handle = None
                    if payload.xpath:
                        try:
                            handle = await page.query_selector(f"xpath={payload.xpath}")
                        except Exception:
                            handle = None
                    if handle is None and payload.css_path:
                        try:
                            handle = await page.query_selector(payload.css_path)
                        except Exception:
                            handle = None
                    if handle is not None:
                        png = await handle.screenshot(type="png")
                        payload.screenshot_base64 = base64.b64encode(png).decode("ascii")
                except Exception:
                    pass
            if payload.component is None:
                payload.component = DomComponentHint()
            if not isinstance(payload.rect, DomRect):
                payload.rect = DomRect.model_validate(payload.rect or {})
            return payload

        return _WORKER.call(_op, timeout=wait_s)

    def cancel_pick(self, *, user_id: Optional[str] = None) -> None:
        uid = self._lane_uid(user_id)

        async def _op() -> None:
            with self._lock:
                sess = self._sessions.get(uid)
                page = sess.get("page") if sess else None
            if not page:
                return
            try:
                await page.evaluate(
                    "() => { if (window.__sidekickSelectCancel) window.__sidekickSelectCancel(); }"
                )
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

    async def _sync_desktop_url(self, page: Any, url: str) -> None:
        """Bring the shared Electron BrowserView to ``url``.

        Prefer waiting briefly for a concurrent Electron ``loadURL`` (UI path).
        If the guest is still on about:blank / another page, drive navigation
        over CDP ourselves — otherwise agent + preview stay blank forever.
        """
        target = (url or "").strip()
        if not target or target == "about:blank":
            return
        current = getattr(page, "url", "") or ""
        if urls_match(current, target):
            return
        # Never rewind a results/article page to the site homepage over CDP
        # because a stale navigate target (or queued UI loadURL sibling) asked
        # for the home URL after the guest had already moved on.
        if is_shallow_site_rewind(current, target):
            return
        try:
            await page.wait_for_url(
                lambda u: urls_match(u, target),
                timeout=2500,
            )
            return
        except Exception:
            pass
        if urls_match(getattr(page, "url", "") or "", target):
            return
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            if (
                self._is_target_closed(exc)
                or dialog_interrupted(exc)
                or self._dialog_pending(page)
            ):
                raise
            try:
                await page.wait_for_url(
                    lambda u: urls_match(u, target),
                    timeout=8000,
                )
            except Exception:
                pass

    async def _goto_page(self, page: Any, url: str) -> None:
        last_exc: Optional[BaseException] = None
        if urls_match(getattr(page, "url", "") or "", url):
            return
        for cand in loopback_url_candidates(url):
            try:
                await page.goto(cand, wait_until="domcontentloaded", timeout=60000)
                return
            except Exception as exc:
                last_exc = exc
                if (
                    self._is_target_closed(exc)
                    or dialog_interrupted(exc)
                    or self._dialog_pending(page)
                ):
                    raise
                if self._is_nav_aborted(exc) and urls_match(getattr(page, "url", "") or "", url):
                    return
                if _is_connection_refused(exc):
                    continue
                try:
                    await page.goto(cand, wait_until="commit", timeout=60000)
                    return
                except Exception as exc2:
                    last_exc = exc2
                    if dialog_interrupted(exc2) or self._dialog_pending(page):
                        raise
                    if self._is_target_closed(exc2) or not _is_connection_refused(exc2):
                        if self._is_nav_aborted(exc2) and urls_match(
                            getattr(page, "url", "") or "", url
                        ):
                            return
                        raise
        if last_exc:
            raise last_exc

    async def _navigate_unlocked(self, sess: dict[str, Any], url: str) -> None:
        page = sess["page"]
        if not self._page_alive(page):
            raise RuntimeError("Target page, context or browser has been closed")
        await self._arm_user_dialogs(page)
        try:
            if sess.get("cdp"):
                await self._sync_desktop_url(page, url)
            else:
                await self._goto_page(page, url)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
        except Exception as exc:
            if self._is_target_closed(exc):
                raise
            if not (self._dialog_pending(page) or dialog_interrupted(exc)):
                raise
        gate = await self._await_user_gate(page, sess)
        if gate.get("dialogs") or gate.get("needs_human") or gate.get("note"):
            sess["_last_gate"] = gate
        prev = str(sess.get("url") or "")
        landed = str(page.url or url or "")
        if prev and prev != "about:blank" and prev != landed:
            hist = list(sess.get("history") or [])
            if not hist or hist[-1] != prev:
                hist.append(prev)
            sess["history"] = hist[-30:]
        sess["url"] = landed
        sess["last_used"] = time.time()
        # Element refs from a previous page are meaningless after navigation.
        with self._lock:
            sess["refs"] = {}
