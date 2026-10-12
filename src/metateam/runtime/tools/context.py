"""Shared state for builtin tool handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ...core.config import Settings
from ...services.skills import Skill


@dataclass
class ToolContext:
    settings: Settings
    skills: list[Skill]
    align_state: dict[str, Any] = field(
        default_factory=lambda: {"aligned": False, "queries": []}
    )
    run_child: Optional[Callable[..., str]] = None
    ask_user_fn: Optional[Callable[..., str]] = None
    end_party_session: Optional[Callable[[], None]] = None
    note_canvas_tasks: Optional[Callable[..., None]] = None
    note_ask_edge: Optional[Callable[..., None]] = None

    def live_ws(self) -> Path:
        """Do not cache Path — the user may switch workspace mid-session."""
        return Path(self.settings.workspace)
