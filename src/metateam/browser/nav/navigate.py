"""BrowserSandbox mixin: navigate."""
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
from .urls import (
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
from ..actions import element_is_fillable, non_fillable_input_error

class BrowserNavigateMixin:

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
        if url_has_chromium_unsafe_port(target):
            port = url_port(target)
            raise ValueError(
                f"Chromium blocks port {port} (net::ERR_UNSAFE_PORT). "
                f"Pick another port (e.g. 5173, 8080, 3000) and call "
                f"browser_navigate again — do not use browser_go_back to that URL."
            )

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

    def go_back(self, *, user_id: Optional[str] = None, lane: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id, lane)

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            before = str(getattr(page, "url", "") or "")
            nav_err: BaseException | None = None
            # Prefer commit so a blocked/unsafe history entry fails fast
            # instead of hanging on domcontentloaded (ERR_UNSAFE_PORT).
            try:
                await page.go_back(timeout=20000, wait_until="commit")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
            except Exception as exc:
                nav_err = exc
            after = str(getattr(page, "url", "") or "")
            if after == before or _is_history_nav_error(nav_err):
                with self._lock:
                    hist = list(sess.get("history") or [])
                landed = False
                skipped_unsafe: list[str] = []
                while hist:
                    cand = str(hist.pop() or "")
                    if not cand or cand == before:
                        continue
                    if url_has_chromium_unsafe_port(cand):
                        skipped_unsafe.append(cand)
                        continue
                    try:
                        await self._goto_page(page, cand)
                        after = str(getattr(page, "url", "") or cand)
                        landed = bool(after and after != before)
                        if landed:
                            break
                    except Exception as exc2:
                        nav_err = exc2
                        if _is_unsafe_port_error(exc2) or url_has_chromium_unsafe_port(
                            cand
                        ):
                            skipped_unsafe.append(cand)
                            continue
                        if _is_history_nav_error(exc2):
                            continue
                        raise
                with self._lock:
                    sess["history"] = hist
                if not landed:
                    hint = (
                        "Previous history entry uses a Chromium-blocked port "
                        "(net::ERR_UNSAFE_PORT) or failed to load. Prefer "
                        "browser_navigate to a safe URL (e.g. http://127.0.0.1:5173); "
                        "avoid ports Chromium refuses (6000, 6665-6669, …)."
                    )
                    err = str(nav_err or "go_back did not change the page")
                    with self._lock:
                        sess["url"] = after or before
                    return {
                        "url": after or before,
                        "ok": False,
                        "from": before,
                        "error": err,
                        "hint": hint,
                        "skipped_unsafe": skipped_unsafe[:6],
                    }
            with self._lock:
                if after != before:
                    hist = list(sess.get("history") or [])
                    if hist and hist[-1] == after:
                        hist.pop()
                    sess["history"] = hist
                sess["url"] = after
            out: dict[str, Any] = {
                "url": after,
                "ok": bool(after and after != before),
                "from": before,
            }
            if nav_err and out["ok"]:
                out["recovered_from"] = str(nav_err)
            return out

        return _WORKER.call(_op, timeout=30.0)

    def go_forward(self, *, user_id: Optional[str] = None, lane: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id, lane)

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            before = str(getattr(page, "url", "") or "")
            nav_err: BaseException | None = None
            try:
                await page.go_forward(timeout=20000, wait_until="commit")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
            except Exception as exc:
                nav_err = exc
            after = str(getattr(page, "url", "") or "")
            ok = bool(after and after != before and not _is_history_nav_error(nav_err))
            with self._lock:
                sess["url"] = after or before
            out: dict[str, Any] = {
                "url": after or before,
                "ok": ok,
                "from": before,
            }
            if not ok:
                out["error"] = str(nav_err or "go_forward did not change the page")
                if _is_unsafe_port_error(nav_err):
                    out["hint"] = (
                        "Forward entry uses a Chromium-blocked port "
                        "(net::ERR_UNSAFE_PORT). Use browser_navigate to a safe URL."
                    )
            return out

        return _WORKER.call(_op, timeout=30.0)

    @staticmethod
    def _context_pages(sess: dict[str, Any]) -> list[Any]:
        cur = sess.get("page")
        context = getattr(cur, "context", None)
        pages = list(getattr(context, "pages", []) or []) if context else []
        return [p for p in pages if BrowserSandbox._page_alive(p)]

    def list_tabs(self, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)

        async def _op() -> dict[str, Any]:
            with self._lock:
                await self._alive_page(uid)
                sess = self._sessions[uid]
                cur = sess.get("page")
            tabs = []
            for i, p in enumerate(self._context_pages(sess)):
                try:
                    title = (await p.title()) or ""
                except Exception:
                    title = ""
                tabs.append(
                    {
                        "index": i,
                        "url": p.url,
                        "title": title[:80],
                        "current": p is cur,
                    }
                )
            return {"count": len(tabs), "tabs": tabs}

        return _WORKER.call(_op, timeout=30.0)

    def switch_tab(self, index: int, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)

        async def _op() -> dict[str, Any]:
            with self._lock:
                await self._alive_page(uid)
                sess = self._sessions[uid]
            pages = self._context_pages(sess)
            i = int(index)
            if i < 0 or i >= len(pages):
                raise ValueError(
                    f"tab index {i} out of range (0-{len(pages) - 1}) — "
                    "call browser_tabs(action='list') first"
                )
            target = pages[i]
            try:
                await target.bring_to_front()
            except Exception:
                pass
            with self._lock:
                sess["page"] = target
                sess["url"] = target.url
                sess["last_used"] = time.time()
                sess.pop("new_tab", False)
            return {"switched": True, "index": i, "url": target.url}

        return _WORKER.call(_op, timeout=30.0)

    def close_tab(self, index: int, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)

        async def _op() -> dict[str, Any]:
            with self._lock:
                await self._alive_page(uid)
                sess = self._sessions[uid]
                cur = sess.get("page")
            pages = self._context_pages(sess)
            i = int(index)
            if i < 0 or i >= len(pages):
                raise ValueError(
                    f"tab index {i} out of range (0-{len(pages) - 1})"
                )
            target = pages[i]
            remaining = [p for j, p in enumerate(pages) if j != i]
            await target.close()
            with self._lock:
                if cur is target and remaining:
                    sess["page"] = remaining[0]
                    sess["url"] = remaining[0].url
                elif cur is not target:
                    sess["url"] = getattr(cur, "url", sess.get("url") or "")
                sess.pop("new_tab", False)
            nxt = sess.get("page")
            return {"closed": True, "index": i, "url": getattr(nxt, "url", "") or ""}

        return _WORKER.call(_op, timeout=30.0)

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

    @staticmethod
    def _is_nav_aborted(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "err_aborted" in msg or "(-3)" in msg or "net::err_aborted" in msg
