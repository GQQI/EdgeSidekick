"""Workspace filesystem undo stack — snapshot before mutating ops."""

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

from ..core.config import get_settings

# Keep enough records for long conversations so the composer review bar can
# show the whole-session net diff (not just the last few tool writes).
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


def _workspace_key(workspace: Path) -> str:
    return hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:16]


def _undo_root(workspace: Optional[Path] = None) -> Path:
    settings = get_settings()
    if workspace is not None:
        ws = Path(workspace).resolve()
    else:
        # Honor bind_active_workspace / workspace_override from panel API calls.
        from .fs_api import workspace_root

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
    from ..core.pathutil import is_relative_to, resolve_path
    from .fs_api import workspace_root

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
