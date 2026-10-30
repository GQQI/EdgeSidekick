"""Click actions for the browser sandbox."""
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


class BrowserClickMixin:
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
    def click_index(self, index: int, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid, int(index))
        if held:
            return held

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._index_selector(sess, int(index))
            await self._highlight_index(page, int(index))
            try:
                await self._click_on(page, sel, sess=sess)
            finally:
                await self._clear_highlight(page)
            return {"action": "click", "index": int(index), "selector": sel}

        return self._run_action(uid, _do, observe_after=True)

