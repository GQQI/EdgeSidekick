"""Undo stack persistence and blob storage."""
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

from .context import (
    MAX_UNDO,
    _lock,
    _stamp_turn,
    _ctx_session_id,
    _ctx_user_turn,
    _ctx_actor,
    _ctx_actor_label,
    _ctx_why,
    _ctx_tool,
    _ctx_call_id,
)

def _workspace_key(workspace: Path) -> str:
    return hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:16]


def _undo_root(workspace: Optional[Path] = None) -> Path:
    settings = get_settings()
    if workspace is not None:
        ws = Path(workspace).resolve()
    else:
        # Honor bind_active_workspace / workspace_override from panel API calls.
        from metateam.workspace.fs_api import workspace_root

        ws = workspace_root()
    root = settings.root / "data" / "fs_undo" / _workspace_key(ws)
    root.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    return root


def _stack_path(workspace: Optional[Path] = None) -> Path:
    return _undo_root(workspace) / "stack.json"


def _load_stack(workspace: Optional[Path] = None) -> list[dict[str, Any]]:
    path = _stack_path(workspace)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_stack(stack: list[dict[str, Any]], workspace: Optional[Path] = None) -> None:
    path = _stack_path(workspace)
    path.write_text(json.dumps(stack[-MAX_UNDO:], ensure_ascii=False, indent=2), encoding="utf-8")


def _new_blob_id() -> str:
    return uuid.uuid4().hex


def _store_file_blob(src: Path, workspace: Optional[Path] = None) -> str:
    bid = _new_blob_id()
    dest = _undo_root(workspace) / "blobs" / bid
    shutil.copy2(src, dest)
    return bid


def _store_dir_blob(src: Path, workspace: Optional[Path] = None) -> str:
    bid = _new_blob_id()
    dest = _undo_root(workspace) / "blobs" / bid
    shutil.copytree(src, dest)
    return bid


def push(record: dict[str, Any], workspace: Optional[Path] = None) -> None:
    """Push an undo record. Caller fills op-specific fields."""
    with _lock:
        stack = _load_stack(workspace)
        record = _stamp_turn(
            {
                **record,
                "id": record.get("id") or _new_blob_id(),
                "ts": time.time(),
            }
        )
        stack.append(record)
        # prune old blobs when trimming stack
        trimmed = stack[:-MAX_UNDO] if len(stack) > MAX_UNDO else []
        stack = stack[-MAX_UNDO:]
        _save_stack(stack, workspace)
        for old in trimmed:
            _discard_blob(old, workspace)


def _discard_blob(rec: dict[str, Any], workspace: Optional[Path] = None) -> None:
    bid = rec.get("blob")
    if not bid:
        return
    path = _undo_root(workspace) / "blobs" / str(bid)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def push_before_write(rel: str, abs_path: Path, workspace: Optional[Path] = None) -> None:
    if abs_path.exists() and abs_path.is_file():
        try:
            blob = _store_file_blob(abs_path, workspace)
            push(
                {
                    "op": "write",
                    "path": rel,
                    "blob": blob,
                    "label": f"修改 {rel}",
                    "had_file": True,
                },
                workspace,
            )
            return
        except OSError:
            pass
    push(
        {
            "op": "write",
            "path": rel,
            "blob": None,
            "label": f"新建 {rel}",
            "had_file": False,
        },
        workspace,
    )


def push_before_create(rel: str, kind: str, workspace: Optional[Path] = None) -> None:
    push(
        {
            "op": "create",
            "path": rel,
            "kind": kind,
            "label": f"新建{'目录' if kind == 'dir' else '文件'} {rel}",
        },
        workspace,
    )


def push_before_delete(
    rel: str,
    abs_path: Path,
    workspace: Optional[Path] = None,
    *,
    source: str = "tool",
) -> None:
    extra = {}
    if source == "explorer":
        extra = {"source": "explorer", "actor": "user", "tool": "explorer"}
    if abs_path.is_dir():
        blob = _store_dir_blob(abs_path, workspace)
        push(
            {
                "op": "delete",
                "path": rel,
                "kind": "dir",
                "blob": blob,
                "label": f"删除目录 {rel}",
                **extra,
            },
            workspace,
        )
    else:
        blob = _store_file_blob(abs_path, workspace)
        push(
            {
                "op": "delete",
                "path": rel,
                "kind": "file",
                "blob": blob,
                "label": f"删除 {rel}",
                **extra,
            },
            workspace,
        )


def push_before_move(
    from_rel: str,
    to_rel: str,
    workspace: Optional[Path] = None,
) -> None:
    push(
        {
            "op": "move",
            "from": from_rel,
            "to": to_rel,
            "label": f"移动 {from_rel} → {to_rel}",
        },
        workspace,
    )


def push_before_rename(
    from_rel: str,
    to_rel: str,
    workspace: Optional[Path] = None,
) -> None:
    push(
        {
            "op": "rename",
            "from": from_rel,
            "to": to_rel,
            "label": f"重命名 {from_rel} → {to_rel}",
        },
        workspace,
    )


def _resolve_in_workspace(rel: str, workspace: Optional[Path] = None) -> Path:
    from ....core.pathutil import is_relative_to, resolve_path
    from metateam.workspace.fs_api import workspace_root

    ws = Path(workspace).resolve() if workspace is not None else workspace_root()
    raw = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not raw or raw.startswith("-"):
        raise ValueError("invalid path")
    target = resolve_path(ws / raw)
    if not is_relative_to(target, ws):
        raise ValueError(f"path outside workspace: {target}")
    return target


def _clip_user_text(text: str, limit: int = 500) -> str:
    clipped = (text or "").strip()
    if len(clipped) > limit:
        return clipped[: limit - 1] + "…"
    return clipped


def push_checkpoint(
    session_id: str,
    user_turn: int,
    workspace: Optional[Path] = None,
    user_text: str = "",
) -> None:
    """Mark the start of a user turn so edit can restore files to this point."""
    prompt = _clip_user_text(user_text)
    with _lock:
        stack = _load_stack(workspace)
        if stack:
            last = stack[-1]
            if (
                last.get("op") == "checkpoint"
                and last.get("session_id") == session_id
                and last.get("user_turn") == user_turn
            ):
                if prompt and not (last.get("user_text") or ""):
                    last["user_text"] = prompt
                    _save_stack(stack, workspace)
                return
    push(
        {
            "op": "checkpoint",
            "session_id": session_id,
            "user_turn": user_turn,
            "label": f"对话轮次 {user_turn + 1}",
            "user_text": prompt,
        },
        workspace,
    )
