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

    if getattr(settings, "mcp_enabled", True):
        try:
            from ...services.mcp_runtime import register_mcp_tools

            register_mcp_tools(reg, Tool=Tool)
        except Exception as exc:  # noqa: BLE001
            from ...core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.tools"), "MCP tool registration failed", exc)

    register_plugins_safely(reg)
    return reg


def register_plugins_safely(reg: ToolRegistry) -> None:
    try:
        from ...plugins.tools import register_plugin_tools

        register_plugin_tools(reg, Tool=Tool)
    except Exception as exc:  # noqa: BLE001
        from ...core.logutil import get_logger, log_exception

        log_exception(get_logger("metateam.tools"), "Plugin tool registration failed", exc)
