"""Optional JSON session persistence (transcript + light meta)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


@dataclass
class SessionMeta:
    id: str
    created_at: str
    model: str
    workspace: str
    updated_at: str = ""
    user_id: str = ""
    title: str = ""
    # Social (会话版) one-shot sessions: hidden from the workbench history list.
    hidden: bool = False


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sessions_dir(root: Path, user_id: Optional[str] = None) -> Path:
    """Per-user sessions directory under ``root/sessions/<user_id>/``."""
    from .tenant_context import get_user_id

    uid = (user_id if user_id is not None else get_user_id()) or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in uid)[:64] or "default"
    d = Path(root) / "sessions" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_sessions_dir(root: Path) -> Path:
    d = root / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(
    root: Path,
    messages: list[dict[str, Any]],
    *,
    model: str,
    workspace: Path,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    title: str = "",
    extra: Optional[dict[str, Any]] = None,
    hidden: bool = False,
) -> Path:
    from .tenant_context import get_user_id

    uid = user_id if user_id is not None else get_user_id()
    sid = session_id or _now_id()
    path = sessions_dir(root, uid) / f"{sid}.json"
    created = _now_iso()
    prev_title = ""
    prev_hidden = False
    if path.exists():
        try:
            old_meta, _ = load_session(path)
            if old_meta.created_at:
                created = old_meta.created_at
            prev_title = old_meta.title or ""
            prev_hidden = old_meta.hidden
        except Exception:
            pass
    meta = SessionMeta(
        id=sid,
        created_at=created,
        updated_at=_now_iso(),
        model=model,
        workspace=str(workspace),
        user_id=uid,
        title=(title or prev_title or "").strip(),
        hidden=hidden or prev_hidden,
    )
    path.write_text(
        json.dumps(
            {
                "meta": asdict(meta),
                "messages": messages,
                **{k: v for k, v in (extra or {}).items() if v is not None},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_session(path: Path) -> tuple[SessionMeta, list[dict[str, Any]]]:
    meta, messages, _extra = load_session_bundle(path)
    return meta, messages


def load_session_bundle(
    path: Path,
) -> tuple[SessionMeta, list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    m = data.get("meta") or {}
    meta = SessionMeta(
        id=str(m.get("id") or path.stem),
        created_at=str(m.get("created_at") or ""),
        updated_at=str(m.get("updated_at") or ""),
        model=str(m.get("model") or ""),
        workspace=str(m.get("workspace") or ""),
        user_id=str(m.get("user_id") or ""),
        title=str(m.get("title") or ""),
        hidden=bool(m.get("hidden")),
    )
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        raise ValueError("invalid session: messages must be a list")
    extra = {k: v for k, v in data.items() if k not in ("meta", "messages")}
    return meta, messages, extra


def workspace_matches(session_ws: str, current_ws: str | Path) -> bool:
    """True when a session belongs to the active project folder."""
    a = str(session_ws or "").strip()
    b = str(current_ws or "").strip()
    if not a or not b:
        return False
    try:
        from .tenant_context import workspace_settings_key

        return workspace_settings_key(a) == workspace_settings_key(b)
    except Exception:
        return a.replace("\\", "/").rstrip("/").lower() == b.replace("\\", "/").rstrip("/").lower()


def latest_session(root: Path) -> Optional[Path]:
    files = sorted(sessions_dir(root).glob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None
