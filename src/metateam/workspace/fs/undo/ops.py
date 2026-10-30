"""Undo status and apply operations."""
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

from .context import _lock, _ctx_session_id
from .stack import (
    _workspace_key,
    _undo_root,
    _load_stack,
    _save_stack,
    _resolve_in_workspace,
    _discard_blob,
)

def _status_item(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec.get("id"),
        "label": rec.get("label") or rec.get("op"),
        "op": rec.get("op"),
        "ts": rec.get("ts"),
        "session_id": rec.get("session_id"),
        "user_turn": rec.get("user_turn"),
        "user_text": rec.get("user_text") or "",
        "files": [],
        "file_entries": [],
        "actors": [],
    }


def _record_paths(rec: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("path", "from", "to"):
        path = str(rec.get(key) or "").replace("\\", "/").strip()
        if path:
            out.add(path)
    return out


def _file_entry(rec: dict[str, Any], fallback: str = "") -> dict[str, Any]:
    path = str(rec.get("path") or rec.get("to") or rec.get("from") or fallback or "")
    path = path.replace("\\", "/").strip()
    return {
        "path": path,
        "op": rec.get("op"),
        "id": rec.get("id"),
        "actor": rec.get("actor") or "",
        "actor_label": rec.get("actor_label") or "",
        "why": rec.get("why") or rec.get("label") or "",
        "tool": rec.get("tool") or "",
        "call_id": rec.get("call_id") or "",
        "label": rec.get("label") or "",
    }


def _attach_file(item: dict[str, Any], rec: dict[str, Any], fallback: str = "") -> None:
    entry = _file_entry(rec, fallback)
    if not entry["path"] and fallback:
        entry["path"] = fallback
    item.setdefault("file_entries", []).append(entry)
    path = entry["path"]
    files = item.setdefault("files", [])
    if path and path not in files:
        files.append(path)
    actor = str(entry.get("actor") or "").strip()
    if actor:
        actors = item.setdefault("actors", [])
        if actor not in actors:
            actors.append(actor)


def status(
    workspace: Optional[Path] = None, *, session_id: Optional[str] = None
) -> dict[str, Any]:
    """One timeline row per conversation turn (checkpoint + its file ops)."""
    with _lock:
        stack = _load_stack(workspace)
    if session_id:
        stack = _session_records(stack, session_id)
    grouped: list[dict[str, Any]] = []
    open_idx: Optional[int] = None
    for rec in stack:
        if rec.get("op") == "checkpoint":
            grouped.append(_status_item(rec))
            open_idx = len(grouped) - 1
            continue
        path = str(rec.get("path") or rec.get("to") or rec.get("from") or "").replace(
            "\\", "/"
        ).strip()
        label = str(rec.get("label") or rec.get("op") or path)
        if open_idx is not None:
            _attach_file(grouped[open_idx], rec, path or label)
            continue
        item = _status_item(rec)
        _attach_file(item, rec, path or label)
        grouped.append(item)
    items = list(reversed(grouped))
    return {"count": len(items), "items": items[:MAX_UNDO]}


def turn_user_text(
    session_id: str,
    user_turn: int,
    workspace: Optional[Path] = None,
) -> str:
    """Prompt text stored on this session's checkpoint for ``user_turn``."""
    with _lock:
        stack = _load_stack(workspace)
    recs = _session_records(stack, session_id)
    for rec in recs:
        if (
            rec.get("op") == "checkpoint"
            and rec.get("session_id") == session_id
            and rec.get("user_turn") == user_turn
        ):
            return str(rec.get("user_text") or "")
    return ""


def undo_file(
    path: str,
    *,
    session_id: Optional[str] = None,
    user_turn: Optional[int] = None,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Revert one path in this conversation (this turn and any later writes)."""
    target = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not target:
        raise ValueError("missing path")
    with _lock:
        stack = _load_stack(workspace)
    recs = _session_records(stack, session_id) if session_id else list(stack)
    ids: list[str] = []
    for rec in reversed(recs):
        if rec.get("op") == "checkpoint":
            continue
        if target not in _record_paths(rec):
            continue
        turn = rec.get("user_turn")
        if user_turn is not None and turn is not None and int(turn) < int(user_turn):
            continue
        rid = str(rec.get("id") or "")
        if rid:
            ids.append(rid)
    if not ids:
        raise ValueError(f"no undo records for {target}")
    return _undo_ids(ids, workspace)


def undo_to_turn(
    session_id: str,
    before_user_turn: int,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Restore this session's files to the state before `before_user_turn`.

    Only records belonging to ``session_id`` are reversed; other conversations
    keep their file ops. Files the user deleted in the explorer stay deleted.
    """
    with _lock:
        stack = _load_stack(workspace)
    recs = _session_records(stack, session_id)
    target_id = ""
    for rec in recs:
        if (
            rec.get("op") == "checkpoint"
            and rec.get("session_id") == session_id
            and rec.get("user_turn") == before_user_turn
        ):
            target_id = str(rec.get("id") or "")
            break
    if not target_id:
        n_ids: list[str] = []
        for rec in reversed(recs):
            turn = rec.get("user_turn")
            if turn is None or int(turn) < before_user_turn:
                break
            rid = str(rec.get("id") or "")
            if rid:
                n_ids.append(rid)
        return _undo_ids(n_ids, workspace, partial=True, keep_explorer_deletes=True)
    return _undo_through_id(
        target_id, workspace, session_id=session_id, keep_explorer_deletes=True
    )


def undo_latest_turn(
    workspace: Optional[Path] = None, *, session_id: Optional[str] = None
) -> dict[str, Any]:
    """Undo the latest conversation turn (checkpoint + file ops), not a single write."""
    with _lock:
        stack = _load_stack(workspace)
    recs = _session_records(stack, session_id) if session_id else list(stack)
    if not recs:
        raise ValueError("nothing to undo")
    last_file = None
    for i in range(len(recs) - 1, -1, -1):
        if recs[i].get("op") != "checkpoint":
            last_file = i
            break
    if last_file is None:
        target = str(recs[-1].get("id") or "")
    else:
        cp = None
        for i in range(last_file, -1, -1):
            if recs[i].get("op") == "checkpoint":
                cp = i
                break
        rec = recs[cp] if cp is not None else recs[last_file]
        target = str(rec.get("id") or "")
    if not target:
        raise ValueError("nothing to undo")
    return undo_to_id(target, workspace, session_id=session_id)


def undo_to_id(
    entry_id: str,
    workspace: Optional[Path] = None,
    *,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Undo from the newest matching records down through ``entry_id`` (inclusive).

    When ``session_id`` is set, other conversations' stack entries are skipped.
    """
    target = str(entry_id or "").strip()
    if not target:
        raise ValueError("missing undo id")
    return _undo_through_id(target, workspace, session_id=session_id)


def _undo_through_id(
    entry_id: str,
    workspace: Optional[Path] = None,
    *,
    session_id: Optional[str] = None,
    partial: bool = False,
    keep_explorer_deletes: bool = False,
) -> dict[str, Any]:
    with _lock:
        stack = _load_stack(workspace)
    recs = _session_records(stack, session_id) if session_id else list(stack)
    idx: Optional[int] = None
    for i, rec in enumerate(recs):
        if str(rec.get("id") or "") == entry_id:
            idx = i
            break
    if idx is None:
        raise ValueError("undo entry not found")
    ids = [str(r.get("id") or "") for r in recs[idx:] if r.get("id")]
    ids.reverse()
    return _undo_ids(
        ids, workspace, partial=partial, keep_explorer_deletes=keep_explorer_deletes
    )


def _take_record(entry_id: str, workspace: Optional[Path] = None) -> Optional[dict[str, Any]]:
    with _lock:
        stack = _load_stack(workspace)
        for i, rec in enumerate(stack):
            if str(rec.get("id") or "") == entry_id:
                rec = stack.pop(i)
                _save_stack(stack, workspace)
                return rec
    return None


def _path_under_tombstones(path: str, tombs: set[str]) -> bool:
    p = str(path or "").replace("\\", "/").strip()
    if not p or not tombs:
        return False
    if p in tombs:
        return True
    return any(p.startswith(t + "/") for t in tombs if t)


def _explorer_deleted_paths(records: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for rec in records:
        if rec.get("op") != "delete":
            continue
        if str(rec.get("source") or "") != "explorer":
            continue
        path = str(rec.get("path") or "").replace("\\", "/").strip()
        if path:
            out.add(path)
    return out


def _undo_would_recreate(rec: dict[str, Any], tombs: set[str]) -> bool:
    """True if applying this undo would put bytes back on a user-deleted path."""
    if not tombs:
        return False
    op = rec.get("op")
    if op == "delete":
        return _path_under_tombstones(str(rec.get("path") or ""), tombs)
    if op == "write" and rec.get("had_file"):
        return _path_under_tombstones(str(rec.get("path") or ""), tombs)
    if op in ("move", "rename"):
        return _path_under_tombstones(str(rec.get("from") or ""), tombs)
    return False


def _undo_ids(
    ids_newest_first: list[str],
    workspace: Optional[Path] = None,
    *,
    partial: bool = False,
    keep_explorer_deletes: bool = False,
) -> dict[str, Any]:
    tombs: set[str] = set()
    if keep_explorer_deletes:
        idset = {str(i) for i in ids_newest_first if i}
        with _lock:
            stack = _load_stack(workspace)
        tombs = _explorer_deleted_paths([r for r in stack if str(r.get("id") or "") in idset])
    undone: list[dict[str, Any]] = []
    errors: list[str] = []
    for eid in ids_newest_first:
        if not eid:
            continue
        rec = _take_record(eid, workspace)
        if not rec:
            continue
        try:
            _apply_undo_record(rec, workspace, skip_restore_paths=tombs)
            undone.append({"id": rec.get("id"), "label": rec.get("label"), "op": rec.get("op")})
        except ValueError:
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            break
    with _lock:
        remaining = len(_load_stack(workspace))
    return {
        "status": "ok",
        "undone_count": len(undone),
        "undone": undone,
        "remaining": remaining,
        "partial": partial,
        "errors": errors,
    }


def _apply_undo_record(
    rec: dict[str, Any],
    workspace: Optional[Path] = None,
    *,
    skip_restore_paths: Optional[set[str]] = None,
) -> None:
    op = rec.get("op")
    blob = rec.get("blob")
    blob_path = (_undo_root(workspace) / "blobs" / str(blob)) if blob else None
    try:
        if skip_restore_paths and _undo_would_recreate(rec, skip_restore_paths):
            return
        if op == "checkpoint":
            pass
        elif op == "write":
            path = str(rec.get("path") or "")
            fp = _resolve_in_workspace(path, workspace)
            if rec.get("had_file") and blob_path and blob_path.is_file():
                fp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(blob_path, fp)
            else:
                if fp.exists() and fp.is_file():
                    fp.unlink()
        elif op == "create":
            path = str(rec.get("path") or "")
            fp = _resolve_in_workspace(path, workspace)
            if fp.exists():
                if fp.is_dir():
                    shutil.rmtree(fp)
                else:
                    fp.unlink()
        elif op == "delete":
            path = str(rec.get("path") or "")
            fp = _resolve_in_workspace(path, workspace)
            if blob_path and blob_path.exists():
                fp.parent.mkdir(parents=True, exist_ok=True)
                if rec.get("kind") == "dir":
                    if fp.exists():
                        shutil.rmtree(fp, ignore_errors=True)
                    shutil.copytree(blob_path, fp)
                else:
                    shutil.copy2(blob_path, fp)
        elif op in ("move", "rename"):
            frm = str(rec.get("from") or "")
            to = str(rec.get("to") or "")
            src = _resolve_in_workspace(to, workspace)
            dest = _resolve_in_workspace(frm, workspace)
            if not src.exists():
                raise FileNotFoundError(to)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise FileExistsError(frm)
            shutil.move(str(src), str(dest))
        else:
            raise ValueError(f"unknown undo op: {op}")
    finally:
        _discard_blob(rec, workspace)


def undo_one(workspace: Optional[Path] = None) -> dict[str, Any]:
    """Pop and reverse the latest FS mutation. Returns summary."""
    with _lock:
        stack = _load_stack(workspace)
        if not stack:
            raise ValueError("nothing to undo")
        rec = stack.pop()
        _save_stack(stack, workspace)

    op = rec.get("op")
    _apply_undo_record(rec, workspace)

    with _lock:
        remaining = len(_load_stack(workspace))
    return {
        "status": "ok",
        "undone": {"id": rec.get("id"), "label": rec.get("label"), "op": op},
        "remaining": remaining,
    }
