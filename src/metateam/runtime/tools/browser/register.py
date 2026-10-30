"""CDP browser sandbox tools (navigate / screenshot / click / type)."""

from __future__ import annotations

from ...tool_registry import Tool, ToolRegistry
from ..context import ToolContext
from .handlers import make_browser_handlers

# Re-export hints for tests / compat
from .hints import (  # noqa: F401
    _local_document_hint,
    _is_connection_refused_text,
    _is_unsafe_port_text,
    _is_loopback_url,
    _connection_refused_hint,
    _unsafe_port_hint,
    _navigate_failure_message,
    _navigate_info_failure,
)


def register_browser_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    """Register browser tools: observe → act-by-index → re-observe."""
    h = make_browser_handlers(ctx)

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
            h.browser_navigate,
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
            h.browser_observe,
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
            h.browser_snapshot,
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
            h.browser_click,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_input",
            "Type into a fillable field by observation index "
            "(input/textarea/select/contenteditable or ARIA textbox/searchbox/combobox/spinbutton). "
            "Non-editable targets (links, buttons, plain divs, …) return a clear error "
            "guiding you to browser_click — Playwright fill is not called. "
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
            h.browser_input,
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
            h.browser_type,
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
            h.browser_scroll,
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
            h.browser_scroll_to_text,
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
            h.browser_extract_content,
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
            h.browser_get_page_content,
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
            h.browser_send_keys,
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
            h.browser_press_key,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "browser_go_back",
            "Navigate back in history; returns a new observation. "
            "If it returns ERROR with ERR_UNSAFE_PORT / ok:false, do not retry — "
            "Chromium blocked that history URL's port; browser_navigate to a safe URL.",
            {"type": "object", "properties": {}, "required": []},
            h.browser_go_back,
            parallel_safe=False,
        )
    )
    reg.register(
        Tool(
            "browser_go_forward",
            "Navigate forward in history; returns a new observation.",
            {"type": "object", "properties": {}, "required": []},
            h.browser_go_forward,
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
            h.browser_switch_tab,
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
            h.browser_tabs,
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
            h.browser_wait_for_user,
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
            h.browser_screenshot,
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
            h.browser_console,
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
            h.browser_diagnostics,
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
            h.browser_wait,
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
            h.browser_select,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    # browser_find_elements RETIRED: it ignored selectors (only accepted `limit`)
    # and returned every interactive node, which made the model search by text
    # instead of acting on live indexed elements. Use browser_observe + index.
