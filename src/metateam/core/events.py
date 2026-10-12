"""Typed runtime events — power CLI + SSE frontend."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .logutil import get_logger, log_exception

PROTOCOL_VERSION = 1

KNOWN_EVENT_TYPES = frozenset(
    {
        "session",
        "final",
        "error",
        "assistant_delta",
        "assistant_reasoning_delta",
        "tool_call_delta",
        "tool_start",
        "tool_end",
        "approval_request",
        "approval_resolved",
        "ask_request",
        "ask_resolved",
        "plan_created",
        "plan_confirm_request",
        "plan_confirm_resolved",
        "plan_step",
        "plan_done",
        "context_usage",
        "llm_start",
        "compress_start",
        "compress_progress",
        "compress",
        "subagent_start",
        "subagent_end",
        "canvas_sync",
        "ask_edge",
        "parallel_batch",
        "browser_open",
        "shell_job",
        "schedule_trigger",
        "max_iterations",
        "cancelled",
        "review",
    }
)

_log = get_logger("metateam.events")


def new_id(prefix: str = "e") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    agent_id: str = ""
    parent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "ts": self.ts,
            "agent_id": self.agent_id,
            "parent_id": self.parent_id,
            "protocol": PROTOCOL_VERSION,
        }


Emitter = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Emitter] = []

    def subscribe(self, fn: Emitter) -> Callable[[], None]:
        self._subs.append(fn)

        def _un() -> None:
            if fn in self._subs:
                self._subs.remove(fn)

        return _un

    def emit(self, event: Event) -> None:
        for fn in list(self._subs):
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001
                log_exception(_log, "event subscriber failed", exc)


def emit(
    bus: Optional[EventBus],
    type_: str,
    data: Optional[dict[str, Any]] = None,
    *,
    agent_id: str = "",
    parent_id: str = "",
) -> None:
    if bus is None:
        return
    bus.emit(Event(type=type_, data=data or {}, agent_id=agent_id, parent_id=parent_id))
