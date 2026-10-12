"""CDP browser sandbox tools (navigate / screenshot / click / type)."""

from __future__ import annotations

import json

from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext

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


def register_browser_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    """Register browser tools: observe → act-by-index → re-observe."""
    live_ws = ctx.live_ws

    def _obs_json(payload: dict) -> str:
        # Prefer compact observation text; keep structured fields for debugging.
        return json.dumps(payload, ensure_ascii=False)

    def browser_navigate(url: str = "", address: str = "", href: str = "") -> str:
        from ...core.hostinfo import network_available
        from ...core.netguard import blocked_http_reason
        from ...services.browser_preview import is_preview_http_url
        from ...services.browser_sandbox import (
            SANDBOX,
            coerce_navigate_target,
            urls_match,
        )

        target = (url or address or href or "").strip()
        if not target:
            return "ERROR: empty url"
        from ...services.browser_sandbox import _looks_like_asset_filename

        shot = _looks_like_asset_filename(target)
        try:
            resolved = coerce_navigate_target(target, workspace=live_ws())
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not resolved:
            doc_hint = _local_document_hint(target)
            if doc_hint:
                return doc_hint
            return (
                "ERROR: invalid url — browser_navigate needs a full http(s) URL "
                "(e.g. https://example.com), localhost:port, or a workspace HTML file. "
                "Do not pass javascript: or a script. Fill a form with browser_observe, "
                "browser_input, then browser_click."
            )
        if resolved != "about:blank" and not is_preview_http_url(resolved):
            reason = blocked_http_reason(resolved, allow_loopback=True)
            if reason:
                return (
                    f"ERROR: navigation blocked ({reason}). "
                    "Private networks are not allowed. Localhost dev servers open in the in-app browser."
                )
            low = resolved.lower()
            public_http = low.startswith("http://") or low.startswith("https://")
            if public_http and not network_available():
                return "ERROR: this host is offline; cannot open public URLs."
        current = SANDBOX.last_url()
        if current and urls_match(current, resolved):
            try:
                obs = SANDBOX.observe_page()
            except Exception:
                obs = {"url": current, "already": True}
            obs = dict(obs)
            obs["already"] = True
            obs["note"] = "already on this page — do not call browser_navigate again with the same url"
            if shot:
                obs["recovered_from"] = target
            return _obs_json(obs)
        try:
            info = SANDBOX.navigate(
                target, headless=False, agent_owned=False, workspace=live_ws()
            )
            info = dict(info or {})
            if shot:
                info["recovered_from"] = target
            # Always return a fresh observation after navigate.
            try:
                obs = SANDBOX.observe_page()
                info["observation"] = obs.get("observation")
                info["elements"] = obs.get("elements")
                info["scroll"] = obs.get("scroll")
                info["title"] = obs.get("title") or info.get("title")
                if obs.get("needs_human"):
                    info["needs_human"] = True
                    info["challenge"] = obs.get("challenge")
            except Exception as exc:  # noqa: BLE001
                info["observation_error"] = str(exc)
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_observe(limit: int = 40, highlight_all: bool = False) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return _obs_json(
                SANDBOX.observe_page(
                    limit=int(limit) if limit else 40,
                    highlight_all=bool(highlight_all),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    # Alias kept so older prompts/tests still resolve.
    def browser_snapshot(limit: int = 40) -> str:
        return browser_observe(limit=limit)

    def browser_click(index: int = -1, ref: str = "", selector: str = "") -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            if index is not None and int(index) >= 0:
                return _obs_json(SANDBOX.click_index(int(index)))
            raw = (ref or "").strip()
            if raw:
                return _obs_json(SANDBOX.click_ref(raw))
            if (selector or "").strip():
                return SANDBOX.click_selector(selector)
            return "ERROR: pass index from the latest observation (preferred), or ref/selector"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_input(
        index: int = -1,
        text: str = "",
        submit: bool = False,
        clear: bool = True,
        ref: str = "",
        selector: str = "",
    ) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            if index is not None and int(index) >= 0:
                return _obs_json(
                    SANDBOX.input_index(
                        int(index), text, submit=bool(submit), clear=bool(clear)
                    )
                )
            raw = (ref or "").strip()
            if raw:
                return _obs_json(SANDBOX.fill_ref(raw, text))
            if (selector or "").strip():
                return SANDBOX.type_text(selector, text, clear=bool(clear))
            return "ERROR: pass index from the latest observation (preferred)"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    # Back-compat alias
    def browser_type(
        index: int = -1,
        text: str = "",
        clear: bool = True,
        ref: str = "",
        selector: str = "",
    ) -> str:
        return browser_input(
            index=index, text=text, clear=clear, ref=ref, selector=selector
        )

    def browser_scroll(
        direction: str = "down",
        amount: int = 0,
        index: int = -1,
        selector: str = "",
        content: int = 0,
        pages: float = 0,
    ) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            sel = selector or ""
            if index is not None and int(index) >= 0 and not sel and not content:
                # Scroll a specific indexed element into view via its stamped attr.
                from ...services import browser_observe as _b

                sel = f'[{_b.IDX_ATTR}="{int(index)}"]'
            info = SANDBOX.scroll(
                direction=direction or "down",
                amount=int(amount) if amount else None,
                selector=sel,
                content=int(content) if content else None,
                pages=float(pages) if pages else None,
            )
            info = dict(info or {})
            try:
                obs = SANDBOX.observe_page()
                info["observation"] = obs.get("observation")
                info["elements"] = obs.get("elements")
                info["scroll"] = obs.get("scroll") or info.get("scroll")
            except Exception as exc:  # noqa: BLE001
                info["observation_error"] = str(exc)
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_scroll_to_text(text: str = "") -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return _obs_json(SANDBOX.scroll_to_text(text))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_extract_content(goal: str = "", max_chars: int = 12000) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return _obs_json(
                SANDBOX.extract_content(
                    goal=goal or "", max_chars=int(max_chars or 12000)
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_get_page_content(max_chars: int = 12000, include_html: bool = False) -> str:
        # Retained for older callers; prefer browser_extract_content.
        if not include_html:
            return browser_extract_content(max_chars=max_chars)
        from ...services.browser_sandbox import SANDBOX

        try:
            return _obs_json(
                SANDBOX.page_content(
                    max_chars=int(max_chars or 12000), include_html=True
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_send_keys(keys: str = "", index: int = -1) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            idx = int(index) if index is not None and int(index) >= 0 else None
            return _obs_json(SANDBOX.send_keys(keys, index=idx))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_press_key(key: str = "", index: int = -1, selector: str = "") -> str:
        if index is not None and int(index) >= 0:
            return browser_send_keys(keys=key, index=index)
        from ...services.browser_sandbox import SANDBOX

        try:
            return SANDBOX.press_key(key, selector=selector or "")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_go_back() -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            info = dict(SANDBOX.go_back() or {})
            try:
                obs = SANDBOX.observe_page()
                info["observation"] = obs.get("observation")
            except Exception:
                pass
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_go_forward() -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return _obs_json(SANDBOX.go_forward())
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_switch_tab(index: int = 0) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            info = dict(SANDBOX.switch_tab(int(index or 0)) or {})
            try:
                obs = SANDBOX.observe_page()
                info["observation"] = obs.get("observation")
                info["elements"] = obs.get("elements")
            except Exception as exc:  # noqa: BLE001
                info["observation_error"] = str(exc)
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_tabs(action: str = "list", index: int = 0) -> str:
        from ...services.browser_sandbox import SANDBOX

        act = (action or "list").strip().lower()
        try:
            if act == "list":
                return _obs_json(SANDBOX.list_tabs())
            if act == "switch":
                return browser_switch_tab(index=int(index or 0))
            if act == "close":
                return _obs_json(SANDBOX.close_tab(int(index or 0)))
            return f"ERROR: unknown action '{act}' — use list / switch / close"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_wait_for_user(timeout_ms: int = 180000) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            info = SANDBOX.wait_for_human(timeout_ms=int(timeout_ms or 180000))
            info = dict(info or {})
            if not info.get("needs_human"):
                try:
                    obs = SANDBOX.observe_page()
                    info["observation"] = obs.get("observation")
                    info["elements"] = obs.get("elements")
                except Exception:
                    pass
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_screenshot(full_page: bool = False, name: str = "") -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            path = SANDBOX.save_screenshot_to_workspace(
                live_ws(),
                name=(name or "").strip(),
                full_page=bool(full_page),
            )
            from ...core.pathutil import relative_to_posix

            rel = relative_to_posix(path, live_ws())
            return json.dumps({"path": rel, "abs": str(path)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_console(limit: int = 40) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            logs = SANDBOX.console_logs(limit=int(limit) if limit else 40)
            return json.dumps({"count": len(logs), "logs": logs}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_diagnostics(limit: int = 20) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return json.dumps(
                SANDBOX.diagnostics(limit=int(limit) if limit else 20),
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_wait(selector: str = "", state: str = "visible", timeout_ms: int = 8000) -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            return SANDBOX.wait_for(
                selector=selector or "",
                state=state or "visible",
                timeout_ms=int(timeout_ms or 8000),
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_select(index: int = -1, ref: str = "", selector: str = "", value: str = "") -> str:
        from ...services.browser_sandbox import SANDBOX

        try:
            if index is not None and int(index) >= 0:
                from ...services import browser_observe as _b

                sel = f'[{_b.IDX_ATTR}="{int(index)}"]'
                return _obs_json(SANDBOX.select_selector(sel, value))
            if (ref or "").strip():
                return _obs_json(SANDBOX.select_ref(ref.strip(), value))
            return _obs_json(SANDBOX.select_selector(selector, value))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    # ---- registrations ----
    reg.register(
        Tool(
            "browser_navigate",
            "Open a WEB PAGE in the in-app Browser panel. Pass url as a full http(s) URL, "
            "bare domain, localhost:port, or workspace HTML file. "
            "Never pass javascript:, data:, or a script that fills a form. "
            "To fill fields, use browser_observe, then browser_input, then browser_click. "
            "http://127.0.0.1:port opens in this panel. Do not launch the system browser.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "address": {"type": "string"},
                    "href": {"type": "string"},
                },
                "required": ["url"],
            },
            browser_navigate,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_observe",
            "Observe the current page: return URL, title, scroll position, open tabs, "
            "and a compact list of visible interactive elements as [index]<tag ...>label. "
            "Call this (or read the observation returned by every action) before acting. "
            "If the target is not listed, scroll then observe again.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 100},
                    "highlight_all": {
                        "type": "boolean",
                        "default": False,
                        "description": "Draw numbered boxes on every indexed element",
                    },
                },
                "required": [],
            },
            browser_observe,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_snapshot",
            "Alias of browser_observe (kept for compatibility).",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 100}},
                "required": [],
            },
            browser_snapshot,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_click",
            "Highlight then click an element by its observation index. "
            "Result includes a NEW observation of the page after the click. "
            "Prefer index; ref/selector are legacy fallbacks only.",
            {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Element index from the latest observation",
                    },
                    "ref": {"type": "string", "description": "Legacy ref e.g. e3"},
                    "selector": {"type": "string"},
                },
                "required": [],
            },
            browser_click,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_input",
            "Highlight then type into an input/textarea by observation index. "
            "Set submit=true to press Enter afterwards. Returns a new observation.",
            {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean", "default": False},
                    "clear": {"type": "boolean", "default": True},
                    "ref": {"type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["text"],
            },
            browser_input,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_type",
            "Alias of browser_input (legacy name).",
            {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "clear": {"type": "boolean", "default": True},
                    "ref": {"type": "string"},
                    "selector": {"type": "string"},
                },
                "required": ["text"],
            },
            browser_type,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_scroll",
            "Scroll one viewport and return a new observation. "
            "direction=down moves one screen down; direction=up moves one screen up. "
            "Call it again to go further. pages overrides the distance in viewports.",
            {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "integer",
                        "description": "Optional. Scroll the Nth in-document link into view.",
                    },
                    "pages": {
                        "type": "number",
                        "description": "Viewports to scroll in one call. Negative scrolls up.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "top", "bottom"],
                        "default": "down",
                    },
                    "amount": {"type": "integer"},
                    "index": {
                        "type": "integer",
                        "description": "Scroll this indexed element into view",
                    },
                    "selector": {"type": "string"},
                },
                "required": [],
            },
            browser_scroll,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_scroll_to_text",
            "Scroll until the given visible text is near the viewport center, "
            "then return a new observation.",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            browser_scroll_to_text,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_extract_content",
            "Read the main readable text of the current page (for answering or "
            "confirming the page has what you need). Optional goal focuses the extract.",
            {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 12000},
                },
                "required": [],
            },
            browser_extract_content,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_get_page_content",
            "Legacy alias of browser_extract_content (set include_html only if markup needed).",
            {
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "default": 12000},
                    "include_html": {"type": "boolean", "default": False},
                },
                "required": [],
            },
            browser_get_page_content,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_send_keys",
            "Send a key (Enter/Tab/Escape/ArrowDown/...). Optional index focuses that "
            "element first. Prefer clicking a submit button over Enter when possible.",
            {
                "type": "object",
                "properties": {
                    "keys": {"type": "string"},
                    "index": {"type": "integer"},
                },
                "required": ["keys"],
            },
            browser_send_keys,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_press_key",
            "Legacy alias of browser_send_keys (parameter name: key).",
            {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "index": {"type": "integer"},
                    "selector": {"type": "string"},
                },
                "required": ["key"],
            },
            browser_press_key,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_go_back",
            "Navigate back in history; returns a new observation.",
            {"type": "object", "properties": {}, "required": []},
            browser_go_back,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_go_forward",
            "Navigate forward in history; returns a new observation.",
            {"type": "object", "properties": {}, "required": []},
            browser_go_forward,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_switch_tab",
            "Switch to tab by index (from observation tabs list / browser_tabs).",
            {
                "type": "object",
                "properties": {"index": {"type": "integer", "default": 0}},
                "required": ["index"],
            },
            browser_switch_tab,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_tabs",
            "List / switch / close sandbox browser tabs.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "switch", "close"],
                        "default": "list",
                    },
                    "index": {"type": "integer", "default": 0},
                },
                "required": [],
            },
            browser_tabs,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_wait_for_user",
            "Pause while the USER completes a captcha / security check in the in-app "
            "browser. Call when needs_human=true. Do NOT keep clicking or navigate home.",
            {
                "type": "object",
                "properties": {
                    "timeout_ms": {"type": "integer", "default": 180000},
                },
                "required": [],
            },
            browser_wait_for_user,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_screenshot",
            "Save a PNG screenshot of the sandbox page into the workspace.",
            {
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "default": False},
                    "name": {"type": "string"},
                },
                "required": [],
            },
            browser_screenshot,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_console",
            "Read recent console messages from the sandbox page.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 40}},
                "required": [],
            },
            browser_console,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_diagnostics",
            "Console errors + failed network requests for the current page.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}},
                "required": [],
            },
            browser_diagnostics,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "browser_wait",
            "Wait for a selector state or a short pause while the page settles.",
            {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": ["visible", "hidden", "attached", "detached"],
                        "default": "visible",
                    },
                    "timeout_ms": {"type": "integer", "default": 8000},
                },
                "required": [],
            },
            browser_wait,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_select",
            "Choose an option in a <select> by observation index (preferred) or ref/selector.",
            {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "ref": {"type": "string"},
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["value"],
            },
            browser_select,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    # browser_find_elements RETIRED: it ignored selectors (only accepted `limit`)
    # and returned every interactive node, which made the model search by text
    # instead of acting on live indexed elements. Use browser_observe + index.
