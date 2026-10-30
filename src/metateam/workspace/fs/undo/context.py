"""Turn / mutation context for FS undo."""
from __future__ import annotations

import contextvars
import difflib
import hashlib
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ....core.config import get_settings


MAX_UNDO = 400
_lock = threading.Lock()

# Bound to the active top-level chat turn so FS mutations can be restored on edit.
_ctx_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_session_id", default=None
)
_ctx_user_turn: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "fs_undo_user_turn", default=None
)
_ctx_actor: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_actor", default=None
)
_ctx_actor_label: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_actor_label", default=None
)
_ctx_why: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_why", default=None
)
_ctx_tool: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_tool", default=None
)
_ctx_call_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "fs_undo_call_id", default=None
)


def set_turn_context(session_id: Optional[str], user_turn: Optional[int]) -> None:
    _ctx_session_id.set(session_id)
    _ctx_user_turn.set(user_turn)


def set_mutation_meta(
    *,
    actor: Optional[str] = None,
    actor_label: Optional[str] = None,
    why: Optional[str] = None,
    tool: Optional[str] = None,
    call_id: Optional[str] = None,
) -> None:
    """Who/why for the next FS mutation (main vs sub-agent tool call)."""
    _ctx_actor.set(actor)
    _ctx_actor_label.set(actor_label)
    _ctx_why.set(why)
    _ctx_tool.set(tool)
    _ctx_call_id.set(call_id)


def clear_mutation_meta() -> None:
    set_mutation_meta()


def clear_turn_context() -> None:
    _ctx_session_id.set(None)
    _ctx_user_turn.set(None)
    clear_mutation_meta()


def _stamp_turn(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("session_id") is None:
        sid = _ctx_session_id.get()
        if sid:
            record["session_id"] = sid
    if record.get("user_turn") is None:
        turn = _ctx_user_turn.get()
        if turn is not None:
            record["user_turn"] = turn
    for key, var in (
        ("actor", _ctx_actor),
        ("actor_label", _ctx_actor_label),
        ("why", _ctx_why),
        ("tool", _ctx_tool),
        ("call_id", _ctx_call_id),
    ):
        if not record.get(key):
            val = var.get()
            if val:
                record[key] = val
    return record
