"""Assemble the builtin + skill + MCP tool registry."""

from __future__ import annotations

from typing import Callable, Optional

from ...core.config import Settings
from ...services.skills import Skill
from ..tool_registry import Tool, ToolRegistry
from .browser import register_browser_tools
from .codebase import register_codebase_tools
from .context import ToolContext
from .delegate import register_ask_and_delegate
from .files import register_file_tools
from .git import register_git_tools
from .memory import register_memory_tools
from .scheduler import register_scheduler_tools
from .shell import register_shell_tools
from .web import register_web_tools
from .skills import register_skill_tools


def build_registry(
    settings: Settings,
    *,
    skills: list[Skill],
    allow_delegate: bool = True,
    run_child: Optional[Callable[..., str]] = None,
    ask_user_fn: Optional[Callable[..., str]] = None,
    talk_only: bool = False,
    end_party_session: Optional[Callable[[], None]] = None,
    note_canvas_tasks: Optional[Callable[..., None]] = None,
    note_ask_edge: Optional[Callable[..., None]] = None,
) -> ToolRegistry:
    reg = ToolRegistry()
    if talk_only:
        return reg
    ctx = ToolContext(
        settings=settings,
        skills=skills,
        run_child=run_child if allow_delegate else None,
        ask_user_fn=ask_user_fn,
        end_party_session=end_party_session,
        note_canvas_tasks=note_canvas_tasks,
        note_ask_edge=note_ask_edge,
    )
    register_file_tools(reg, ctx)
    register_codebase_tools(reg, ctx)
    register_git_tools(reg, ctx)
    register_shell_tools(reg, ctx)
    register_skill_tools(reg, ctx)
    register_memory_tools(reg, ctx)
    register_scheduler_tools(reg, ctx)
    register_ask_and_delegate(reg, ctx)
    register_browser_tools(reg, ctx)
    register_web_tools(reg)

    register_mcp_safely(reg, settings=settings)
    register_plugins_safely(reg, settings=settings)
    apply_extension_budget_safely(reg, settings=settings)
    return reg


def register_mcp_safely(
    reg: ToolRegistry,
    *,
    settings: Optional[Settings] = None,
    server_ids: Optional[list[str]] = None,
) -> None:
    """Bind MCP tools when META_MCP_ENABLED and prefs.mcp_in_chat allow it.

    Empty / missing binding = all enabled servers (auto, like Grok Bot).
    Non-empty list = optional composer subset filter.
    Master toggle mcp_in_chat / META_MCP_ENABLED still gates injection.
    """
    try:
        from ...services.app_prefs import mcp_in_chat
        from ...services.mcp_runtime import register_mcp_tools
        from .extension_budget import normalize_id_list

        if settings is not None and not getattr(settings, "mcp_enabled", True):
            if hasattr(reg, "drop_prefix"):
                reg.drop_prefix("mcp_")
            return
        if not mcp_in_chat():
            if hasattr(reg, "drop_prefix"):
                reg.drop_prefix("mcp_")
            return
        if server_ids is not None:
            bound = normalize_id_list(server_ids)
        elif settings is not None:
            raw = getattr(settings, "mcp_bound_ids", None)
            bound = normalize_id_list(raw) if raw is not None else []
        else:
            bound = []
        # Product: unbound/empty → None → all enabled; subset → filter.
        filter_ids = bound if bound else None
        register_mcp_tools(reg, Tool=Tool, server_ids=filter_ids)
    except Exception as exc:  # noqa: BLE001
        from ...core.logutil import get_logger, log_exception

        log_exception(get_logger("metateam.tools"), "MCP tool registration failed", exc)


def register_plugins_safely(
    reg: ToolRegistry,
    *,
    settings: Optional[Settings] = None,
    plugin_ids: Optional[list[str]] = None,
) -> None:
    """Bind plugin tools when prefs.plugins_in_chat allow it.

    Empty / missing binding = all installed (+ configured if credentials required) plugins (auto).
    Non-empty list = optional composer subset filter.
    """
    try:
        from ...plugins.tools import register_plugin_tools
        from ...services.app_prefs import plugins_in_chat
        from .extension_budget import normalize_id_list

        if not plugins_in_chat():
            from ...plugins.manifest import TOOL_PREFIX

            if hasattr(reg, "drop_prefix"):
                reg.drop_prefix(TOOL_PREFIX)
            return
        if plugin_ids is not None:
            bound = normalize_id_list(plugin_ids)
        elif settings is not None:
            raw = getattr(settings, "plugin_bound_ids", None)
            bound = normalize_id_list(raw) if raw is not None else []
        else:
            bound = []
        filter_ids = bound if bound else None
        register_plugin_tools(reg, Tool=Tool, plugin_ids=filter_ids)
    except Exception as exc:  # noqa: BLE001
        from ...core.logutil import get_logger, log_exception

        log_exception(get_logger("metateam.tools"), "Plugin tool registration failed", exc)


def apply_extension_budget_safely(
    reg: ToolRegistry,
    *,
    settings: Optional[Settings] = None,
    recent: Optional[list[str]] = None,
    mention_text: Optional[str] = None,
    plugin_summaries: Optional[list] = None,
) -> None:
    """Cap full MCP/plugin schemas; enable search_tools when over threshold."""
    try:
        from .extension_budget import (
            DEFAULT_TOOL_SCHEMA_THRESHOLD,
            apply_extension_schema_budget,
        )

        threshold = DEFAULT_TOOL_SCHEMA_THRESHOLD
        if settings is not None:
            try:
                threshold = int(getattr(settings, "tool_schema_threshold", threshold) or threshold)
            except Exception:
                threshold = DEFAULT_TOOL_SCHEMA_THRESHOLD
        recent_list = list(recent or [])
        if settings is not None and not recent_list:
            raw = getattr(settings, "recent_tool_successes", None)
            if isinstance(raw, (list, tuple)):
                recent_list = [str(x) for x in raw if str(x).strip()]
        mention = mention_text
        if mention is None and settings is not None:
            mention = getattr(settings, "budget_mention_text", None)
        summaries = plugin_summaries
        apply_extension_schema_budget(
            reg,
            threshold=threshold,
            recent=recent_list,
            mention_text=str(mention or "") or None,
            plugin_summaries=summaries,
            Tool=Tool,
        )
    except Exception as exc:  # noqa: BLE001
        from ...core.logutil import get_logger, log_exception

        log_exception(get_logger("metateam.tools"), "extension schema budget failed", exc)


def inject_recent_extension_tools(
    reg: ToolRegistry,
    *,
    settings: Optional[Settings] = None,
    recent: Optional[list[str]] = None,
    limit: int = 8,
) -> int:
    """Legacy helper: inject recent successes as a narrow whitelist.

    Prefer empty binding = auto-all via register_*_safely; keep this for
    callers that still want a recent-only hot path without full auto.
    """
    from .extension_budget import is_extension_tool

    recent_list = [
        str(x).strip()
        for x in (recent or [])
        if str(x).strip() and is_extension_tool(str(x))
    ]
    if settings is not None and not recent_list:
        raw = getattr(settings, "recent_tool_successes", None)
        if isinstance(raw, (list, tuple)):
            recent_list = [
                str(x).strip()
                for x in raw
                if str(x).strip() and is_extension_tool(str(x))
            ]
    recent_list = recent_list[: max(1, int(limit))]
    if not recent_list:
        return 0

    mcp_ids: list[str] = []
    plugin_bodies: list[str] = []
    for name in recent_list:
        if name.startswith("mcp_"):
            body = name[4:]
            sid = body.split("_", 1)[0] if body else ""
            if sid and sid not in mcp_ids:
                mcp_ids.append(sid)
        elif name.startswith("plugin_"):
            plugin_bodies.append(name[7:])

    refined_plugins: list[str] = []
    try:
        from ...plugins.store import list_installed

        installed = list(list_installed())
        for body in plugin_bodies:
            for pl in installed:
                pid = str(pl.id)
                safe = pid.replace("-", "_")
                if body.startswith(safe + "_") or body == safe:
                    if pid not in refined_plugins:
                        refined_plugins.append(pid)
                    break
    except Exception:
        refined_plugins = []

    if mcp_ids:
        register_mcp_safely(reg, settings=settings, server_ids=mcp_ids)
    if refined_plugins:
        register_plugins_safely(reg, settings=settings, plugin_ids=refined_plugins)

    if hasattr(reg, "set_schema_focus"):
        focus = {n for n in reg.names() if not is_extension_tool(n)}
        for n in recent_list:
            if reg.get(n):
                focus.add(n)
        reg.set_schema_focus(focus)
    return len(recent_list)
