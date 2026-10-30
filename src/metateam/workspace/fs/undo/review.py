"""Undo review / tracked-file diff helpers."""
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

from .context import _lock
from .stack import (
    _workspace_key,
    _undo_root,
    _load_stack,
    _resolve_in_workspace,
    _clip_user_text,
)

def _read_text_capped(path: Path, limit: int = 2_000_000) -> Optional[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return ""
    if b"\0" in data[:8192]:
        return None
    if len(data) > limit:
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


def _count_lines_text(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _blob_root(blob_id: Any, workspace: Optional[Path] = None) -> Optional[Path]:
    if not blob_id:
        return None
    path = _undo_root(workspace) / "blobs" / str(blob_id)
    return path if path.exists() else None


def _blob_text(blob_id: Any, workspace: Optional[Path] = None) -> Optional[str]:
    root = _blob_root(blob_id, workspace)
    if root is None or not root.is_file():
        return None
    return _read_text_capped(root)


def _read_blob_file(path: Path) -> tuple[str, bool]:
    """Return (text, is_binary). Missing files yield empty non-binary text."""
    if not path.is_file():
        return "", False
    text = _read_text_capped(path)
    if text is None:
        return "", True
    return text, False


def _deleted_blob_members(
    st: dict[str, Any], workspace: Optional[Path] = None
) -> list[tuple[str, str, bool]]:
    """Inner relative path ('' = the tracked path itself), text, binary flag."""
    blob = st.get("delete_blob") or st.get("blob")
    root = _blob_root(blob, workspace)
    if root is None:
        return [("", "", False)]
    if root.is_file():
        text, binary = _read_blob_file(root)
        return [("", text, binary)]
    if root.is_dir():
        members: list[tuple[str, str, bool]] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            inner = p.relative_to(root).as_posix()
            text, binary = _read_blob_file(p)
            members.append((inner, text, binary))
        return members or [("", "", False)]
    return [("", "", False)]


def _find_tracked(
    path: str, tracked: dict[str, dict[str, Any]]
) -> tuple[str, Optional[dict[str, Any]], str]:
    """Match an exact path, or a file inside a deleted directory blob."""
    st = tracked.get(path)
    if st is not None:
        return path, st, ""
    best: Optional[tuple[str, dict[str, Any], str]] = None
    for prefix, item in tracked.items():
        if not prefix or path == prefix:
            continue
        if not path.startswith(prefix + "/"):
            continue
        if not (item.get("op") == "delete" and item.get("kind") == "dir"):
            continue
        inner = path[len(prefix) + 1 :]
        if best is None or len(prefix) > len(best[0]):
            best = (prefix, item, inner)
    if best:
        return best
    return path, None, ""


def _line_delta(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    added = deleted = 0
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            deleted += i2 - i1
        elif tag == "replace":
            deleted += i2 - i1
            added += j2 - j1
    return added, deleted


def _review_entry(
    path: str,
    *,
    kind: str,
    added: int,
    deleted: int,
    xy: str,
    untracked: bool = False,
) -> dict[str, Any]:
    return {
        "path": path,
        "xy": xy,
        "staged": False,
        "unstaged": True,
        "untracked": untracked,
        "kind": kind,
        "added": added,
        "deleted": deleted,
    }


def _resolve_session_id(stack: list[dict[str, Any]], session_id: Optional[str]) -> Optional[str]:
    sid = str(session_id or "").strip() or None
    if sid:
        return sid
    for rec in reversed(stack):
        if rec.get("op") == "checkpoint" and rec.get("session_id"):
            return str(rec["session_id"])
    return None


def _session_records(
    stack: list[dict[str, Any]], session_id: Optional[str]
) -> list[dict[str, Any]]:
    """Records belonging to one conversation, including interleaved turns.

    A record belongs to the session if it is stamped with that session_id, or
    it is untagged and follows that session's checkpoint (file tools / explorer
    ops that did not stamp an id).
    """
    sid = _resolve_session_id(stack, session_id)
    if not sid:
        return list(stack)
    out: list[dict[str, Any]] = []
    owner: Optional[str] = None
    for rec in stack:
        rec_sid = str(rec.get("session_id") or "").strip() or None
        if rec.get("op") == "checkpoint" and rec_sid:
            owner = rec_sid
        if rec_sid:
            if rec_sid == sid:
                out.append(rec)
            continue
        if owner == sid:
            out.append(rec)
    return out


def _collect_tracked(
    workspace: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """Per-file review baseline = first touch in the current conversation.

    A file created then edited in the same chat is *added* vs empty, with
    the final content — the net change for this conversation, not the
    last turn only.
    """
    with _lock:
        stack = _session_records(list(_load_stack(workspace)), session_id)

    tracked: dict[str, dict[str, Any]] = {}

    def ensure(path: str) -> dict[str, Any]:
        item = tracked.get(path)
        if item is None:
            item = {
                "blob": None,
                "had": False,
                "op": "",
                "kind": "file",
                "delete_blob": None,
                "seen": False,
            }
            tracked[path] = item
        return item

    def begin_first(item: dict[str, Any], *, blob: Any, had: bool) -> None:
        if item.get("seen"):
            return
        item["seen"] = True
        item["blob"] = blob
        item["had"] = had

    for rec in stack:
        op = rec.get("op")
        if op == "checkpoint":
            continue
        if op in (None,):
            continue
        if op == "write":
            path = str(rec.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            item = ensure(path)
            begin_first(item, blob=rec.get("blob"), had=bool(rec.get("had_file")))
            item["op"] = "write"
            item["kind"] = "file"
        elif op == "create":
            path = str(rec.get("path") or "").replace("\\", "/").strip()
            if not path or rec.get("kind") == "dir":
                continue
            item = ensure(path)
            begin_first(item, blob=None, had=False)
            item["op"] = "create"
            item["kind"] = "file"
        elif op == "delete":
            path = str(rec.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            item = ensure(path)
            begin_first(item, blob=rec.get("blob"), had=True)
            item["op"] = "delete"
            item["kind"] = rec.get("kind") or "file"
            if rec.get("blob"):
                item["delete_blob"] = rec.get("blob")
        elif op in ("move", "rename"):
            frm = str(rec.get("from") or "").replace("\\", "/").strip()
            to = str(rec.get("to") or "").replace("\\", "/").strip()
            if not frm or not to:
                continue
            prev = tracked.pop(frm, None)
            dest = ensure(to)
            if not dest.get("seen"):
                dest["seen"] = True
                if prev:
                    dest["blob"] = prev.get("blob")
                    dest["had"] = bool(prev.get("had"))
                else:
                    dest["blob"] = None
                    dest["had"] = True
            dest["op"] = "rename"
            dest["kind"] = "file"

    return tracked


def review_snapshot(
    workspace: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """File changes for the composer review bar (net diff for this conversation).

    Defaults to the latest checkpoint's session. Non-git workspaces use this
    as the sole source; git workspaces keep porcelain in the git panel.
    """
    ws = (workspace or get_settings().workspace).resolve()
    tracked = _collect_tracked(workspace, session_id=session_id)
    files: list[dict[str, Any]] = []
    added_total = deleted_total = 0
    for path in sorted(tracked):
        st = tracked[path]
        last = st.get("op")
        abs_path = ws / path
        missing = not abs_path.exists()
        is_dir = st.get("kind") == "dir"

        if is_dir and last != "delete" and not missing:
            continue

        gone = last == "delete" or missing
        if gone:
            for inner, text, binary in _deleted_blob_members(st, workspace):
                full = f"{path}/{inner}" if inner else path
                n = 0 if binary else _count_lines_text(text)
                files.append(
                    _review_entry(path=full, kind="deleted", added=0, deleted=n, xy=" D")
                )
                deleted_total += n
            continue

        current = _read_text_capped(abs_path)
        if current is None and abs_path.is_file():
            # Binary file still present: list it so add/modify is not dropped.
            n = 0
            kind = "added" if not st.get("had") else ("renamed" if last == "rename" else "modified")
            files.append(
                _review_entry(
                    path,
                    kind=kind,
                    added=n,
                    deleted=0,
                    xy="??" if kind == "added" else " M",
                    untracked=kind == "added",
                )
            )
            continue
        text = current or ""
        if not st.get("had"):
            n = _count_lines_text(text)
            files.append(
                _review_entry(
                    path,
                    kind="added",
                    added=n,
                    deleted=0,
                    xy="??",
                    untracked=True,
                )
            )
            added_total += n
            continue
        old = _blob_text(st.get("blob"), workspace)
        if old is None:
            n = _count_lines_text(text)
            kind = "renamed" if last == "rename" else "modified"
            files.append(_review_entry(path, kind=kind, added=n, deleted=0, xy=" M"))
            added_total += n
            continue
        if old == text:
            continue
        added, deleted = _line_delta(old, text)
        kind = "renamed" if last == "rename" else "modified"
        files.append(
            _review_entry(path, kind=kind, added=added, deleted=deleted, xy=" M")
        )
        added_total += added
        deleted_total += deleted

    return {
        "files": files,
        "totals": {"files": len(files), "added": added_total, "deleted": deleted_total},
    }


def file_review_pair(
    workspace: Optional[Path],
    rel: str,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Original vs current text for one changed file in this conversation."""
    path = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not path or path.startswith("-"):
        raise ValueError("invalid path")
    ws = (workspace or get_settings().workspace).resolve()
    tracked = _collect_tracked(workspace, session_id=session_id)
    _prefix, st, inner = _find_tracked(path, tracked)
    if not st:
        raise ValueError(f"not a changed file: {path}")
    abs_path = ws / path
    last = st.get("op")
    gone = last == "delete" or not abs_path.exists()
    if gone:
        blob = st.get("delete_blob") or st.get("blob")
        root = _blob_root(blob, workspace)
        target = (root / inner) if (root is not None and inner) else root
        if target is not None and target.is_dir():
            return {
                "path": path,
                "old": "",
                "new": "",
                "kind": "deleted",
                "is_new": False,
                "is_deleted": True,
                "binary": False,
            }
        old = ""
        binary = False
        if target is not None and target.is_file():
            old, binary = _read_blob_file(target)
        return {
            "path": path,
            "old": old,
            "new": "",
            "kind": "deleted",
            "is_new": False,
            "is_deleted": True,
            "binary": binary,
        }
    current = _read_text_capped(abs_path)
    if current is None and abs_path.exists() and abs_path.is_file():
        return {
            "path": path,
            "old": "",
            "new": "",
            "kind": st.get("kind") or "modified",
            "is_new": not st.get("had"),
            "is_deleted": False,
            "binary": True,
        }
    text = current or ""
    if not st.get("had"):
        return {
            "path": path,
            "old": "",
            "new": text,
            "kind": "added",
            "is_new": True,
            "is_deleted": False,
            "binary": False,
        }
    old = _blob_text(st.get("blob"), workspace) or ""
    kind = "renamed" if last == "rename" else "modified"
    return {
        "path": path,
        "old": old,
        "new": text,
        "kind": kind,
        "is_new": False,
        "is_deleted": False,
        "binary": False,
    }
