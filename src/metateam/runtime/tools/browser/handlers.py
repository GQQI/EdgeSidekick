"""Browser tool handler implementations (bound to ToolContext)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from ..context import ToolContext
from .hints import (
    _local_document_hint,
    _navigate_failure_message,
    _navigate_info_failure,
    _is_connection_refused_text,
    _is_unsafe_port_text,
    _is_loopback_url,
    _connection_refused_hint,
    _unsafe_port_hint,
)


def make_browser_handlers(ctx: ToolContext):
    """Build callables closed over `ctx` / `live_ws` for registration."""
    live_ws = ctx.live_ws

    def _obs_json(payload: dict) -> str:
        # Prefer compact observation text; keep structured fields for debugging.
        return json.dumps(payload, ensure_ascii=False)

    def _format_input_result(payload: dict | str) -> str:
        """Surface non-editable targets as ERROR: … while keeping observation JSON."""
        if not isinstance(payload, dict):
            return str(payload)
        if payload.get("ok") is False:
            err = str(payload.get("error") or "element is not editable")
            if not err.startswith("ERROR:"):
                payload = {**payload, "error": f"ERROR: {err}"}
        return _obs_json(payload)

    def browser_navigate(url: str = "", address: str = "", href: str = "") -> str:
        from ....core.hostinfo import network_available
        from ....core.netguard import blocked_http_reason
        from ....services.browser_preview import is_preview_http_url
        from ....services.browser_sandbox import (
            SANDBOX,
            coerce_navigate_target,
            urls_match,
        )

        target = (url or address or href or "").strip()
        if not target:
            return "ERROR: empty url"
        from ....services.browser_sandbox import _looks_like_asset_filename
        from ....services.user_directives import path_is_denied

        denied = path_is_denied(target)
        if denied:
            return (
                f"ERROR: blocked by user directive — do not open «{denied}». "
                "The user forbade this path; acknowledge and continue without it."
            )

        shot = _looks_like_asset_filename(target)
        try:
            resolved = coerce_navigate_target(target, workspace=live_ws())
        except ValueError as exc:
            return f"ERROR: {exc}"
        if resolved:
            denied_r = path_is_denied(resolved)
            if denied_r:
                return (
                    f"ERROR: blocked by user directive — do not open «{denied_r}». "
                    "The user forbade this path; acknowledge and continue without it."
                )
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
                if obs.get("url"):
                    info["url"] = obs.get("url") or info.get("url")
                if obs.get("needs_human"):
                    info["needs_human"] = True
                    info["challenge"] = obs.get("challenge")
            except Exception as exc:  # noqa: BLE001
                info["observation_error"] = str(exc)
            fail = _navigate_info_failure(info, resolved)
            if fail:
                return fail
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return _navigate_failure_message(str(exc), resolved or target)

    def browser_observe(limit: int = 40, highlight_all: bool = False) -> str:
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

        try:
            if index is not None and int(index) >= 0:
                payload = SANDBOX.input_index(
                    int(index), text, submit=bool(submit), clear=bool(clear)
                )
                return _format_input_result(payload)
            raw = (ref or "").strip()
            if raw:
                return _format_input_result(SANDBOX.fill_ref(raw, text))
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
        from ....services.browser_sandbox import SANDBOX

        try:
            sel = selector or ""
            if index is not None and int(index) >= 0 and not sel and not content:
                # Scroll a specific indexed element into view via its stamped attr.
                from ....services import browser_observe as _b

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
        from ....services.browser_sandbox import SANDBOX

        try:
            return _obs_json(SANDBOX.scroll_to_text(text))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_extract_content(goal: str = "", max_chars: int = 12000) -> str:
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

        try:
            return _obs_json(
                SANDBOX.page_content(
                    max_chars=int(max_chars or 12000), include_html=True
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_send_keys(keys: str = "", index: int = -1) -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            idx = int(index) if index is not None and int(index) >= 0 else None
            return _obs_json(SANDBOX.send_keys(keys, index=idx))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_press_key(key: str = "", index: int = -1, selector: str = "") -> str:
        if index is not None and int(index) >= 0:
            return browser_send_keys(keys=key, index=index)
        from ....services.browser_sandbox import SANDBOX

        try:
            return SANDBOX.press_key(key, selector=selector or "")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_go_back() -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            info = dict(SANDBOX.go_back() or {})
            if info.get("ok") is False:
                err = str(info.get("error") or "go_back failed")
                hint = str(info.get("hint") or "").strip()
                parts = [f"ERROR: {err}"]
                if hint:
                    parts.append(hint)
                parts.append(
                    "Do not retry browser_go_back on the same unsafe history entry; "
                    "call browser_navigate to a safe URL instead."
                )
                return "\n".join(parts)
            try:
                obs = SANDBOX.observe_page()
                info["observation"] = obs.get("observation")
            except Exception:
                pass
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_go_forward() -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            info = dict(SANDBOX.go_forward() or {})
            if info.get("ok") is False:
                err = str(info.get("error") or "go_forward failed")
                hint = str(info.get("hint") or "").strip()
                parts = [f"ERROR: {err}"]
                if hint:
                    parts.append(hint)
                return "\n".join(parts)
            return _obs_json(info)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_switch_tab(index: int = 0) -> str:
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

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
        from ....services.browser_sandbox import SANDBOX

        try:
            path = SANDBOX.save_screenshot_to_workspace(
                live_ws(),
                name=(name or "").strip(),
                full_page=bool(full_page),
            )
            from ....core.pathutil import relative_to_posix

            rel = relative_to_posix(path, live_ws())
            return json.dumps({"path": rel, "abs": str(path)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_console(limit: int = 40) -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            logs = SANDBOX.console_logs(limit=int(limit) if limit else 40)
            return json.dumps({"count": len(logs), "logs": logs}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_diagnostics(limit: int = 20) -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            data = SANDBOX.diagnostics(limit=int(limit) if limit else 20)
            # Cap console text so one diagnostics call cannot blow the window
            # and abort the next LLM round (empty UI "no text this turn").
            errs = data.get("console_errors") if isinstance(data, dict) else None
            if isinstance(errs, list):
                clipped = []
                for e in errs[:50]:
                    if not isinstance(e, dict):
                        continue
                    row = dict(e)
                    msg = str(row.get("text") or row.get("message") or "")
                    if len(msg) > 240:
                        row["text"] = msg[:240] + "…"
                        row.pop("message", None)
                    clipped.append(row)
                data["console_errors"] = clipped
            raw = json.dumps(data, ensure_ascii=False)
            if len(raw) > 12_000:
                raw = raw[:12_000] + "…[truncated]"
            return raw
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_wait(selector: str = "", state: str = "visible", timeout_ms: int = 8000) -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            return SANDBOX.wait_for(
                selector=selector or "",
                state=state or "visible",
                timeout_ms=int(timeout_ms or 8000),
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def browser_select(index: int = -1, ref: str = "", selector: str = "", value: str = "") -> str:
        from ....services.browser_sandbox import SANDBOX

        try:
            if index is not None and int(index) >= 0:
                from ....services import browser_observe as _b

                sel = f'[{_b.IDX_ATTR}="{int(index)}"]'
                return _obs_json(SANDBOX.select_selector(sel, value))
            if (ref or "").strip():
                return _obs_json(SANDBOX.select_ref(ref.strip(), value))
            return _obs_json(SANDBOX.select_selector(selector, value))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"


    return SimpleNamespace(
        browser_navigate=browser_navigate,
        browser_observe=browser_observe,
        browser_snapshot=browser_snapshot,
        browser_click=browser_click,
        browser_input=browser_input,
        browser_type=browser_type,
        browser_scroll=browser_scroll,
        browser_scroll_to_text=browser_scroll_to_text,
        browser_extract_content=browser_extract_content,
        browser_get_page_content=browser_get_page_content,
        browser_send_keys=browser_send_keys,
        browser_press_key=browser_press_key,
        browser_go_back=browser_go_back,
        browser_go_forward=browser_go_forward,
        browser_switch_tab=browser_switch_tab,
        browser_tabs=browser_tabs,
        browser_wait_for_user=browser_wait_for_user,
        browser_screenshot=browser_screenshot,
        browser_console=browser_console,
        browser_diagnostics=browser_diagnostics,
        browser_wait=browser_wait,
        browser_select=browser_select,
        _obs_json=_obs_json,
        _format_input_result=_format_input_result,
    )
