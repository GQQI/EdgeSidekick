"""Compat shim — implementation lives in metateam.runtime.agent.agent_execute."""
from .agent.agent_execute import *  # noqa: F403
from .agent.agent_execute import (  # noqa: F401 — star import skips leading underscores
    _alias_tool_name,
    _annotate_file_blocker,
    _is_blocker_result,
    _tool_arg_error,
)
