"""Navigate / document error hints for browser tools."""

from __future__ import annotations

import json

_DOCUMENT_EXTS = {
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods",
    ".epub", ".rtf", ".pdf", ".txt", ".md", ".markdown", ".csv", ".json",
    ".yaml", ".yml", ".xml", ".log",
}


def _local_document_hint(target: str) -> str:
    """Steer the model to read_file when it hands a document name to the browser."""
    low = target.lower().strip().rstrip("/")
    if "://" in low or low.startswith("localhost"):
        return ""
    for ext in _DOCUMENT_EXTS:
        if low.endswith(ext):
            return (
                f"ERROR: '{target}' is a local {ext} document, not a web page. "
                "browser_navigate only opens http(s) URLs or workspace HTML files. "
                f"Use read_file(path={target!r}) to get its text "
                "(docx/pptx/xlsx/pdf are converted automatically), or search_text "
                "to locate it. Do not retry browser_navigate for this file."
            )
    return ""


def _is_connection_refused_text(text: str) -> bool:
    low = (text or "").lower()
    return (
        "err_connection_refused" in low
        or "connection refused" in low
        or "econnrefused" in low
        or "net::err_connection_refused" in low
    )


def _is_unsafe_port_text(text: str) -> bool:
    low = (text or "").lower()
    return "err_unsafe_port" in low or "unsafe_port" in low or "unsafe port" in low


def _is_loopback_url(url: str) -> bool:
    low = (url or "").strip().lower()
    if not low:
        return False
    if "://127.0.0.1" in low or "://localhost" in low or "://[::1]" in low:
        return True
    if low.startswith("127.0.0.1:") or low.startswith("localhost:"):
        return True
    return False


def _connection_refused_hint(url: str) -> str:
    """Actionable next steps — never tell the model to stop the task."""
    if _is_loopback_url(url):
        return (
            " Nothing is accepting connections on that host/port. "
            "Do NOT stop the task. Next steps: start or restart the static/dev "
            "server for the workspace file (bind 127.0.0.1), confirm the listening "
            "port, then retry browser_navigate; or open a workspace .html by its "
            "relative path; try localhost vs 127.0.0.1 if one refused."
        )
    return (
        " Do NOT stop the task. Change approach: verify the URL, try an alternate "
        "host/port, start a required local server, or diagnose with run_shell / read_file."
    )


def _unsafe_port_hint(url: str = "") -> str:
    """Chromium blocks some ports; tell the agent how to recover without retrying."""
    return (
        " Chromium refused this port (net::ERR_UNSAFE_PORT). "
        "Do NOT retry the same URL or browser_go_back to it. "
        "Do NOT stop the task. Next: pick a safe port (e.g. 5173, 8080, 3000), "
        "rebind the local server, then browser_navigate to the new URL"
        + (f" (was {url})." if url else ".")
    )


def _navigate_failure_message(exc_or_note: str, url: str) -> str:
    detail = str(exc_or_note or "").strip() or "page load failed"
    if _is_unsafe_port_text(detail):
        hint = _unsafe_port_hint(url)
    elif _is_connection_refused_text(detail) or _is_loopback_url(url):
        hint = _connection_refused_hint(url)
    else:
        hint = (
            " Do NOT stop the task. Change URL/approach or diagnose; do not repeat "
            "the identical browser_navigate call."
        )
    return f"ERROR: page load failed: {detail}.{hint}"


def _navigate_info_failure(info: dict, url: str) -> str:
    """Turn deferred/chrome-error navigate payloads into a clear recoverable ERROR."""
    if not isinstance(info, dict):
        return ""
    blob = ""
    try:
        blob = json.dumps(info, ensure_ascii=False)
    except Exception:
        blob = str(info)
    low = blob.lower()
    note = str(info.get("note") or "")
    landed = str(info.get("url") or "")
    refused = _is_connection_refused_text(note) or _is_connection_refused_text(blob)
    chrome_err = "chrome-error://" in low or "chromewebdata" in low
    ready_false = info.get("ready") is False or info.get("deferred") is True
    if refused or chrome_err or (ready_false and refused):
        detail = note or landed or "ERR_CONNECTION_REFUSED / chrome-error"
        return _navigate_failure_message(detail, url or landed)
    if ready_false and _is_loopback_url(url or landed) and note:
        # Playwright deferred with a note — treat as load failure for loopback.
        if any(k in low for k in ("timeout", "refused", "closed", "failed", "err_")):
            return _navigate_failure_message(note, url or landed)
    return ""
