"""BrowserSandbox action methods (click/fill/type/nav helpers)."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from . import browser_observe as _bobs
from .browser_sandbox_dialogs import BrowserSandboxDialogsMixin
from .browser_sandbox_worker import _WORKER
from .browser_sandbox_util import (
    _USER_WAIT_TIMEOUT,
    challenge_js_source,
    dialog_interrupted,
    is_shallow_site_rewind,
    urls_match,
)


class BrowserSandboxActionsMixin:
    def click_selector(self, selector: str, *, user_id: Optional[str] = None) -> str:
        uid = self._lane_uid(user_id)
        sel = (selector or "").strip()
        if not sel:
            return "ERROR: empty selector"

        async def _op() -> str:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                await self._click_on(page, sel, sess=sess)
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
            await asyncio.sleep(0.4)
            with self._lock:
                cur = sess.get("page") or page
                switched = cur is not page
                try:
                    cur_url = str(getattr(cur, "url", "") or "")
                except Exception:
                    cur_url = str(sess.get("url") or "")
                sess["url"] = cur_url
                sess["last_used"] = time.time()
                sess.pop("new_tab", False)
                if cur_url:
                    self._last_url[uid] = cur_url
            note = (
                " [new tab opened — now viewing it; re-run browser_snapshot for fresh refs]"
                if switched
                else ""
            )
            gate_note = str(gate.get("note") or "").strip()
            if gate_note:
                note += f" [{gate_note}]"
            return f"clicked {sel} @ {cur_url}{note}"

        return _WORKER.call(_op, timeout=_USER_WAIT_TIMEOUT)

    # ---- blind-model toolkit: snapshot refs + post-action diagnostics ----

    @staticmethod
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

    def snapshot_page(
        self, *, user_id: Optional[str] = None, limit: int = 120
    ) -> dict[str, Any]:
        """Observe interactive elements (index list). Name kept for tools."""
        return self.observe_page(user_id=user_id, limit=limit)

    @staticmethod
    def _is_playwright_timeout(exc: BaseException) -> bool:
        if type(exc).__name__ == "TimeoutError":
            return True
        text = str(exc).lower()
        return "timeout" in text and "exceeded" in text

    async def _click_on(
        self,
        page: Any,
        sel: str,
        *,
        sess: Optional[dict[str, Any]] = None,
        timeout_ms: int = 15000,
    ) -> None:
        """Click with force, DOM fallbacks, popup/navigation follow, href rescue.

        A detached Electron BrowserView produces no frames, so Playwright's
        actionability check ("visible, enabled and stable") never settles and
        a plain click times out even though evaluate() works. CDP input
        dispatch does not need frames — retry with force=True there.

        Some pages hide the real submit control (display:none), so even a
        forced click lands on nothing. Final fallback: a DOM-level el.click().

        Search-result links often use target=_blank or JS wrappers. After the
        click we wait briefly for a popup or same-tab URL change; if neither
        happens but the element (or its enclosing <a>) has an http(s) href,
        navigate there directly so "open this result" actually opens it.
        """
        url_before = ""
        try:
            url_before = str(getattr(page, "url", "") or "")
        except Exception:
            url_before = ""

        link_meta: dict[str, Any] = {}
        try:
            raw_meta = await page.evaluate(
                """(s) => {
                  const el = document.querySelector(s);
                  if (!el) return null;
                  let a = null;
                  try {
                    a = (el.tagName && el.tagName.toLowerCase() === 'a')
                      ? el
                      : (el.closest ? el.closest('a[href]') : null);
                  } catch (e) { a = null; }
                  const node = a || el;
                  let href = '';
                  if (node && node.getAttribute) {
                    const direct = node.getAttribute('href') || '';
                    const alt = node.getAttribute('data-href')
                      || node.getAttribute('data-url')
                      || node.getAttribute('mu')
                      || '';
                    href = (!direct || direct === '#' || /^javascript:/i.test(direct))
                      ? (alt || direct) : direct;
                  }
                  let abs = '';
                  try { abs = (node && node.href) ? String(node.href) : ''; } catch (e) {}
                  const target = (node && node.getAttribute)
                    ? (node.getAttribute('target') || '')
                    : '';
                  return {
                    href: href,
                    abs: abs,
                    target: target,
                    tag: (node && node.tagName) ? String(node.tagName).toLowerCase() : '',
                  };
                }""",
                sel,
            )
            if isinstance(raw_meta, dict):
                link_meta = raw_meta
        except Exception:
            link_meta = {}

        async def _raw_click() -> None:
            last_exc: Optional[BaseException] = None
            try:
                first = 5000 if (sess is not None and sess.get("cdp")) else timeout_ms
                await page.click(sel, timeout=first)
                return
            except Exception as exc:
                last_exc = exc
                if self._is_playwright_timeout(exc) and sess is not None and sess.get("cdp"):
                    try:
                        await page.click(sel, timeout=timeout_ms, force=True)
                        return
                    except Exception as exc2:
                        last_exc = exc2
            try:
                await page.evaluate(
                    "(s) => { const el = document.querySelector(s);"
                    " if (!el) throw new Error('element not found: ' + s);"
                    " try { el.scrollIntoView({ block: 'center' }); } catch (e) {}"
                    " el.click(); }",
                    sel,
                )
            except Exception:
                if last_exc is not None:
                    raise last_exc
                raise

        popup_page = None
        clicked = False
        target_attr = str(link_meta.get("target") or "").strip().lower()
        wants_blank = target_attr == "_blank" or "blank" in target_attr
        # Only arm expect_popup when the link asks for a new tab — otherwise
        # every ordinary click would pay a multi-second popup timeout.
        if wants_blank and not (sess is not None and sess.get("cdp")):
            try:
                async with page.expect_popup(timeout=4000) as popup_info:
                    await _raw_click()
                    clicked = True
                popup_page = await popup_info.value
            except Exception:
                popup_page = None
                if not clicked:
                    await _raw_click()
                    clicked = True
        else:
            await _raw_click()
            clicked = True

        if sess is not None:
            sess["scroll_streak"] = 0

        if popup_page is not None and sess is not None:
            uid = str(sess.get("user_id") or "")
            await self._adopt_popup(uid, sess, popup_page)
            return

        # Same-tab navigation (or Electron windowOpenHandler loading in-place).
        try:
            await page.wait_for_function(
                """(before) => {
                    try { return String(location.href || '') !== String(before || ''); }
                    catch (e) { return false; }
                }""",
                url_before,
                timeout=5000,
            )
        except Exception:
            pass

        url_after = ""
        try:
            url_after = str(getattr(page, "url", "") or "")
        except Exception:
            url_after = ""

        if url_after and url_before and not urls_match(url_after, url_before):
            return

        # Click did not navigate — rescue via resolved http(s) href.
        abs_href = str(link_meta.get("abs") or "").strip()
        raw_href = str(link_meta.get("href") or "").strip()
        cand = abs_href if abs_href.startswith(("http://", "https://")) else (raw_href or abs_href)
        if cand.startswith("//"):
            try:
                scheme = urlparse(url_before).scheme or "https"
            except Exception:
                scheme = "https"
            cand = f"{scheme}:{cand}"
        if not (cand.startswith("http://") or cand.startswith("https://")):
            return
        if url_before and urls_match(cand, url_before):
            return
        # Never "rescue" by navigating to the site homepage while already on a
        # deeper page (search results / article). That recreates the redirect
        # loop when the nearest <a> is a logo or 首页 link.
        if url_before and is_shallow_site_rewind(url_before, cand):
            return

        if wants_blank and sess is not None and not sess.get("cdp"):
            # Headed Playwright: open the URL in a fresh tab and adopt it.
            try:
                ctx = page.context
                new_page = await ctx.new_page()
                await self._goto_page(new_page, cand)
                uid = str(sess.get("user_id") or "")
                await self._adopt_popup(uid, sess, new_page)
                return
            except Exception:
                pass
        # Same-tab (or CDP/Electron single-view): drive navigation ourselves.
        await self._goto_page(page, cand)

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

    def click_ref(self, ref: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        raw = (ref or "").strip()
        if raw.isdigit() or (raw.startswith("e") and raw[1:].isdigit()):
            idx = int(raw[1:] if raw.startswith("e") else raw)
            return self.click_index(idx, user_id=uid)

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._ref_selector(sess, ref)
            try:
                await page.locator(sel).first.scroll_into_view_if_needed(timeout=8000)
            except Exception:
                pass
            try:
                await page.evaluate(
                    "(s) => { const el = document.querySelector(s);"
                    " if (el) el.style.outline = '3px solid #0a84ff'; }",
                    sel,
                )
                await page.wait_for_timeout(400)
            except Exception:
                pass
            try:
                await self._click_on(page, sel, sess=sess)
            finally:
                try:
                    await page.evaluate(
                        "(s) => { const el = document.querySelector(s);"
                        " if (el) el.style.outline = ''; }",
                        sel,
                    )
                except Exception:
                    pass
                await self._clear_highlight(page)
            return {"action": "click", "ref": ref, "selector": sel}

        return self._run_action(uid, _do, observe_after=True)

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

    def network_recent(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 30,
        failed_only: bool = False,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        with self._lock:
            sess = self._sessions.get(uid)
            net = list((sess or {}).get("network") or [])
        if failed_only:
            net = [
                e for e in net
                if e.get("failed") or int(e.get("status") or 0) >= 400
            ]
        n = max(1, min(int(limit or 30), 100))
        return {"total_recorded": len(net), "requests": net[-n:]}

    def diagnostics(
        self, *, user_id: Optional[str] = None, limit: int = 20
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        with self._lock:
            sess = self._sessions.get(uid)
            logs = list((sess or {}).get("console") or [])
            net = list((sess or {}).get("network") or [])
        errs = [e for e in logs if e.get("type") in ("error", "pageerror")]
        failed = [
            e for e in net
            if e.get("failed") or int(e.get("status") or 0) >= 400
        ]
        n = max(1, min(int(limit or 20), 50))
        return {
            "url": (sess or {}).get("url") or "",
            "console_error_total": len(errs),
            "console_errors": errs[-n:],
            "failed_request_total": len(failed),
            "failed_requests": [
                {
                    "method": e.get("method"),
                    "url": (e.get("url") or "")[:200],
                    "status": e.get("status"),
                    "failure": e.get("failure") or "",
                }
                for e in failed[-n:]
            ],
        }

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


    def go_back(self, *, user_id: Optional[str] = None, lane: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id, lane)

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            before = str(getattr(page, "url", "") or "")
            resp = await page.go_back(timeout=20000, wait_until="domcontentloaded")
            after = str(getattr(page, "url", "") or "")
            if resp is None or after == before:
                with self._lock:
                    hist = list(sess.get("history") or [])
                prev = ""
                while hist:
                    cand = str(hist.pop() or "")
                    if cand and cand != before:
                        prev = cand
                        break
                with self._lock:
                    sess["history"] = hist
                if prev:
                    await self._goto_page(page, prev)
                    after = str(getattr(page, "url", "") or prev)
            with self._lock:
                if after != before:
                    hist = list(sess.get("history") or [])
                    if hist and hist[-1] == after:
                        hist.pop()
                    sess["history"] = hist
                sess["url"] = after
            return {"url": after, "ok": bool(after and after != before), "from": before}

        return _WORKER.call(_op, timeout=30.0)

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

    def go_forward(self, *, user_id: Optional[str] = None, lane: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id, lane)

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            resp = await page.go_forward(timeout=20000, wait_until="domcontentloaded")
            with self._lock:
                sess["url"] = page.url
            return {"url": page.url, "ok": resp is not None}

        return _WORKER.call(_op, timeout=30.0)

    # ---- multi-tab management ----

    @staticmethod
    def _context_pages(sess: dict[str, Any]) -> list[Any]:
        cur = sess.get("page")
        context = getattr(cur, "context", None)
        pages = list(getattr(context, "pages", []) or []) if context else []
        return [p for p in pages if BrowserSandboxDialogsMixin._page_alive(p)]

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

