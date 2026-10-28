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
import queue
import re
import threading
import time
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass, field
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

HOST_KIND = "cdp_playwright"

# True inside 会话版 (social) chat turns. The agent's browser there is
# headless and auto-closed at turn end; the workbench keeps the classic
# visible-window behaviour.
SOCIAL_BROWSING: ContextVar[bool] = ContextVar("sidekick_social_browsing", default=False)

# A standalone browser window auto-closes after this much inactivity, so the
# agent never leaves one open behind it.
SESSION_IDLE_SECONDS = 300.0
_REAP_INTERVAL_SECONDS = 60.0

# Extract clean http(s) URL; strip markdown/CJK glued by chat models.
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



_FILLABLE_TAGS = frozenset({"input", "textarea", "select"})
# Align with Playwright fill(): native fields, contenteditable, and roles that
# participate in the editable/ARIA-readonly contract (no product-specific cases).
_FILLABLE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})

_PROBE_FILLABLE_JS = """(sel) => {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el || !el.tagName) return { found: false };
  const tag = String(el.tagName || "").toLowerCase();
  const role = String(el.getAttribute("role") || "").toLowerCase();
  const ceAttr = String(el.getAttribute("contenteditable") || "").toLowerCase();
  const ce = !!(el.isContentEditable || ceAttr === "true");
  const fillable =
    ["input", "textarea", "select"].includes(tag) ||
    ce ||
    ["textbox", "searchbox", "combobox", "spinbutton"].includes(role);
  return {
    found: true,
    tag,
    role,
    contenteditable: ce,
    fillable,
    disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true"),
    readonly: !!(el.readOnly || el.getAttribute("aria-readonly") === "true"),
  };
}"""


def element_is_fillable(
    *,
    tag: str = "",
    role: str = "",
    contenteditable: bool = False,
) -> bool:
    """True if the element matches Playwright's fill() editability contract."""
    t = (tag or "").strip().lower()
    r = (role or "").strip().lower()
    if t in _FILLABLE_TAGS:
        return True
    if contenteditable:
        return True
    if r in _FILLABLE_ROLES:
        return True
    return False


def non_fillable_input_error(
    *,
    tag: str = "",
    role: str = "",
    index: int | None = None,
    selector: str = "",
) -> str:
    """Structured guidance when browser_input targets a non-editable element."""
    t = (tag or "").strip().lower() or "unknown"
    r = (role or "").strip().lower()
    where = ""
    if index is not None:
        where = f" index={int(index)}"
    elif (selector or "").strip():
        where = f" selector={selector.strip()!r}"
    role_bit = f" role={r}" if r else ""
    clickish = t in {"a", "button", "summary", "option"} or r in {
        "link",
        "button",
        "tab",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "checkbox",
        "radio",
        "switch",
        "option",
    }
    if clickish:
        tip = (
            "This element is not a text field — use browser_click instead of "
            "browser_input/browser_type."
        )
    else:
        tip = (
            "browser_input only works on <input>, <textarea>, <select>, "
            "[contenteditable], or ARIA roles textbox/searchbox/combobox/spinbutton. "
            "Pick a fillable field from browser_observe, or use browser_click if "
            "the control is meant to be clicked."
        )
    return f"element is not editable{where}: <{t}>{role_bit}. {tip}"


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
        last = str(SANDBOX.last_url() or "").strip()
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


# Chromium refuses these ports (net::ERR_UNSAFE_PORT). Kept as a membership
# set derived from Chromium's port_util — not product rules. Agents often
# hit this on go_back when history points at a blocked local port.
_CHROMIUM_UNSAFE_PORTS = frozenset(
    {
        1,
        7,
        9,
        11,
        13,
        15,
        17,
        19,
        20,
        21,
        22,
        23,
        25,
        37,
        42,
        43,
        53,
        69,
        77,
        79,
        87,
        95,
        101,
        102,
        103,
        104,
        109,
        110,
        111,
        113,
        115,
        117,
        119,
        123,
        135,
        137,
        139,
        143,
        161,
        179,
        389,
        427,
        465,
        512,
        513,
        514,
        515,
        526,
        530,
        531,
        532,
        540,
        548,
        554,
        555,
        556,
        563,
        587,
        601,
        636,
        989,
        990,
        993,
        995,
        1719,
        1720,
        1723,
        2049,
        3659,
        4045,
        5060,
        5061,
        6000,
        6566,
        6665,
        6666,
        6667,
        6668,
        6669,
        6697,
        10080,
    }
)


def url_port(url: str) -> int | None:
    """Return the explicit URL port, or None when absent/unparseable."""
    text = (url or "").strip()
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    return parsed.port


def is_chromium_unsafe_port(port: int | None) -> bool:
    """True when Chromium will refuse to navigate to this TCP port."""
    if port is None:
        return False
    try:
        return int(port) in _CHROMIUM_UNSAFE_PORTS
    except (TypeError, ValueError):
        return False


def url_has_chromium_unsafe_port(url: str) -> bool:
    return is_chromium_unsafe_port(url_port(url))


def _is_connection_refused(exc: BaseException) -> bool:
    s = str(exc).lower()
    return (
        "err_connection_refused" in s
        or "econnrefused" in s
        or "connection refused" in s
    )


def _is_unsafe_port_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    s = str(exc).lower()
    return "err_unsafe_port" in s or "unsafe_port" in s or "unsafe port" in s


def _is_history_nav_error(exc: BaseException | None) -> bool:
    """Navigation failures that should fall back to session history, not raise."""
    if exc is None:
        return False
    if _is_unsafe_port_error(exc) or _is_connection_refused(exc):
        return True
    s = str(exc).lower()
    return (
        "net::err_" in s
        or "page.goto" in s
        or "page.go_back" in s
        or "page.go_forward" in s
        or "timeout" in s
        or "navigation" in s
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


@dataclass
class _Job:
    factory: Callable[[], Awaitable[Any]]
    timeout: float = 120.0
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None


class _PlaywrightThread:
    """Dedicated thread: own asyncio loop + one async Playwright instance."""

    def __init__(self) -> None:
        self._q: queue.Queue[Optional[_Job]] = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="sidekick-playwright", daemon=True
        )
        self._started = False
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._pw: Any = None
        self._boot_error: Optional[BaseException] = None

    @property
    def playwright(self) -> Any:
        return self._pw

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._thread.start()
            self._started = True
        if not self._ready.wait(timeout=60.0):
            raise TimeoutError("browser sandbox worker failed to start")
        if self._boot_error is not None:
            raise RuntimeError(str(self._boot_error)) from self._boot_error

    def revive(self) -> None:
        """Abandon a wedged worker thread and start a fresh one in-place.

        Playwright ops can block the dedicated loop in a non-cancellable C call;
        when that happens `call` times out and the old daemon thread is left
        to die on process exit. New work must not queue behind it.
        """
        with self._start_lock:
            try:
                self._q.put_nowait(None)
            except Exception:
                pass
            self._q = queue.Queue()
            self._thread = threading.Thread(
                target=self._loop, name="sidekick-playwright", daemon=True
            )
            self._started = False
            self._ready = threading.Event()
            self._pw = None
            self._boot_error = None

    def call(self, factory: Callable[[], Awaitable[Any]], *, timeout: float = 120.0) -> Any:
        self.ensure_started()
        job = _Job(factory=factory, timeout=float(timeout))
        self._q.put(job)
        # The loop-side wait_for fires first and reports the real failure;
        # this grace period only catches a truly dead worker thread.
        if not job.done.wait(timeout=timeout + 10.0):
            self.revive()
            raise TimeoutError("browser sandbox worker timed out")
        if job.error is not None:
            raise RuntimeError(str(job.error)) from job.error
        return job.result

    def _loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        pw = None
        try:
            try:
                from playwright.async_api import async_playwright

                pw = loop.run_until_complete(async_playwright().start())
                self._pw = pw
            except BaseException as exc:  # noqa: BLE001
                self._boot_error = exc
                self._ready.set()
                return
            self._ready.set()
            while True:
                job = self._q.get()
                if job is None:
                    return
                try:
                    job.result = loop.run_until_complete(
                        asyncio.wait_for(job.factory(), timeout=job.timeout)
                    )
                except asyncio.TimeoutError:
                    # A hung op (launch/CDP connect/renderer wedged) must not
                    # poison the whole worker: cancel it and keep serving.
                    job.error = TimeoutError(
                        f"browser sandbox job timed out after {job.timeout:.0f}s"
                    )
                except BaseException as exc:  # noqa: BLE001
                    job.error = exc
                finally:
                    job.done.set()
        finally:
            self._pw = None
            if pw is not None:
                try:
                    loop.run_until_complete(pw.stop())
                except Exception:
                    pass
            try:
                loop.close()
            except Exception:
                pass


_WORKER = _PlaywrightThread()


class BrowserSandbox:
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
                rejected = await self._reject_if_not_fillable(
                    page, sel, action="fill"
                )
                if rejected is not None:
                    rejected["ref"] = ref
                    rejected["value"] = str(value)
                    return rejected
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
                rejected = await self._reject_if_not_fillable(
                    page, sel, action="type"
                )
                if rejected is not None:
                    return f"ERROR: {rejected['error']}"
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

    # ---- multi-tab management ----

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

    @staticmethod
    def _is_nav_aborted(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "err_aborted" in msg or "(-3)" in msg or "net::err_aborted" in msg

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

    def _dialog_state_for(self, page: Any) -> dict[str, Any]:
        states = getattr(self, "_dialog_states", None)
        if states is None:
            states = {}
            self._dialog_states = states
        key = id(page)
        state = states.get(key)
        if state is None:
            state = {"open": 0, "items": [], "armed": False, "hooked": False}
            states[key] = state
        return state

    def _dialog_pending(self, page: Any) -> bool:
        state = getattr(self, "_dialog_states", {}).get(id(page))
        if not state:
            return False
        return any(it.get("open") for it in state.get("items") or [])

    @staticmethod
    def _recount_dialogs(state: dict[str, Any]) -> int:
        n = sum(1 for it in state.get("items") or [] if it.get("open"))
        state["open"] = n
        return n

    async def _arm_user_dialogs(self, page: Any) -> None:
        """Own JS dialogs so Playwright does not dismiss them before the user clicks.

        With no listener, Playwright dismisses alert/confirm/prompt and accepts
        beforeunload immediately, then aborts the click. The native dialog can
        still be on screen while the model already plans the next step.
        """
        if page is None:
            return
        state = self._dialog_state_for(page)
        if not state.get("hooked"):
            try:
                await page.evaluate(_DIALOG_HOOK_JS)
                state["hooked"] = True
            except Exception:
                pass
        ctx = getattr(page, "context", None)
        if ctx is not None and not getattr(ctx, "_sidekick_dialog_init", False):
            try:
                await ctx.add_init_script(_DIALOG_HOOK_JS)
                setattr(ctx, "_sidekick_dialog_init", True)
            except Exception:
                pass
        if state.get("armed"):
            return
        state["armed"] = True

        def _on_dialog(dialog: Any) -> None:
            try:
                url = str(getattr(page, "url", "") or "")
            except Exception:
                url = ""
            try:
                kind = str(getattr(dialog, "type", "") or "")
                message = str(getattr(dialog, "message", "") or "")
            except Exception:
                kind, message = "dialog", ""
            state["items"].append(
                {
                    "type": kind or "dialog",
                    "message": message[:500],
                    "open": True,
                    "accepted": None,
                    "_dialog": dialog,
                    "_url": url,
                }
            )
            self._recount_dialogs(state)

        def _on_dialog_closed(*args: Any) -> None:
            dialog = args[0] if args else None
            matched = False
            for item in state["items"]:
                if (
                    item.get("open")
                    and item.get("type") != "file"
                    and item.get("_dialog") is dialog
                ):
                    item["open"] = False
                    matched = True
                    break
            if not matched:
                for item in state["items"]:
                    if item.get("open") and item.get("type") != "file":
                        item["open"] = False
                        break
            self._recount_dialogs(state)

        def _on_file(chooser: Any) -> None:
            item = {
                "type": "file",
                "message": "choose a file",
                "open": True,
                "accepted": None,
            }
            state["items"].append(item)
            self._recount_dialogs(state)

            async def _watch() -> None:
                how = "cancelled"
                try:
                    how = await asyncio.wait_for(
                        chooser.element.evaluate(
                            """el => new Promise((resolve) => {
                              let done = false;
                              const finish = (v) => { if (!done) { done = true; resolve(v); } };
                              el.addEventListener('change', () => finish('chosen'), {once: true});
                              el.addEventListener('cancel', () => finish('cancelled'), {once: true});
                            })"""
                        ),
                        timeout=_HUMAN_CONFIRM_S,
                    )
                except Exception:
                    how = "closed"
                item["open"] = False
                item["accepted"] = how == "chosen"
                item["value"] = how
                self._recount_dialogs(state)

            try:
                asyncio.get_running_loop().create_task(_watch())
            except Exception:
                item["open"] = False
                self._recount_dialogs(state)

        def _rehook(frame: Any = None) -> None:
            try:
                main = getattr(page, "main_frame", None)
                if frame is not None and main is not None and frame != main:
                    return
            except Exception:
                pass
            state["hooked"] = False

            async def _again() -> None:
                try:
                    await page.evaluate(_DIALOG_HOOK_JS)
                    state["hooked"] = True
                except Exception:
                    pass

            try:
                asyncio.get_running_loop().create_task(_again())
            except Exception:
                pass

        try:
            page.on("dialog", _on_dialog)
            page.on("dialogclosed", _on_dialog_closed)
            page.on("filechooser", _on_file)
            page.on("framenavigated", _rehook)
            page.on("close", lambda *_a: getattr(self, "_dialog_states", {}).pop(id(page), None))
        except Exception:
            state["armed"] = False

    async def _wait_user_dialogs(self, page: Any) -> None:
        """Block until the user closes the dialog, or the human wait expires."""
        deadline = time.monotonic() + _HUMAN_CONFIRM_S
        while self._dialog_pending(page) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if not self._dialog_pending(page):
                break
            try:
                await asyncio.wait_for(page.evaluate("() => 1"), timeout=1.0)
            except Exception:
                continue
            try:
                url = str(getattr(page, "url", "") or "")
            except Exception:
                url = ""
            release_unblocked_dialogs(
                self._dialog_state_for(page)["items"], page_ready=True, url=url
            )
            self._recount_dialogs(self._dialog_state_for(page))
        await self._merge_hook_log(page)

    async def _merge_hook_log(self, page: Any) -> None:
        try:
            raw = await asyncio.wait_for(
                page.evaluate(
                    """() => {
                      const log = window.__sidekickDialogLog;
                      if (!Array.isArray(log) || !log.length) return [];
                      return log.splice(0, log.length);
                    }"""
                ),
                timeout=2.0,
            )
        except Exception:
            return
        if isinstance(raw, list):
            merge_dialog_log(self._dialog_state_for(page)["items"], raw)

    def _collect_dialogs(self, page: Any) -> list[dict[str, Any]]:
        state = self._dialog_state_for(page)
        items = list(state.get("items") or [])
        closed = [it for it in items if not it.get("open")]
        state["items"] = [it for it in items if it.get("open")]
        self._recount_dialogs(state)
        published = [public_dialog(it) for it in closed]
        published.extend(public_dialog(it) for it in state["items"])
        return published

    async def _await_user_gate(self, page: Any, sess: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Wait out dialogs and security checks before the tool result is delivered."""
        await self._arm_user_dialogs(page)
        await asyncio.sleep(0)
        if self._dialog_pending(page):
            await self._wait_user_dialogs(page)
        dialogs = self._collect_dialogs(page)
        note = dialog_result_note(dialogs)
        still_open = any(d.get("open") for d in dialogs)
        challenge: dict[str, Any] = {"challenged": False}
        if not still_open:
            try:
                challenge = await self._probe_challenge(page)
            except Exception:
                challenge = {"challenged": False}
            if challenge.get("challenged"):
                deadline = time.monotonic() + _HUMAN_CONFIRM_S
                while challenge.get("challenged") and time.monotonic() < deadline:
                    await asyncio.sleep(1.2)
                    try:
                        challenge = await self._probe_challenge(page)
                    except Exception:
                        challenge = {"challenged": False}
                        break
        captcha_blocked = (not still_open) and bool(challenge.get("challenged"))
        # An open dialog must not set the session hold. That hold returns the
        # next click immediately, so the model thinks again while the prompt
        # is still up. Leave the dialog open in page state; the next action
        # waits in this same gate.
        if sess is not None and (captcha_blocked or not still_open):
            sess["needs_human"] = captcha_blocked
        needs_human = still_open or captcha_blocked
        if needs_human and not still_open:
            challenge_note = (
                "页面上的安全验证还在。用户还没有完成。"
                "不要调用下一个工具，等用户完成后再继续。"
            )
            note = f"{note} {challenge_note}".strip() if note else challenge_note
        return {
            "dialogs": dialogs,
            "note": note,
            "needs_human": needs_human,
            "challenge": challenge if challenge.get("challenged") else None,
        }

    def _apply_gate(self, sess: Optional[dict[str, Any]], info: dict[str, Any]) -> dict[str, Any]:
        if not sess or not isinstance(info, dict):
            return info
        gate = sess.pop("_last_gate", None) or {}
        if not gate:
            return info
        info = dict(info)
        if gate.get("dialogs"):
            info["dialogs"] = gate["dialogs"]
        if gate.get("needs_human"):
            info["needs_human"] = True
            if gate.get("challenge"):
                info["challenge"] = gate["challenge"]
        note = str(gate.get("note") or "").strip()
        if note:
            prev = str(info.get("note") or "").strip()
            info["note"] = f"{prev} {note}".strip() if prev else note
        return info

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


    # ------------------------------------------------------------------
    # Index-based observation / actions (browser-use style loop)
    # ------------------------------------------------------------------

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


    async def _probe_fillable(self, page: Any, sel: str) -> dict[str, Any]:
        """Inspect whether ``sel`` resolves to a Playwright-fillable element."""
        try:
            info = await page.evaluate(_PROBE_FILLABLE_JS, sel)
        except Exception:
            info = None
        if not isinstance(info, dict):
            return {"found": False, "fillable": False}
        return info

    async def _reject_if_not_fillable(
        self,
        page: Any,
        sel: str,
        *,
        index: int | None = None,
        action: str = "input",
    ) -> dict[str, Any] | None:
        """Return an error payload if the target cannot be filled; else None."""
        info = await self._probe_fillable(page, sel)
        if not info.get("found"):
            where = f" index={int(index)}" if index is not None else f" selector={sel!r}"
            return {
                "ok": False,
                "action": action,
                "error": (
                    f"element not found{where}. Re-run browser_observe and use a "
                    "fresh index for a fillable field."
                ),
                "index": index,
                "selector": sel,
            }
        tag = str(info.get("tag") or "")
        role = str(info.get("role") or "")
        ce = bool(info.get("contenteditable"))
        if element_is_fillable(tag=tag, role=role, contenteditable=ce):
            return None
        err = non_fillable_input_error(tag=tag, role=role, index=index, selector=sel)
        out: dict[str, Any] = {
            "ok": False,
            "action": action,
            "error": err,
            "tag": tag,
            "role": role,
            "selector": sel,
        }
        if index is not None:
            out["index"] = int(index)
        return out

    def _index_selector(self, sess: dict[str, Any], index: int) -> str:
        amap = sess.get("index_map") or {}
        if int(index) in amap:
            return str(amap[int(index)])
        return f'[{_bobs.IDX_ATTR}="{int(index)}"]'

    def _agent_hold(self, uid: str, index: int | None = None) -> dict[str, Any] | None:
        """Refuse agent clicks while a person must finish, or the same click is stuck."""
        from .browser_queue import human_hold_result, repeated_click_result, skip_repeated_click

        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return None
            url = str(sess.get("url") or "")
            if sess.get("needs_human"):
                return human_hold_result(url)
            if index is not None and skip_repeated_click(sess.get("last_click"), index):
                return repeated_click_result(url, index)
        return None

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

    def input_index(
        self,
        index: int,
        text: str,
        *,
        submit: bool = False,
        clear: bool = True,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid)
        if held:
            return held

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._index_selector(sess, int(index))
            await self._highlight_index(page, int(index))
            try:
                rejected = await self._reject_if_not_fillable(
                    page, sel, index=int(index), action="input"
                )
                if rejected is not None:
                    return rejected
                loc = page.locator(sel).first
                if clear:
                    try:
                        await loc.fill("")
                    except Exception:
                        pass
                await loc.fill(str(text), timeout=15000)
                if submit:
                    try:
                        await loc.press("Enter")
                    except Exception:
                        await page.keyboard.press("Enter")
            finally:
                await self._clear_highlight(page)
            return {
                "action": "input",
                "index": int(index),
                "text": str(text),
                "submit": bool(submit),
            }

        return self._run_action(uid, _do, observe_after=True)

    def scroll_to_text(self, text: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        needle = (text or "").strip()
        if not needle:
            raise ValueError("empty text")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            raw = await page.evaluate(_bobs.scroll_to_text_js(), needle)
            return {"action": "scroll_to_text", "text": needle, **dict(raw or {})}

        return self._run_action(uid, _do, observe_after=True)

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

    def send_keys(
        self, keys: str, *, user_id: Optional[str] = None, index: Optional[int] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        key = (keys or "").strip()
        if not key:
            raise ValueError("empty keys")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            if index is not None:
                sel = self._index_selector(sess, int(index))
                await self._highlight_index(page, int(index))
                try:
                    await page.locator(sel).first.focus(timeout=8000)
                finally:
                    await self._clear_highlight(page)
            await page.keyboard.press(key)
            return {"action": "send_keys", "keys": key, "index": index}

        return self._run_action(uid, _do, observe_after=True)



SANDBOX = BrowserSandbox()
