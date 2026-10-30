"""ChatSession model."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...runtime.agent import Agent
from ...core.config import Settings, get_settings, reload_settings
from ...core.events import EventBus, new_id
from ...core.logutil import get_logger, log_exception
from ..session import load_session, load_session_bundle, save_session, sessions_dir, workspace_matches

_log = get_logger("metateam.store")


@dataclass
class ChatSession:
    id: str
    agent: Agent
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    title: str = "New chat"
    user_id: str = ""
    busy: bool = False
    stop_requested: bool = False
    agent_tree: list[Any] = field(default_factory=list)
    pending_job_notices: list[dict[str, Any]] = field(default_factory=list)
    # Per-conversation MCP / plugin binding (empty = auto all enabled).
    mcp_server_ids: list[str] = field(default_factory=list)
    plugin_ids: list[str] = field(default_factory=list)
    recent_tool_successes: list[str] = field(default_factory=list)
    # Social (会话版) one-shot session: excluded from the workbench history list.
    hidden: bool = False
