"""Browser URL sanitize / coerce / match helpers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse, urlunparse

_URL_EXTRACT_RE = re.compile(
    r"https?://[A-Za-z0-9][-A-Za-z0-9._~:/?#\[\]@!$&'()+,;=%]*",
    re.IGNORECASE,
)

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

_SCRIPT_URL_RE = re.compile(r"^(?:javascript|vbscript|data)\s*:", re.IGNORECASE)

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

def _is_connection_refused(exc: BaseException) -> bool:
    s = str(exc).lower()
    return (
        "err_connection_refused" in s
        or "econnrefused" in s
        or "connection refused" in s
    )

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

def _is_unsafe_port_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    s = str(exc).lower()
    return "err_unsafe_port" in s or "unsafe_port" in s or "unsafe port" in s

def _looks_like_asset_filename(text: str) -> bool:
    stem = (text or "").split("?")[0].split("#")[0].strip().replace("\\", "/")
    if not stem or "://" in stem:
        return False
    lower = stem.lower()
    return any(lower.endswith(ext) for ext in _IMAGE_SUFFIXES)

def _workspace_root(explicit: Optional[Path]) -> Optional[Path]:
    if explicit is not None:
        try:
            root = Path(explicit)
            if str(root) and root.is_dir():
                return root
        except OSError:
            pass
    from ...workspace.workspace_store import get_active_workspace

    ws = get_active_workspace()
    root_s = str(ws.get("path") or "").strip() if ws.get("configured") else ""
    if not root_s:
        return None
    root = Path(root_s)
    return root if root.is_dir() else None

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

def is_chromium_unsafe_port(port: int | None) -> bool:
    """True when Chromium will refuse to navigate to this TCP port."""
    if port is None:
        return False
    try:
        return int(port) in _CHROMIUM_UNSAFE_PORTS
    except (TypeError, ValueError):
        return False

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

def lookup_screenshot_page_url(raw: str, workspace: Optional[Path] = None) -> str:
    """If ``raw`` is a saved screenshot, return the page URL it was taken from."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    last = ""
    try:
        from ..session.sandbox import SANDBOX
        last = str(SANDBOX.last_url() or "").strip()
    except Exception:
        last = ""
    candidates: list[Path] = []
    root: Optional[Path] = workspace
    if root is None:
        try:
            from ...workspace.workspace_store import get_active_workspace

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

def resolve_browser_target(raw: str, workspace: Optional[Path] = None) -> str:
    """http(s) URL, or local workspace HTML served as http://127.0.0.1/..."""
    http = sanitize_browser_url(raw)
    if http:
        return http
    from ..session.preview import (
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

def url_has_chromium_unsafe_port(url: str) -> bool:
    return is_chromium_unsafe_port(url_port(url))

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

