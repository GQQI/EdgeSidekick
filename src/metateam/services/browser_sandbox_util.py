"""URL / dialog / CDP helpers for the browser sandbox (no Playwright worker)."""
from __future__ import annotations

import re
import json
import urllib.request
import os
import asyncio
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

from ..runtime.browser_protocol import (
    PROTOCOL_VERSION,
    STYLE_KEYS,
    DomComponentHint,
    DomElementPayload,
    DomRect,
)

_URL_EXTRACT_RE = re.compile(
    r"https?://[A-Za-z0-9][-A-Za-z0-9._~:/?#\[\]@!$&'()+,;=%]*",
    re.IGNORECASE,
)


def sanitize_browser_url(raw: str) -> str:
    """Return a navigable http(s) URL, or '' if none.

    ``http://localhost:5173**，已在/`` → ``http://localhost:5173/``
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text == "about:blank":
        return text
    m = _URL_EXTRACT_RE.search(text)
    candidate = m.group(0) if m else text
    star = candidate.find("*")
    if star >= 0:
        candidate = candidate[:star]
    for i, ch in enumerate(candidate):
        if ord(ch) > 127:
            candidate = candidate[:i]
            break
    candidate = candidate.rstrip("),.;:!?，。；！？*_~`")
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https"):
            return ""
        if not parsed.netloc:
            return ""
        return urlunparse(parsed)
    except Exception:
        return ""


def _workspace_root(explicit: Optional[Path]) -> Optional[Path]:
    if explicit is not None:
        try:
            root = Path(explicit)
            if str(root) and root.is_dir():
                return root
        except OSError:
            pass
    from .workspace_store import get_active_workspace

    ws = get_active_workspace()
    root_s = str(ws.get("path") or "").strip() if ws.get("configured") else ""
    if not root_s:
        return None
    root = Path(root_s)
    return root if root.is_dir() else None


def resolve_browser_target(raw: str, workspace: Optional[Path] = None) -> str:
    """http(s) URL, or local workspace HTML served as http://127.0.0.1/..."""
    http = sanitize_browser_url(raw)
    if http:
        return http
    from .browser_preview import (
        is_workspace_page_ref,
        preview_http_url,
        resolve_local_html_file,
    )

    root = _workspace_root(workspace)
    local = resolve_local_html_file(raw, workspace=root)
    if local is not None and root is not None:
        return preview_http_url(local, root)
    if is_workspace_page_ref(raw):
        shown = (raw or "").strip()
        raise ValueError(
            f"{shown!r} is not an existing file in the workspace. "
            "browser_navigate opens an .html, .htm, or .pdf by its path "
            "relative to the workspace."
        )
    return ""


_IMAGE_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".avif",
)
_BARE_HOST_RE = re.compile(
    r"^(?:www\.)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
    r"(?::\d{2,5})?(?:[/?#].*)?$"
)
_LOCALHOST_RE = re.compile(
    r"^(?:localhost|127\.0\.0\.1)(?::\d{2,5})?(?:[/?#].*)?$",
    re.IGNORECASE,
)

def is_shallow_site_rewind(current: str, target: str) -> bool:
    """True when `target` looks like a site home and `current` is a deeper same-host page.

    Agents often "restart" by navigating back to https://example.com/ while already
    sitting on /search?q=... — that erases progress and loops. Detect it so the
    navigate path can refuse or warn instead of bouncing home.
    """
    cur = (current or "").strip()
    tgt = (target or "").strip()
    if not cur or not tgt:
        return False
    if cur in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}:
        return False
    try:
        pc, pt = urlparse(cur), urlparse(tgt)
    except Exception:
        return False
    if (pc.scheme or "").lower() not in ("http", "https"):
        return False
    if (pt.scheme or "").lower() not in ("http", "https"):
        return False
    ch = (pc.hostname or "").lower()
    th = (pt.hostname or "").lower()
    if ch.startswith("www."):
        ch = ch[4:]
    if th.startswith("www."):
        th = th[4:]
    if not ch or ch != th:
        return False
    cpath = pc.path or "/"
    tpath = pt.path or "/"
    if cpath != "/" and cpath.endswith("/"):
        cpath = cpath[:-1]
    if tpath != "/" and tpath.endswith("/"):
        tpath = tpath[:-1]
    # Target is site root (no meaningful path / query) while current is deeper.
    tgt_is_home = (tpath in ("", "/") and not (pt.query or "").strip())
    cur_is_deeper = (cpath not in ("", "/") or bool((pc.query or "").strip()))
    return bool(tgt_is_home and cur_is_deeper)


# Generic human-verification / captcha cues (site-agnostic).
_CHALLENGE_CUES = (
    "验证码",
    "安全验证",
    "人机验证",
    "请完成验证",
    "滑动验证",
    "点击验证",
    "身份验证",
    "unusual traffic",
    "verify you are human",
    "are you a robot",
    "security check",
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "just a moment",
    "attention required",
    "access denied",
    "机器人检测",
)

_CHALLENGE_JS = r"""(() => {
  const cues = __CUES__;
  const url = String(location.href || "");
  const title = String(document.title || "");
  const body = (document.body && (document.body.innerText || document.body.textContent || "")) || "";
  const sample = (url + "\n" + title + "\n" + body.slice(0, 5000)).toLowerCase();
  const hit = [];
  for (const c of cues) {
    if (c && sample.indexOf(String(c).toLowerCase()) >= 0) hit.push(c);
  }
  let iframeHit = false;
  try {
    const iframes = Array.from(document.querySelectorAll("iframe[src]"));
    for (const f of iframes) {
      const src = String(f.getAttribute("src") || "").toLowerCase();
      if (!src) continue;
      if (src.indexOf("captcha") >= 0 || src.indexOf("recaptcha") >= 0 ||
          src.indexOf("hcaptcha") >= 0 || src.indexOf("geetest") >= 0 ||
          src.indexOf("challenge") >= 0) {
        iframeHit = true;
        break;
      }
    }
  } catch (e) {}
  let widget = false;
  try {
    widget = !!(document.querySelector(
      "[class*='captcha' i], [id*='captcha' i], .g-recaptcha, .h-captcha, #captcha, #verify"
    ));
  } catch (e) {}
  const challenged = hit.length > 0 || iframeHit || widget;
  return {
    challenged,
    reasons: hit.slice(0, 6),
    iframe: iframeHit,
    widget,
    url,
    title: title.slice(0, 120),
  };
})()"""


def challenge_js_source() -> str:
    import json as _json
    return _CHALLENGE_JS.replace("__CUES__", _json.dumps(list(_CHALLENGE_CUES), ensure_ascii=False))



def urls_match(left: str, right: str) -> bool:
    """True if two http(s) URLs point at the same document (ignore www / slash)."""

    def norm(raw: str) -> str:
        text = (raw or "").strip()
        if not text or text == "about:blank":
            return text
        try:
            parsed = urlparse(text)
        except Exception:
            return text.rstrip("/")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        scheme = (parsed.scheme or "https").lower()
        query = parsed.query
        return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")

    a, b = norm(left), norm(right)
    return bool(a and b and a == b)


def _looks_like_asset_filename(text: str) -> bool:
    stem = (text or "").split("?")[0].split("#")[0].strip().replace("\\", "/")
    if not stem or "://" in stem:
        return False
    lower = stem.lower()
    return any(lower.endswith(ext) for ext in _IMAGE_SUFFIXES)


def lookup_screenshot_page_url(raw: str, workspace: Optional[Path] = None) -> str:
    """If ``raw`` is a saved screenshot, return the page URL it was taken from."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    last = ""
    try:
        from .browser_sandbox import SANDBOX as _sandbox

        last = str(_sandbox.last_url() or "").strip()
    except Exception:
        last = ""
    candidates: list[Path] = []
    root: Optional[Path] = workspace
    if root is None:
        try:
            from .workspace_store import get_active_workspace

            ws = get_active_workspace()
            root_s = str(ws.get("path") or "").strip() if ws.get("configured") else ""
            root = Path(root_s) if root_s else None
        except Exception:
            root = None
    name = Path(text).name
    if root is not None:
        candidates.append(root / text)
        candidates.append(root / ".sidekick" / "browser" / name)
    candidates.append(Path(text))
    for cand in candidates:
        meta = Path(str(cand) + ".json")
        if not meta.is_file():
            meta = cand.with_suffix(cand.suffix + ".json") if cand.suffix else cand
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        url = sanitize_browser_url(str((data or {}).get("url") or ""))
        if url:
            return url
    if root is not None:
        latest = root / ".sidekick" / "browser" / "latest.json"
        if latest.is_file():
            try:
                data = json.loads(latest.read_text(encoding="utf-8"))
                url = sanitize_browser_url(str((data or {}).get("url") or ""))
                if url:
                    return url
            except Exception:
                pass
    if last and last not in {"about:blank", "chrome://newtab/"}:
        return last
    return ""


_SCRIPT_URL_RE = re.compile(r"^(?:javascript|vbscript|data)\s*:", re.IGNORECASE)

# How long a browser tool may block while the user answers a dialog or
# security check. The model must not plan the next step until this returns.
_HUMAN_CONFIRM_S = 180.0
_USER_WAIT_TIMEOUT = 60.0 + (_HUMAN_CONFIRM_S * 2)

# Runs in the page before site scripts. Records alert/confirm/prompt and
# still calls the native dialog, so the user clicks the real prompt and the
# tool can read what they chose after it closes.
_DIALOG_HOOK_JS = r"""
(() => {
  if (window.__sidekickDialogHook) return;
  window.__sidekickDialogHook = true;
  const log = [];
  window.__sidekickDialogLog = log;
  window.__sidekickDialogOpen = false;
  const nativeAlert = window.alert.bind(window);
  const nativeConfirm = window.confirm.bind(window);
  const nativePrompt = window.prompt.bind(window);
  function arm(entry) {
    log.push(entry);
    if (log.length > 20) log.shift();
    window.__sidekickDialogOpen = true;
  }
  function disarm() {
    window.__sidekickDialogOpen = log.some((e) => e && e.open);
  }
  window.alert = function (message) {
    const entry = { type: "alert", message: String(message ?? ""), open: true };
    arm(entry);
    try { nativeAlert(message); entry.accepted = true; }
    finally { entry.open = false; disarm(); }
  };
  window.confirm = function (message) {
    const entry = { type: "confirm", message: String(message ?? ""), open: true };
    arm(entry);
    let accepted = false;
    try { accepted = !!nativeConfirm(message); entry.accepted = accepted; }
    finally { entry.open = false; disarm(); }
    return accepted;
  };
  window.prompt = function (message, defaultValue) {
    const entry = {
      type: "prompt",
      message: String(message ?? ""),
      default: String(defaultValue ?? ""),
      open: true,
    };
    arm(entry);
    let value = null;
    try {
      value = nativePrompt(message, defaultValue);
      entry.value = value;
      entry.accepted = value !== null;
    } finally { entry.open = false; disarm(); }
    return value;
  };
})();
"""


def script_navigation_error(raw: str) -> str:
    """Reject script URLs. browser_navigate opens pages; it does not run them."""
    text = (raw or "").strip().strip("'\"")
    if not text or not _SCRIPT_URL_RE.match(text):
        return ""
    return (
        "browser_navigate only opens a page. It does not run javascript:, data:, "
        "or vbscript: URLs. To change the page, call browser_observe, then "
        "browser_input on each field index, then browser_click the control that "
        "submits or confirms. Do not put a script in url."
    )


def dialog_interrupted(exc: BaseException) -> bool:
    """Playwright aborts the in-flight click as soon as a JS dialog opens."""
    text = str(exc).lower()
    return (
        "javascript dialog" in text
        or "dialog interrupted" in text
        or "beforeunload" in text
    )


def dialog_result_note(items: list[dict[str, Any]]) -> str:
    """Tell the model whether the user has actually answered."""
    if not items:
        return ""
    if any(it.get("open") for it in items):
        return (
            "浏览器弹窗还开着（alert、confirm、prompt、离开页面或选择文件）。"
            "用户还没有确认。不要调用下一个工具，也不要开始下一步思考。"
        )
    bits: list[str] = []
    for it in items:
        kind = str(it.get("type") or "dialog")
        msg = " ".join(str(it.get("message") or "").split())[:160]
        accepted = it.get("accepted")
        if kind == "file":
            choice = "已选择文件" if accepted else "取消了文件选择"
        elif accepted is True:
            choice = "点了确定"
        elif accepted is False:
            choice = "点了取消"
        else:
            choice = "已关闭"
        bits.append(f"{kind} {choice}" + (f"（{msg}）" if msg else ""))
    return (
        "用户已经处理完浏览器弹窗："
        + "；".join(bits)
        + "。按这个结果继续，不要再当成没点过。"
    )


def release_unblocked_dialogs(
    items: list[dict[str, Any]], *, page_ready: bool, url: str
) -> None:
    """A responsive page means alert/confirm/beforeunload already closed.

    A file chooser does not freeze the page, so those stay open until the
    file input fires change or cancel.
    """
    if not page_ready:
        return
    for item in items:
        if not item.get("open") or item.get("type") == "file":
            continue
        if item.get("type") == "beforeunload" and item.get("accepted") is None:
            before = str(item.get("_url") or "")
            item["accepted"] = bool(url and before and url != before)
        item["open"] = False


def merge_dialog_log(items: list[dict[str, Any]], log: list[Any]) -> None:
    """Copy accepted/value from the in-page hook onto matching dialog records."""
    entries = [e for e in log if isinstance(e, dict)]
    used: set[int] = set()
    for item in items:
        if item.get("type") in {"file", "beforeunload"}:
            continue
        for i, entry in enumerate(entries):
            if i in used:
                continue
            if str(entry.get("type") or "") != str(item.get("type") or ""):
                continue
            if str(entry.get("message") or "") != str(item.get("message") or ""):
                continue
            used.add(i)
            if "accepted" in entry:
                item["accepted"] = bool(entry.get("accepted"))
            if entry.get("value") is not None:
                item["value"] = entry.get("value")
            break


def public_dialog(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not str(k).startswith("_")}


def coerce_navigate_target(raw: str, workspace: Optional[Path] = None) -> str:
    """Turn a chat/tool argument into a navigable http(s) URL.

    Accepts full URLs, ``example.com``, ``localhost:5173``, or a workspace
    HTML file. Screenshot filenames like ``real_1_top.png`` recover the page
    they were taken from (sidecar / last session). If that fails they raise
    ValueError so the model can pass a real URL instead.
    """
    text = (raw or "").strip().strip("'\"")
    if not text:
        return ""
    script_err = script_navigation_error(text)
    if script_err:
        raise ValueError(script_err)
    if text == "about:blank":
        return text
    if _looks_like_asset_filename(text):
        recovered = lookup_screenshot_page_url(text)
        if recovered:
            return recovered
        raise ValueError(
            f"{text!r} is a screenshot path, not a web page. "
            "browser_navigate needs a full http(s) URL such as https://example.com "
            "(the page the shot was taken from). "
            "To inspect the image itself, call read_file on that path."
        )
    http = sanitize_browser_url(text)
    if http:
        return http
    if _LOCALHOST_RE.match(text):
        return sanitize_browser_url("http://" + text) or ("http://" + text)
    if _BARE_HOST_RE.match(text):
        host = text.split("/")[0].split("?")[0].split("#")[0]
        tld = host.rsplit(".", 1)[-1].lower()
        if tld not in {ext.lstrip(".") for ext in _IMAGE_SUFFIXES} | {
            "html",
            "htm",
            "js",
            "css",
            "json",
            "txt",
            "md",
        }:
            return sanitize_browser_url("https://" + text) or ("https://" + text)
    return resolve_browser_target(text, workspace=workspace)


_cdp_cached = ""


def discover_cdp_url() -> str:
    """Loopback CDP endpoint of the desktop BrowserView, if running."""
    global _cdp_cached
    if _cdp_cached:
        return _cdp_cached
    env = (os.getenv("SIDEKICK_CDP_URL") or "").strip()
    if env:
        _cdp_cached = env
        return env
    port = (os.getenv("SIDEKICK_CDP_PORT") or "8315").strip() or "8315"
    url = f"http://127.0.0.1:{port}"
    try:
        req = urllib.request.Request(f"{url}/json/version")
        with urllib.request.urlopen(req, timeout=0.35) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        browser = str(data.get("Browser") or "")
        if "Electron" in browser or "Chrome" in browser:
            _cdp_cached = url
            return url
    except Exception:
        pass
    return ""


def _is_workbench_url(url: str) -> bool:
    u = (url or "").lower()
    prefixes = []
    ui = (os.getenv("SIDEKICK_UI_URL") or "").strip().rstrip("/").lower()
    if ui:
        prefixes.append(ui)
    port = os.getenv("META_PORT", "8787")
    prefixes.extend(
        [
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            "http://127.0.0.1:5177",
            "http://localhost:5177",
            "http://127.0.0.1:8787",
            "http://localhost:8787",
        ]
    )
    return any(u.startswith(p) for p in prefixes)


async def _find_guest_page(browser: Any) -> Any:
    """Locate the in-app BrowserView page; never the workbench renderer."""
    deadline = time.monotonic() + 8.0
    fallback = None
    while time.monotonic() < deadline:
        for ctx in list(getattr(browser, "contexts", None) or []):
            for page in list(getattr(ctx, "pages", None) or []):
                url = ""
                try:
                    url = page.url or ""
                except Exception:
                    continue
                if _is_workbench_url(url):
                    continue
                try:
                    if await page.evaluate("() => Boolean(window.__sidekickGuest)"):
                        return page
                except Exception:
                    pass
                # about:blank / empty guest view is fine; the workbench is not.
                if fallback is None:
                    fallback = page
        await asyncio.sleep(0.15)
    if fallback is not None:
        try:
            url = fallback.url or ""
        except Exception:
            url = ""
        if _is_workbench_url(url):
            return None
    return fallback


def loopback_url_candidates(url: str) -> list[str]:
    """Try IPv4 and IPv6 loopback — Vite on Windows often binds only one family."""
    text = (url or "").strip()
    if not text or text == "about:blank":
        return [text] if text else []
    try:
        parsed = urlparse(text)
    except Exception:
        return [text]
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
        return [text]
    port = parsed.port
    out: list[str] = []
    seen: set[str] = set()

    def add(hostname: str) -> None:
        hostpart = f"[{hostname}]" if ":" in hostname else hostname
        netloc = f"{hostpart}:{port}" if port else hostpart
        cand = urlunparse(parsed._replace(netloc=netloc))
        if cand not in seen:
            seen.add(cand)
            out.append(cand)

    add("127.0.0.1" if host == "0.0.0.0" else host)
    for h in ("127.0.0.1", "localhost", "::1"):
        add(h)
    return out or [text]


def _is_connection_refused(exc: BaseException) -> bool:
    s = str(exc).lower()
    return (
        "err_connection_refused" in s
        or "econnrefused" in s
        or "connection refused" in s
    )


_SELECT_BOOTSTRAP = r"""
(() => {
  if (window.__sidekickSelectBooted) return true;
  window.__sidekickSelectBooted = true;

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function xpathFor(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return '//*[@id="' + el.id + '"]';
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && cur !== document.documentElement) {
      let i = 1;
      let sib = cur.previousElementSibling;
      while (sib) {
        if (sib.tagName === cur.tagName) i++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(cur.tagName.toLowerCase() + "[" + i + "]");
      cur = cur.parentElement;
    }
    return "/html/body/" + parts.join("/");
  }

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + cssEscape(el.id);
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur.nodeType === 1 && depth < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) {
        parts.unshift("#" + cssEscape(cur.id));
        break;
      }
      if (cur.classList && cur.classList.length) {
        part += "." + Array.from(cur.classList).slice(0, 2).map(cssEscape).join(".");
      }
      parts.unshift(part);
      cur = cur.parentElement;
      depth++;
    }
    return parts.join(" > ");
  }

  function reactHint(el) {
    const key = Object.keys(el).find(
      (k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$")
    );
    if (!key) return null;
    let fiber = el[key];
    for (let i = 0; i < 12 && fiber; i++) {
      const t = fiber.type;
      if (typeof t === "function") {
        const name = t.displayName || t.name;
        if (name && !name.startsWith("_")) {
          return { name, framework: "react", file_hint: "" };
        }
      }
      if (typeof t === "object" && t && t.displayName) {
        return { name: String(t.displayName), framework: "react", file_hint: "" };
      }
      fiber = fiber.return;
    }
    return null;
  }

  function attrsOf(el) {
    const out = {};
    if (!el || !el.attributes) return out;
    for (const a of Array.from(el.attributes).slice(0, 24)) {
      if (a.name === "class" || a.name === "style") continue;
      let v = a.value || "";
      if (v.length > 120) v = v.slice(0, 120) + "…";
      out[a.name] = v;
    }
    return out;
  }

  function stylesOf(el) {
    const cs = window.getComputedStyle(el);
    const keys = __STYLE_KEYS__;
    const out = {};
    for (const k of keys) {
      try { out[k] = cs[k]; } catch (e) {}
    }
    return out;
  }

  function payloadFor(el) {
    const r = el.getBoundingClientRect();
    const classes = el.classList ? Array.from(el.classList).slice(0, 12) : [];
    let outer = "";
    try {
      outer = (el.outerHTML || "").slice(0, 4000);
    } catch (e) {}
    return {
      kind: "dom-element",
      protocol_version: __PROTOCOL_VERSION__,
      url: location.href,
      tag: (el.tagName || "").toLowerCase(),
      xpath: xpathFor(el),
      css_path: cssPath(el),
      id: el.id || "",
      classes,
      attributes: attrsOf(el),
      inner_text: (el.innerText || "").trim().slice(0, 800),
      role: el.getAttribute("role") || "",
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      computed_styles: stylesOf(el),
      component: reactHint(el),
      outer_html: outer,
      selected_at: Date.now() / 1000,
    };
  }

  let hl = null;
  let armed = false;
  let resolver = null;

  function ensureHl() {
    if (hl) return hl;
    hl = document.createElement("div");
    hl.setAttribute("data-sidekick-select-hl", "1");
    Object.assign(hl.style, {
      position: "fixed",
      pointerEvents: "none",
      zIndex: "2147483646",
      border: "2px solid #2563eb",
      background: "rgba(37,99,235,0.12)",
      borderRadius: "2px",
      display: "none",
    });
    document.documentElement.appendChild(hl);
    return hl;
  }

  function onMove(ev) {
    if (!armed) return;
    const el = document.elementFromPoint(ev.clientX, ev.clientY);
    if (!el || el === hl) return;
    const r = el.getBoundingClientRect();
    const box = ensureHl();
    box.style.display = "block";
    box.style.left = r.x + "px";
    box.style.top = r.y + "px";
    box.style.width = Math.max(0, r.width) + "px";
    box.style.height = Math.max(0, r.height) + "px";
  }

  function onClick(ev) {
    if (!armed) return;
    ev.preventDefault();
    ev.stopPropagation();
    const el = document.elementFromPoint(ev.clientX, ev.clientY);
    if (!el || el === hl) return;
    armed = false;
    if (hl) hl.style.display = "none";
    document.removeEventListener("mousemove", onMove, true);
    document.removeEventListener("click", onClick, true);
    const payload = payloadFor(el);
    const r = resolver;
    resolver = null;
    if (r) r(payload);
  }

  window.__sidekickSelectArm = function (timeoutMs) {
    return new Promise((resolve) => {
      if (armed && resolver) {
        resolver(null);
      }
      armed = true;
      resolver = resolve;
      ensureHl();
      document.addEventListener("mousemove", onMove, true);
      document.addEventListener("click", onClick, true);
      const ms = Math.max(1000, Number(timeoutMs) || 60000);
      setTimeout(() => {
        if (!armed) return;
        armed = false;
        if (hl) hl.style.display = "none";
        document.removeEventListener("mousemove", onMove, true);
        document.removeEventListener("click", onClick, true);
        const r = resolver;
        resolver = null;
        if (r) r(null);
      }, ms);
    });
  };

  window.__sidekickSelectCancel = function () {
    armed = false;
    if (hl) hl.style.display = "none";
    document.removeEventListener("mousemove", onMove, true);
    document.removeEventListener("click", onClick, true);
    const r = resolver;
    resolver = null;
    if (r) r(null);
  };

  return true;
})()
""".replace("__STYLE_KEYS__", json.dumps(list(STYLE_KEYS))).replace(
    "__PROTOCOL_VERSION__", str(PROTOCOL_VERSION)
)


# Enumerate clickable/typeable elements so the agent can act without guessing
# selectors from raw HTML. Mirrors the cssPath() logic in _SELECT_BOOTSTRAP but
# is self-contained (evaluated standalone, not part of the Select Mode bundle).
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

# Blind-model friendly page snapshot: enumerate visible interactive elements
# in DOM order, tag each with a stable data-bk-ref attribute and return a
# compact structured list so a text-only model can act by ref (no vision).
_PCT_INCOMPLETE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _fix_url_encoding(url: str) -> str:
    """Legalize model-built URLs (e.g. half-encoded queries like ?wd=人%E...).

    An incomplete percent escape breaks navigation or silently truncates the
    search term. Escape stray '%' as '%25' and percent-encode raw non-ASCII
    characters, leaving the rest of the URL untouched.
    """
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return u
    if "%" in u:
        u = _PCT_INCOMPLETE_RE.sub("%25", u)
    if any(ord(c) > 127 for c in u):
        u = "".join(quote(ch) if ord(ch) > 127 else ch for ch in u)
    return u


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
