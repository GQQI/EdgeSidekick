"""Per-request tenant (local multi-user) context."""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional

from ..core.config import ROOT

# "default" = legacy single-user / pre-setup bucket
DEFAULT_USER_ID = "default"

_current_user_id: ContextVar[str] = ContextVar("sidekick_user_id", default=DEFAULT_USER_ID)
_current_username: ContextVar[str] = ContextVar("sidekick_username", default="")
_current_session_id: ContextVar[str] = ContextVar("sidekick_session_id", default="")


def get_user_id() -> str:
    return _current_user_id.get() or DEFAULT_USER_ID


def get_username() -> str:
    return _current_username.get() or ""


def set_user(user_id: str, username: str = "") -> None:
    _current_user_id.set(user_id or DEFAULT_USER_ID)
    _current_username.set(username or "")


def reset_user() -> None:
    _current_user_id.set(DEFAULT_USER_ID)
    _current_username.set("")


def get_session_id() -> str:
    return (_current_session_id.get() or "").strip()


def bind_session_id(session_id: str) -> Any:
    return _current_session_id.set((session_id or "").strip())


def reset_session_id(token: Any) -> None:
    try:
        _current_session_id.reset(token)
    except Exception:
        _current_session_id.set("")


def tenants_root() -> Path:
    p = ROOT / "data" / "tenants"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tenant_dir(user_id: Optional[str] = None) -> Path:
    uid = (user_id or get_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    # sanitize path segment
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in uid)[:64] or DEFAULT_USER_ID
    p = tenants_root() / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def tenant_model_path(user_id: Optional[str] = None) -> Path:
    """Account-level default model.json (inherited by workspaces without an override)."""
    return tenant_dir(user_id) / "model.json"


def workspace_settings_key(workspace_path: str | Path) -> str:
    """Stable folder id for a host path (case-insensitive on Windows)."""
    try:
        resolved = Path(workspace_path).expanduser().resolve()
    except Exception:
        resolved = Path(str(workspace_path))
    raw = str(resolved).replace("\\", "/").rstrip("/").lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def tenant_workspace_model_path(
    workspace_path: str | Path,
    user_id: Optional[str] = None,
) -> Path:
    key = workspace_settings_key(workspace_path)
    return tenant_dir(user_id) / "workspaces" / key / "model.json"


def tenant_saved_workspace_path(user_id: Optional[str] = None) -> Optional[Path]:
    """Active workspace folder from workspace.json, if it exists on disk."""
    state = tenant_workspace_path(user_id)
    if not state.exists():
        return None
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
        raw = str((data or {}).get("path") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser().resolve()
        return path if path.is_dir() else None
    except Exception:
        return None


def tenant_workspace_path(user_id: Optional[str] = None) -> Path:
    return tenant_dir(user_id) / "workspace.json"


def tenant_mcp_path(user_id: Optional[str] = None) -> Path:
    return tenant_dir(user_id) / "mcp.json"


def tenant_prefs_path(user_id: Optional[str] = None) -> Path:
    return tenant_dir(user_id) / "prefs.json"


def tenant_schedules_path(user_id: Optional[str] = None) -> Path:
    return tenant_dir(user_id) / "schedules.json"


def tenant_skills_dir(user_id: Optional[str] = None) -> Path:
    p = tenant_dir(user_id) / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tenant_memory_file(user_id: Optional[str] = None) -> Path:
    p = tenant_dir(user_id) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p / "MEMORY.md"


def ensure_tenant_knowledge(user_id: Optional[str] = None) -> tuple[Path, Path]:
    """Per-user skills/ + memory/MEMORY.md, seeded from bundled/legacy defaults once."""
    import shutil

    from ..core.config import ROOT, SRC_ROOT

    uid = user_id or get_user_id()
    skills = tenant_skills_dir(uid)
    mem = tenant_memory_file(uid)

    def _copy_skill_trees(src: Path, dest: Path) -> None:
        if not src.is_dir():
            return
        try:
            if src.resolve() == dest.resolve():
                return
        except OSError:
            return
        for skill_md in src.rglob("SKILL.md"):
            rel = skill_md.parent.relative_to(src)
            target = dest / rel
            if target.exists():
                continue
            try:
                shutil.copytree(skill_md.parent, target, dirs_exist_ok=True)
            except OSError:
                continue

    if not any(skills.rglob("SKILL.md")):
        if uid == DEFAULT_USER_ID:
            _copy_skill_trees(ROOT / "skills", skills)
        _copy_skill_trees(SRC_ROOT / "skills", skills)

    # Never overwrite an existing library or MEMORY.md on install/upgrade.
    lib_path = mem.parent / "library.json"
    if not mem.exists() and not lib_path.is_file():
        candidates = []
        if uid == DEFAULT_USER_ID:
            candidates.append(ROOT / "memory" / "MEMORY.md")
        candidates.append(SRC_ROOT / "memory" / "MEMORY.md")
        copied = False
        for src in candidates:
            if not src.is_file():
                continue
            try:
                if src.resolve() == mem.resolve():
                    continue
                shutil.copy2(src, mem)
                copied = True
                break
            except OSError:
                continue
        if not copied:
            mem.write_text(
                "# MEMORY\n\nDurable facts about the user and environment.\n",
                encoding="utf-8",
            )
    return skills, mem


def apply_knowledge_to_settings(settings: Any, user_id: Optional[str] = None) -> None:
    skills, mem = ensure_tenant_knowledge(user_id)
    settings.skills_dir = skills
    settings.memory_file = mem


def tenant_sessions_dir(user_id: Optional[str] = None) -> Path:
    """Sessions live under src/sessions/<user_id>/."""
    uid = (user_id or get_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in uid)[:64] or DEFAULT_USER_ID
    p = ROOT / "sessions" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p
