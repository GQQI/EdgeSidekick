"""BrowserSandbox mixin: observe_ops."""
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
from ..constants import (
    HOST_KIND,
    SESSION_IDLE_SECONDS,
    SOCIAL_BROWSING,
    _HUMAN_CONFIRM_S,
    _REAP_INTERVAL_SECONDS,
    _USER_WAIT_TIMEOUT,
)
from ..thread import _WORKER
from . import core as _bobs
from ..urls import (
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

_LIST_ELEMENTS_JS = r"""
(() => {
  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + cssEscape(el.id);
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur.nodeType === 1 && depth < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift("#" + cssEscape(cur.id)); break; }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (siblings.length > 1) part += ":nth-of-type(" + (siblings.indexOf(cur) + 1) + ")";
      }
      parts.unshift(part);
      cur = parent;
      depth++;
    }
    return parts.join(" > ");
  }
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return false;
    return true;
  }
  function textOf(el) {
    const t = (
      el.innerText || el.value || el.getAttribute("aria-label") ||
      el.getAttribute("placeholder") || el.getAttribute("title") || ""
    ).trim();
    return t.replace(/\s+/g, " ").slice(0, 120);
  }
  const SEL = 'a[href], button, [role="button"], [role="link"], [role="tab"], ' +
    '[role="menuitem"], [role="checkbox"], [role="switch"], input, select, ' +
    'textarea, summary, [onclick], [tabindex]:not([tabindex="-1"])';
  const nodes = Array.from(document.querySelectorAll(SEL));
  const out = [];
  const seen = new Set();
  for (const el of nodes) {
    if (out.length >= __LIMIT__) break;
    if (!visible(el)) continue;
    const path = cssPath(el);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    const tag = el.tagName.toLowerCase();
    const rec = {
      index: out.length,
      tag,
      type: el.getAttribute("type") || "",
      text: textOf(el),
      selector: path,
    };
    if (tag === "a") rec.href = el.getAttribute("href") || "";
    out.push(rec);
  }
  return out;
})()
"""

_SNAPSHOT_JS = r"""(() => {
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = window.getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) === 0) return false;
    return true;
  }
  function trim(s, n) {
    return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n);
  }
  const SEL = 'a[href], button, [role="button"], [role="link"], [role="tab"], ' +
    '[role="menuitem"], [role="checkbox"], [role="switch"], [role="option"], ' +
    '[role="combobox"], input, select, textarea, summary, [onclick], ' +
    '[contenteditable]:not([contenteditable="false"])';
  let nodes;
  try { nodes = Array.from(document.querySelectorAll(SEL)); } catch (e) { nodes = []; }
  const vis = nodes.filter(visible);
  const out = [];
  for (const el of vis) {
    // Prefer the innermost target: skip an element if another candidate sits
    // inside it (e.g. <a> wrapping a real <button>).
    let hasInner = false;
    for (const other of vis) {
      if (other !== el && el.contains(other)) { hasInner = true; break; }
    }
    if (hasInner) continue;
    out.push(el);
  }
  // Promote enclosing links: innermost controls inside <a href> are useful for
  // typing, but opening a search result needs the anchor itself. Expose both.
  const promoted = [];
  const seenEl = new Set();
  for (const el of out) {
    if (!seenEl.has(el)) { seenEl.add(el); promoted.push(el); }
    let a = null;
    try { a = el.closest ? el.closest('a[href]') : null; } catch (e) { a = null; }
    if (a && a !== el && visible(a) && !seenEl.has(a)) {
      seenEl.add(a);
      promoted.push(a);
    }
  }
  const items = [];
  let n = 0;
  function sigOf(el, tag, name) {
    // Content signature baked into the ref attribute: after a re-snapshot the
    // same slot number gets a different sig, so a STALE ref from an older
    // snapshot matches nothing instead of silently hitting the wrong element.
    let s = tag + "|" + name;
    let h = 5381;
    for (let i = 0; i < s.length; i++) { h = ((h << 5) + h + s.charCodeAt(i)) | 0; }
    return (h >>> 0).toString(36);
  }
  for (const el of promoted) {
    if (items.length >= __LIMIT__) break;
    const tag = el.tagName.toLowerCase();
    const roleAttr = el.getAttribute("role") || "";
    const fallbackRole = { a: "link", button: "button", select: "combobox" }[tag] ||
      ((tag === "input" || tag === "textarea") ? "textbox" : "");
    const item = { ref: "e" + (++n), tag, role: roleAttr || fallbackRole };
    const name = trim(
      el.getAttribute("aria-label") || el.innerText || el.value ||
      el.getAttribute("placeholder") || el.getAttribute("title") ||
      el.getAttribute("alt") || el.getAttribute("name") || "",
      80
    );
    // A control the model cannot name is pure noise — it can neither match
    // the element to what the page shows nor explain the action. Keep only
    // form fields (value/placeholder carry the meaning) without a name.
    const isField = tag === "input" || tag === "textarea" || tag === "select";
    if (!name && !isField) { try { el.removeAttribute("data-bk-ref"); } catch (e) {} continue; }
    const ref = item.ref;
    const sref = ref + "." + sigOf(el, tag, name);
    try { el.setAttribute("data-bk-ref", sref); } catch (e) { /* detached */ }
    item.sref = sref;
    if (name) item.name = name;
    const type = el.getAttribute("type") || "";
    if (type) item.type = type;
    if (tag === "input" || tag === "textarea") {
      const v = trim(el.value, 60);
      if (v) item.value = v;
      const ph = trim(el.getAttribute("placeholder"), 60);
      if (ph) item.placeholder = ph;
      if (el.disabled) item.disabled = true;
      if (el.required) item.required = true;
    }
    if (tag === "select") {
      const v = trim(el.value, 60);
      if (v) item.value = v;
      try {
        item.options = Array.from(el.options || [])
          .map((o) => trim(o.value || o.text, 40))
          .filter(Boolean)
          .slice(0, 12);
      } catch (e) { /* not a real select */ }
    }
    if (tag === "a") {
      const h = el.getAttribute("href") || "";
      if (h && h !== "#") {
        let abs = h;
        try { abs = new URL(h, location.href).href; } catch (e) {}
        item.href = trim(abs || h, 120);
        try {
          const u = new URL(item.href, location.href);
          const path = u.pathname || "/";
          if ((path === "/" || path === "") && !(u.search || "").trim()) {
            item.home_link = true;
          }
        } catch (e) {}
      }
    }
    if (el.checked === true) item.checked = true;
    if (el.getAttribute("aria-expanded") === "true") item.expanded = true;
    items.push(item);
  }
  return {
    url: location.href,
    title: document.title || "",
    count: items.length,
    elements: items,
  };
})()
"""

class BrowserObserveMixin:

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

    def snapshot_page(
        self, *, user_id: Optional[str] = None, limit: int = 120
    ) -> dict[str, Any]:
        """Observe interactive elements (index list). Name kept for tools."""
        return self.observe_page(user_id=user_id, limit=limit)

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

    def list_interactive_elements(
        self, *, user_id: Optional[str] = None, limit: int = 60
    ) -> list[dict[str, Any]]:
        uid = self._lane_uid(user_id)
        n = max(1, min(int(limit or 60), 150))

        async def _op() -> list[dict[str, Any]]:
            with self._lock:
                page = await self._alive_page(uid)
            js = _LIST_ELEMENTS_JS.replace("__LIMIT__", str(n))
            raw = await page.evaluate(js)
            return list(raw or [])

        return _WORKER.call(_op, timeout=30.0)

    def save_screenshot_to_workspace(
        self,
        workspace: Path,
        *,
        user_id: Optional[str] = None,
        name: str = "",
        full_page: bool = False,
    ) -> Path:
        png = self.screenshot_png(user_id=user_id, full_page=full_page)
        out_dir = Path(workspace) / ".sidekick" / "browser"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = (name or f"shot_{int(time.time())}.png").replace("..", "")
        if not fname.endswith(".png"):
            fname += ".png"
        path = out_dir / fname
        path.write_bytes(png)
        page_url = self.last_url(user_id)
        meta = {
            "url": page_url,
            "path": fname,
            "saved_at": time.time(),
        }
        try:
            path.with_name(path.name + ".json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "latest.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return path

    async def _observe_on_page(
        self,
        page: Any,
        sess: dict[str, Any],
        *,
        limit: int = 100,
        expand: int = 400,
        highlight_all: bool = False,
    ) -> dict[str, Any]:
        """Stamp data-bk-idx on interactive nodes; return structured observation."""
        n = max(1, min(int(limit or 100), 200))
        exp = max(-1, min(int(expand if expand is not None else 400), 5000))
        js = _bobs.observe_js(limit=n, expand=exp, highlight_all=bool(highlight_all))
        prev_sig: Any = None
        raw: Any = None
        for _ in range(3):
            raw = await page.evaluate(js)
            els = (raw or {}).get("elements") or []
            sig = tuple(
                (e.get("index"), e.get("tag"), e.get("name"), e.get("href"))
                for e in els[:24]
                if isinstance(e, dict)
            )
            if sig == prev_sig:
                break
            prev_sig = sig
            await page.wait_for_timeout(350)
        data = dict(raw or {})
        elements = [e for e in (data.get("elements") or []) if isinstance(e, dict)]
        try:
            cur_url = str(getattr(page, "url", "") or data.get("url") or "")
        except Exception:
            cur_url = str(data.get("url") or "")
        refs: dict[str, str] = {}
        index_map: dict[int, str] = {}
        for e in elements:
            idx = e.get("index")
            if idx is None:
                continue
            sel = f'[{_bobs.IDX_ATTR}="{idx}"]'
            refs[f"e{idx}"] = sel
            refs[str(idx)] = sel
            try:
                index_map[int(idx)] = sel
            except Exception:
                pass
        sess["refs"] = refs
        sess["index_map"] = index_map
        if cur_url:
            sess["url"] = cur_url
        data["url"] = cur_url or data.get("url")
        data["elements"] = elements
        data["count"] = len(elements)
        try:
            challenge = await self._probe_challenge(page)
        except Exception:
            challenge = {"challenged": False}
        if challenge.get("challenged"):
            data["needs_human"] = True
            data["challenge"] = challenge
        tabs_info: list[dict[str, Any]] = []
        try:
            cur = sess.get("page")
            for i, p in enumerate(self._context_pages(sess)):
                try:
                    title = (await p.title()) or ""
                except Exception:
                    title = ""
                try:
                    u = str(getattr(p, "url", "") or "")
                except Exception:
                    u = ""
                tabs_info.append(
                    {"index": i, "url": u, "title": title[:80], "current": p is cur}
                )
        except Exception:
            tabs_info = []
        data["tabs"] = tabs_info
        data["hint"] = (
            "Act with browser_click(index=N) / browser_input(index=N, text=...). "
            "If the target is missing, browser_scroll or browser_scroll_to_text. "
            "Never navigate back to the site homepage to restart. "
            "On needs_human, call browser_wait_for_user."
        )
        return data

    def observe_page(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 40,
        expand: int = 0,
        highlight_all: bool = False,
    ) -> dict[str, Any]:
        """Index viewport interactive elements (capped + ranked); compact text."""
        uid = self._lane_uid(user_id)
        n = max(1, min(int(limit or 40), 80))
        # expand=0 => strict viewport (browser-use / Playwright-MCP viewport-first).
        exp = max(-1, min(int(expand if expand is not None else 0), 5000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            data = await self._observe_on_page(
                page, sess, limit=n, expand=exp, highlight_all=bool(highlight_all)
            )
            try:
                cur_url = str(data.get("url") or "")
            except Exception:
                cur_url = ""
            with self._lock:
                if cur_url:
                    sess["url"] = cur_url
                    self._last_url[uid] = cur_url
            # Dual shape: new observation text + legacy elements with ref=eN
            packed = _bobs.observation_payload(
                data, tabs=data.get("tabs"), action_note="observe"
            )
            elements = []
            for e in packed.get("elements") or []:
                if not isinstance(e, dict):
                    continue
                item = {
                    "ref": f"e{e.get('index')}",
                    "index": e.get("index"),
                    "tag": e.get("tag"),
                    "role": e.get("role"),
                    "name": e.get("name"),
                }
                for k in ("href", "placeholder", "value", "type", "home_link", "checked"):
                    if k in e:
                        item[k] = e[k]
                elements.append(item)
            packed["elements"] = elements
            packed["count"] = len(elements)
            return packed

        return _WORKER.call(_op, timeout=45.0)

    async def _highlight_index(self, page: Any, index: int) -> None:
        try:
            await page.evaluate(_bobs.highlight_index_js(), int(index))
            await page.wait_for_timeout(450)
        except Exception:
            pass

    async def _clear_highlight(self, page: Any) -> None:
        try:
            await page.evaluate(_bobs.clear_highlight_js())
        except Exception:
            pass

    def extract_content(
        self,
        *,
        user_id: Optional[str] = None,
        goal: str = "",
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        limit = max(500, min(int(max_chars or 12000), 50000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
            raw = await page.evaluate(_bobs.extract_content_js(), limit)
            out = dict(raw or {})
            if goal:
                out["goal"] = goal
            if goal and out.get("text"):
                tokens = [t for t in re.split(r"\s+", goal) if len(t) >= 2][:8]
                body = str(out.get("text") or "")
                if tokens:
                    hits = [
                        line
                        for line in re.split(r"(?<=[。！？.!?\n])", body)
                        if any(tok.lower() in line.lower() for tok in tokens)
                    ]
                    if hits:
                        clipped = "\n".join(hits)
                        if len(clipped) > limit:
                            clipped = clipped[:limit]
                        out["text"] = clipped
                        out["filtered_by_goal"] = True
            return out

        return _WORKER.call(_op, timeout=30.0)
