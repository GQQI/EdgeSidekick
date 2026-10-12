"""Browser observation layer (browser-use / ReAct WebShop / Playwright-MCP style).

Inspired by:
  - browser-use (MIT): viewport scroll pixels + interactive selector map
    https://github.com/browser-use/browser-use
  - Playwright MCP: accessibility/action refs; community guidance to prefer
    viewport-reachable elements over full-page dumps
    https://github.com/microsoft/playwright-mcp
  - SeeAct / SOM-style numbered overlays for in-view candidates only

We adapt observe → act-by-index → re-observe without depending on those packages.
Defaults are viewport-first with a hard cap and importance ranking so large pages
do not dump hundreds of nodes into the LLM context.

This module owns:
  - injected JS that indexes interactive elements (shadow roots + same-origin iframes)
  - highlight / clear overlay helpers (Set-of-Marks for the capped candidate set)
  - compact text formatting the model reads every step
"""

from __future__ import annotations

import json
from typing import Any, Optional

# Attribute stamped on live DOM nodes for index → element resolution this step.
IDX_ATTR = "data-bk-idx"
HIGHLIGHT_ROOT_ID = "bk-agent-highlight-root"

# ---------------------------------------------------------------------------
# Observation JS — returns {url, title, scroll, elements:[...], count}
# Cap / viewport expansion are injected via placeholders.
# ---------------------------------------------------------------------------

OBSERVE_JS = r"""
(() => {
  const LIMIT = __LIMIT__;
  const VIEWPORT_EXPAND = __EXPAND__;
  const DO_HIGHLIGHT_ALL = __HIGHLIGHT_ALL__;
  const IDX_ATTR = "__IDX_ATTR__";
  const ROOT_ID = "__ROOT_ID__";

  // Clear previous step indices + overlays.
  try {
    document.querySelectorAll("[" + IDX_ATTR + "]").forEach((el) => {
      try { el.removeAttribute(IDX_ATTR); } catch (e) {}
    });
  } catch (e) {}
  try {
    const old = document.getElementById(ROOT_ID);
    if (old) old.remove();
  } catch (e) {}

  function trim(s, n) {
    return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, n);
  }

  function isVisible(el) {
    if (!el || el.nodeType !== 1) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (!st || st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
    const expand = VIEWPORT_EXPAND;
    if (expand !== -1) {
      if (r.bottom < -expand || r.top > window.innerHeight + expand) return false;
      if (r.right < -expand || r.left > window.innerWidth + expand) return false;
    }
    return true;
  }

  function isInteractive(el) {
    if (!el || !el.tagName) return false;
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const interactiveTags = new Set([
      "a", "button", "input", "textarea", "select", "summary", "option",
    ]);
    const interactiveRoles = new Set([
      "button", "link", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
      "checkbox", "radio", "option", "switch", "textbox", "searchbox",
      "combobox", "slider", "spinbutton",
    ]);
    if (interactiveTags.has(tag)) {
      if (tag === "a" && !el.getAttribute("href") && role !== "button") {
        // bare <a> without href — only keep if role/handler suggests click
      } else {
        return true;
      }
    }
    if (interactiveRoles.has(role)) return true;
    if (el.isContentEditable || el.getAttribute("contenteditable") === "true") return true;
    if (el.hasAttribute("onclick") || el.hasAttribute("ng-click") || el.hasAttribute("@click")) return true;
    const ti = el.getAttribute("tabindex");
    if (ti !== null && ti !== "" && Number(ti) >= 0) return true;
    try {
      const st = window.getComputedStyle(el);
      if (st && st.cursor === "pointer" && (el.onclick || el.getAttribute("onclick"))) return true;
    } catch (e) {}
    return false;
  }

  function isTopAtCenter(el) {
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
      // Outside strict viewport but inside expand band — keep.
      return true;
    }
    try {
      const root = el.getRootNode && el.getRootNode();
      const doc = (root && root.elementFromPoint) ? root : document;
      let top = doc.elementFromPoint(x, y);
      if (!top) return true;
      let cur = top;
      while (cur) {
        if (cur === el) return true;
        cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
      }
      return false;
    } catch (e) {
      return true;
    }
  }

  function textOf(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea") {
      return trim(
        el.getAttribute("aria-label") ||
        el.getAttribute("placeholder") ||
        el.getAttribute("name") ||
        el.getAttribute("title") ||
        el.value || "",
        80
      );
    }
    return trim(
      el.getAttribute("aria-label") ||
      el.innerText ||
      el.textContent ||
      el.getAttribute("title") ||
      el.getAttribute("alt") ||
      el.getAttribute("placeholder") ||
      el.value || "",
      80
    );
  }

  const collected = [];
  const seen = new Set();

  function visit(root, iframeOffset) {
    if (!root) return;
    let nodes;
    try {
      nodes = root.querySelectorAll("*");
    } catch (e) {
      return;
    }
    for (const el of nodes) {
      if (seen.has(el)) continue;
      // open shadow root
      if (el.shadowRoot) {
        visit(el.shadowRoot, iframeOffset);
      }
      // same-origin iframe
      if (el.tagName && el.tagName.toLowerCase() === "iframe") {
        try {
          const doc = el.contentDocument || (el.contentWindow && el.contentWindow.document);
          if (doc) {
            const ir = el.getBoundingClientRect();
            visit(doc, { x: iframeOffset.x + ir.left, y: iframeOffset.y + ir.top });
          }
        } catch (e) { /* cross-origin */ }
      }
      if (!isInteractive(el)) continue;
      if (!isVisible(el)) continue;
      if (!isTopAtCenter(el)) continue;
      // Prefer leaf: skip if a collected descendant already covers this.
      let covered = false;
      for (const other of collected) {
        if (el.contains(other.el)) { covered = true; break; }
      }
      if (covered) continue;
      // Drop ancestors already collected that this leaf replaces.
      for (let i = collected.length - 1; i >= 0; i--) {
        if (collected[i].el.contains(el)) collected.splice(i, 1);
      }
      seen.add(el);
      collected.push({ el, iframeOffset });
      if (collected.length >= LIMIT * 2) break;
    }
  }

  visit(document, { x: 0, y: 0 });

  // Promote enclosing anchors for leaf controls inside links (search results).
  const promoted = [];
  const seenEl = new Set();
  for (const item of collected) {
    const el = item.el;
    if (!seenEl.has(el)) { seenEl.add(el); promoted.push(item); }
    let a = null;
    try { a = el.closest ? el.closest("a[href]") : null; } catch (e) { a = null; }
    if (a && a !== el && isVisible(a) && !seenEl.has(a)) {
      seenEl.add(a);
      promoted.push({ el: a, iframeOffset: item.iframeOffset });
    }
  }

  function inStrictViewport(el) {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  }

  function scoreItem(item) {
    const el = item.el;
    const r = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const name = textOf(el);
    const area = Math.max(0, r.width) * Math.max(0, r.height);
    let score = 0;
    // browser-use / Playwright-MCP guidance: prefer reachable viewport nodes.
    if (inStrictViewport(el)) score += 1000;
    else score += 100; // expand-band only
    const isField = tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
    if (isField) score += 220;
    if (tag === "button" || role === "button") score += 180;
    if (tag === "a" || role === "link") score += 120;
    if (tag === "summary" || role === "tab" || role === "menuitem") score += 90;
    // Size: larger hit targets are more actionable; tiny chrome ranks lower.
    score += Math.min(180, Math.sqrt(area));
    // Semantic: named controls beat empty chrome.
    if (name) score += Math.min(80, name.length);
    // Demote pure site-home links (logo / 首页) — they cause restart loops.
    try {
      if (tag === "a") {
        const h = el.getAttribute("href") || "";
        const u = new URL(h, location.href);
        const path = u.pathname || "/";
        if ((path === "/" || path === "") && !(u.search || "").trim()) score -= 400;
      }
    } catch (e) {}
    // Demote disabled / aria-disabled already filtered; demote footer-ish y.
    if (r.top > window.innerHeight * 0.92) score -= 60;
    return score;
  }

  // Visual order (top-to-bottom, then left-to-right), viewport first.
  // browser-use / Playwright-MCP index what is on screen in reading order so
  // "the Nth article" is the Nth content link, not the Nth highest score.
  function visualKey(item) {
    const r = item.el.getBoundingClientRect();
    const off = item.iframeOffset || { x: 0, y: 0 };
    const top = r.top + (off.y || 0);
    const left = r.left + (off.x || 0);
    return {
      band: inStrictViewport(item.el) ? 0 : 1,
      row: Math.round(top / 12),
      left: left,
      score: scoreItem(item),
    };
  }
  const ranked = promoted
    .map((item) => ({ item, key: visualKey(item) }))
    .sort((a, b) => {
      if (a.key.band !== b.key.band) return a.key.band - b.key.band;
      if (a.key.row !== b.key.row) return a.key.row - b.key.row;
      if (a.key.left !== b.key.left) return a.key.left - b.key.left;
      return b.key.score - a.key.score;
    });

  const totalCandidates = ranked.length;
  const elements = [];
  let index = 1;
  let truncated = false;
  // Document-order ordinal for real links, including ones outside the viewport.
  // Viewport-local numbering made "the Nth article" look like it was already
  // on screen, so the model never scrolled.
  const contentOrdinal = new Map();
  let contentTotal = 0;
  let contentAbove = 0;
  let contentBelow = 0;
  let contentIn = 0;
  function considerContentLink(el) {
    if (!el || !el.tagName || el.tagName.toLowerCase() !== "a") return;
    const h = el.getAttribute("href") || el.getAttribute("data-href") || el.getAttribute("data-url") || el.getAttribute("mu") || "";
    if (!h || h === "#" || /^javascript:/i.test(h)) return;
    let home = false;
    try {
      const u = new URL(h, location.href);
      const path = u.pathname || "/";
      home = (path === "/" || path === "") && !(u.search || "").trim();
    } catch (e) { home = false; }
    if (home) return;
    const st = window.getComputedStyle(el);
    if (!st || st.display === "none" || st.visibility === "hidden") return;
    contentTotal += 1;
    contentOrdinal.set(el, contentTotal);
    const r = el.getBoundingClientRect();
    if (r.bottom <= 0) contentAbove += 1;
    else if (r.top >= window.innerHeight) contentBelow += 1;
    else contentIn += 1;
  }
  try {
    document.querySelectorAll("a[href], a[data-href], a[data-url], a[mu]").forEach(considerContentLink);
  } catch (e) {}
  for (const { item } of ranked) {
    if (elements.length >= LIMIT) { truncated = true; break; }
    const el = item.el;
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "") || (
      tag === "a" ? "link" :
      tag === "button" ? "button" :
      (tag === "input" || tag === "textarea") ? "textbox" :
      tag === "select" ? "combobox" : ""
    );
    const name = textOf(el);
    const isField = tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
    if (!name && !isField) continue;

    try { el.setAttribute(IDX_ATTR, String(index)); } catch (e) { continue; }

    const rec = {
      index: index,
      tag: tag,
      role: role,
      name: name,
      in_viewport: inStrictViewport(el),
    };
    const type = el.getAttribute("type") || "";
    if (type) rec.type = type;
    if (tag === "input" || tag === "textarea" || el.isContentEditable) {
      const v = trim(el.value != null ? el.value : el.textContent, 60);
      if (v) rec.value = v;
      const ph = trim(el.getAttribute("placeholder"), 60);
      if (ph) rec.placeholder = ph;
    }
    const rawHref = (
      el.getAttribute("href") ||
      el.getAttribute("data-href") ||
      el.getAttribute("data-url") ||
      el.getAttribute("mu") ||
      ""
    );
    if (tag === "a" || rawHref) {
      const h = rawHref;
      if (h && h !== "#" && !/^javascript:/i.test(h)) {
        let abs = h;
        try { abs = new URL(h, location.href).href; } catch (e) {}
        rec.href = trim(abs || h, 180);
        try {
          const u = new URL(rec.href, location.href);
          const path = u.pathname || "/";
          if ((path === "/" || path === "") && !(u.search || "").trim()) {
            rec.home_link = true;
          }
        } catch (e) {}
      }
    }
    const absN = contentOrdinal.get(el);
    if (absN) rec.content_n = absN;
    if (el.checked === true) rec.checked = true;
    elements.push(rec);

    if (DO_HIGHLIGHT_ALL) {
      try { drawBox(el, index, item.iframeOffset); } catch (e) {}
    }
    index += 1;
  }

  function drawBox(el, idx, iframeOffset) {
    let root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement("div");
      root.id = ROOT_ID;
      root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
      document.documentElement.appendChild(root);
    }
    const r = el.getBoundingClientRect();
    const top = r.top + (iframeOffset ? iframeOffset.y : 0);
    const left = r.left + (iframeOffset ? iframeOffset.x : 0);
    const box = document.createElement("div");
    box.style.cssText = [
      "position:fixed",
      "top:" + top + "px",
      "left:" + left + "px",
      "width:" + Math.max(r.width, 2) + "px",
      "height:" + Math.max(r.height, 2) + "px",
      "border:2px solid #ff3b30",
      "background:rgba(255,59,48,0.08)",
      "box-sizing:border-box",
      "border-radius:3px",
    ].join(";");
    const label = document.createElement("div");
    label.textContent = String(idx);
    label.style.cssText = [
      "position:fixed",
      "top:" + Math.max(0, top - 16) + "px",
      "left:" + left + "px",
      "background:#ff3b30",
      "color:#fff",
      "font:11px/14px monospace",
      "padding:0 4px",
      "border-radius:3px",
    ].join(";");
    root.appendChild(box);
    root.appendChild(label);
  }

  const scrollY = window.scrollY || document.documentElement.scrollTop || 0;
  const viewport = window.innerHeight || 0;
  const docH = Math.max(
    document.body ? document.body.scrollHeight : 0,
    document.documentElement ? document.documentElement.scrollHeight : 0
  );
  const above = Math.max(0, Math.round(scrollY));
  const below = Math.max(0, Math.round(docH - scrollY - viewport));

  return {
    url: location.href,
    title: document.title || "",
    scroll: { above: above, below: below, y: Math.round(scrollY), viewport: viewport, height: docH },
    count: elements.length,
    total_candidates: totalCandidates,
    truncated: truncated || totalCandidates > elements.length,
    elements: elements,
    content_links: {
      total: contentTotal,
      above: contentAbove,
      below: contentBelow,
      in_view: contentIn,
    },
  };
})()
"""

HIGHLIGHT_INDEX_JS = r"""
(idx) => {
  const IDX_ATTR = "__IDX_ATTR__";
  const ROOT_ID = "__ROOT_ID__";
  const el = document.querySelector("[" + IDX_ATTR + "=\"" + String(idx) + "\"]");
  if (!el) return { ok: false, reason: "missing" };
  try { el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }); } catch (e) {
    try { el.scrollIntoView(true); } catch (e2) {}
  }
  let root = document.getElementById(ROOT_ID);
  if (root) root.remove();
  root = document.createElement("div");
  root.id = ROOT_ID;
  root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
  document.documentElement.appendChild(root);
  const r = el.getBoundingClientRect();
  const box = document.createElement("div");
  box.style.cssText = [
    "position:fixed",
    "top:" + r.top + "px",
    "left:" + r.left + "px",
    "width:" + Math.max(r.width, 4) + "px",
    "height:" + Math.max(r.height, 4) + "px",
    "border:3px solid #0a84ff",
    "background:rgba(10,132,255,0.15)",
    "box-sizing:border-box",
    "border-radius:4px",
    "box-shadow:0 0 0 2px rgba(10,132,255,0.35)",
  ].join(";");
  const label = document.createElement("div");
  label.textContent = "[" + String(idx) + "]";
  label.style.cssText = [
    "position:fixed",
    "top:" + Math.max(0, r.top - 18) + "px",
    "left:" + r.left + "px",
    "background:#0a84ff",
    "color:#fff",
    "font:12px/16px monospace",
    "padding:1px 6px",
    "border-radius:4px",
  ].join(";");
  root.appendChild(box);
  root.appendChild(label);
  return { ok: true, index: idx, rect: { top: r.top, left: r.left, width: r.width, height: r.height } };
}
"""

CLEAR_HIGHLIGHT_JS = r"""
() => {
  const ROOT_ID = "__ROOT_ID__";
  const n = document.getElementById(ROOT_ID);
  if (n) n.remove();
  return true;
}
"""

SCROLL_TO_TEXT_JS = r"""
(text) => {
  const needle = String(text || "").trim();
  if (!needle) return { ok: false, reason: "empty" };
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walk.nextNode())) {
    const t = (node.nodeValue || "").replace(/\s+/g, " ");
    if (t.toLowerCase().includes(needle.toLowerCase())) {
      const parent = node.parentElement;
      if (parent) {
        try { parent.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {
          try { parent.scrollIntoView(true); } catch (e2) {}
        }
        return { ok: true, matched: t.trim().slice(0, 80) };
      }
    }
  }
  return { ok: false, reason: "not_found" };
}
"""

# Same main-result rules as considerContentLink in OBSERVE_JS.
# One call jumps the Nth result into view, including inner scrollers.
SCROLL_TO_CONTENT_JS = r"""
(n) => {
  const target = Math.max(1, Number(n) || 1);
  function isMain(el) {
    if (!el || !el.tagName || el.tagName.toLowerCase() !== "a") return false;
    const h = el.getAttribute("href") || el.getAttribute("data-href") || el.getAttribute("data-url") || el.getAttribute("mu") || "";
    if (!h || h === "#" || /^javascript:/i.test(h)) return false;
    try {
      const u = new URL(h, location.href);
      const path = u.pathname || "/";
      if ((path === "/" || path === "") && !(u.search || "").trim()) return false;
    } catch (e) {}
    const st = window.getComputedStyle(el);
    if (!st || st.display === "none" || st.visibility === "hidden") return false;
    return true;
  }
  const links = document.querySelectorAll("a[href], a[data-href], a[data-url], a[mu]");
  let count = 0;
  let el = null;
  for (const a of links) {
    if (!isMain(a)) continue;
    count += 1;
    if (count === target) { el = a; break; }
  }
  if (!el) return { ok: false, reason: "not_found", total: count, content: target };
  try { el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }); }
  catch (e) { try { el.scrollIntoView(true); } catch (e2) {} }
  const r = el.getBoundingClientRect();
  return {
    ok: true,
    content: target,
    total: count,
    name: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80),
    top: Math.round(r.top),
  };
}
"""

# One viewport at a time. Scroll the box under the viewport center
# (browser-use), and also the window, so either kind of page moves.
SCROLL_PAGES_JS = r"""
(pages) => {
  const n = Number(pages);
  const steps = n === 0 || !isFinite(n) ? 1 : n;
  const dy = Math.round(steps * (window.innerHeight || 800) * 0.9);
  function scrollable(start) {
    let el = start;
    while (el && el !== document.body && el !== document.documentElement) {
      const st = window.getComputedStyle(el);
      const oy = st.overflowY;
      const room = (el.scrollHeight || 0) - (el.clientHeight || 0);
      if (room > 4 && (oy === "auto" || oy === "scroll" || oy === "overlay")) return el;
      el = el.parentElement;
    }
    return document.scrollingElement || document.documentElement;
  }
  const hit = document.elementFromPoint(Math.floor(window.innerWidth / 2), Math.floor(window.innerHeight / 2));
  const box = scrollable(hit);
  const winBefore = window.scrollY || 0;
  const boxBefore = box.scrollTop || 0;
  window.scrollBy(0, dy);
  try { box.scrollBy({ top: dy, left: 0, behavior: "instant" }); }
  catch (e) { box.scrollTop = (box.scrollTop || 0) + dy; }
  const winAfter = window.scrollY || 0;
  const boxAfter = box.scrollTop || 0;
  return {
    ok: Math.abs(winAfter - winBefore) > 4 || Math.abs(boxAfter - boxBefore) > 4,
    before: Math.round(winBefore),
    after: Math.round(winAfter),
    boxBefore: Math.round(boxBefore),
    boxAfter: Math.round(boxAfter),
    pages: steps,
  };
}
"""

EXTRACT_CONTENT_JS = r"""
(maxChars) => {
  const limit = Math.max(500, Math.min(Number(maxChars) || 12000, 50000));
  const denylist = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "SVG", "PATH"]);
  function visibleText(root) {
    const parts = [];
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walk.nextNode())) {
      const p = n.parentElement;
      if (!p || denylist.has(p.tagName)) continue;
      const st = window.getComputedStyle(p);
      if (!st || st.display === "none" || st.visibility === "hidden") continue;
      const t = (n.nodeValue || "").replace(/\s+/g, " ").trim();
      if (t) parts.push(t);
      if (parts.join(" ").length > limit) break;
    }
    return parts.join(" ").slice(0, limit);
  }
  const main = document.querySelector("main, article, [role='main'], #content, .content") || document.body;
  const text = visibleText(main || document.body);
  return {
    url: location.href,
    title: document.title || "",
    text: text,
    truncated: text.length >= limit,
  };
}
"""

SELECTOR_FOR_INDEX_JS = r"""
(idx) => {
  const IDX_ATTR = "__IDX_ATTR__";
  const el = document.querySelector("[" + IDX_ATTR + "=\"" + String(idx) + "\"]");
  if (!el) return null;
  return "[" + IDX_ATTR + "=\"" + String(idx) + "\"]";
}
"""


def _fill(js: str) -> str:
    return (
        js.replace("__IDX_ATTR__", IDX_ATTR)
        .replace("__ROOT_ID__", HIGHLIGHT_ROOT_ID)
    )


def observe_js(*, limit: int = 40, expand: int = 0, highlight_all: bool = False) -> str:
    # Cap hard: large pages must paginate via scroll + re-observe, not dump.
    n = max(1, min(int(limit or 40), 80))
    exp = int(expand)
    return (
        _fill(OBSERVE_JS)
        .replace("__LIMIT__", str(n))
        .replace("__EXPAND__", str(exp))
        .replace("__HIGHLIGHT_ALL__", "true" if highlight_all else "false")
    )


def highlight_index_js() -> str:
    return _fill(HIGHLIGHT_INDEX_JS)


def clear_highlight_js() -> str:
    return _fill(CLEAR_HIGHLIGHT_JS)


def scroll_to_text_js() -> str:
    return SCROLL_TO_TEXT_JS


def scroll_to_content_js() -> str:
    return SCROLL_TO_CONTENT_JS


def scroll_pages_js() -> str:
    return SCROLL_PAGES_JS


def extract_content_js() -> str:
    return EXTRACT_CONTENT_JS


def selector_for_index_js() -> str:
    return _fill(SELECTOR_FOR_INDEX_JS)


def format_element_line(el: dict[str, Any]) -> str:
    idx = el.get("index")
    tag = str(el.get("tag") or "div")
    role = str(el.get("role") or "")
    name = str(el.get("name") or "")
    head = f"[{idx}]<{tag}"
    if role and role not in {tag, "textbox", "link", "button"}:
        head = f"[{idx}]<{tag} role={role}"
    attrs: list[str] = []
    if el.get("type"):
        attrs.append(f'type="{el["type"]}"')
    if el.get("placeholder"):
        attrs.append(f'placeholder="{el["placeholder"]}"')
    if el.get("value"):
        attrs.append(f'value="{el.get("value")}"')
    if el.get("href"):
        attrs.append(f'href="{el["href"]}"')
    if el.get("content_n"):
        attrs.append(f'content={el["content_n"]}')
    if el.get("home_link"):
        attrs.append("home_link")
    if el.get("checked"):
        attrs.append("checked")
    inner = (" " + " ".join(attrs)) if attrs else ""
    # Avoid duplicating value/placeholder as body text for fields.
    body = ""
    if tag not in {"input", "textarea", "select"}:
        body = name
    elif name and name not in {
        str(el.get("value") or ""),
        str(el.get("placeholder") or ""),
    }:
        body = name
    return f"{head}{inner}>{body}</{tag}>"


def format_observation(
    data: dict[str, Any],
    *,
    tabs: Optional[list[dict[str, Any]]] = None,
    action_note: str = "",
) -> str:
    """Compact text the model reads each step."""
    lines: list[str] = []
    if action_note:
        lines.append(f"action: {action_note}")
    lines.append(f"url: {data.get('url') or ''}")
    lines.append(f"title: {data.get('title') or ''}")
    scroll = data.get("scroll") or {}
    if isinstance(scroll, dict):
        lines.append(
            f"scroll: {int(scroll.get('above') or 0)}px above, "
            f"{int(scroll.get('below') or 0)}px below"
        )
    span = data.get("content_links") if isinstance(data.get("content_links"), dict) else {}
    if span:
        below_n = int(span.get("below") or 0)
        above_n = int(span.get("above") or 0)
        lines.append(
            f"content_links: {int(span.get('in_view') or 0)} in this viewport, "
            f"{above_n} above, {below_n} below, {int(span.get('total') or 0)} in the document. "
            "content=N is document order of links, not the row on screen."
        )
        if below_n > 0:
            lines.append(
                "More links are below this viewport. browser_scroll(direction=\"down\") moves one screen."
            )
    if tabs:
        tab_bits = []
        for t in tabs:
            mark = "*" if t.get("current") else ""
            tab_bits.append(
                f"{t.get('index', '?')}{mark}:{trim_str(t.get('title') or t.get('url') or '', 40)}"
            )
        lines.append("tabs: " + " | ".join(tab_bits))
    if data.get("needs_human") or (data.get("challenge") or {}).get("challenged"):
        lines.append(
            "ALERT: captcha/security check detected — call browser_wait_for_user; "
            "do NOT click through; do NOT navigate home."
        )
    els = [e for e in (data.get("elements") or []) if isinstance(e, dict)]
    total = int(data.get("total_candidates") or len(els) or 0)
    truncated = bool(data.get("truncated")) or total > len(els)
    lines.append(
        f"elements ({len(els)}"
        + (f" of ~{total} candidates, truncated" if truncated and total > len(els) else "")
        + "):"
    )
    if not els:
        lines.append("(none in viewport — browser_scroll or browser_scroll_to_text)")
    else:
        for e in els:
            lines.append(format_element_line(e))
    if truncated or (isinstance(scroll, dict) and int(scroll.get("below") or 0) > 80):
        lines.append(
            "hint: viewport-first and capped, visual order. "
            "browser_scroll(direction=\"down\") moves one viewport, then click an index in the new list. "
            "browser_extract_content only after the "
            "article page is open (do NOT ask for a full DOM dump)."
        )
    hint = data.get("hint")
    if hint:
        lines.append(f"hint: {hint}")
    return "\n".join(lines)


def trim_str(s: Any, n: int) -> str:
    return str(s or "").replace("\n", " ").strip()[:n]


def observation_payload(
    data: dict[str, Any],
    *,
    tabs: Optional[list[dict[str, Any]]] = None,
    action_note: str = "",
) -> dict[str, Any]:
    """Structured + text observation for tool results."""
    text = format_observation(data, tabs=tabs, action_note=action_note)
    out = dict(data)
    out["observation"] = text
    if tabs is not None:
        out["tabs"] = tabs
    if action_note:
        out["action"] = action_note
    return out
