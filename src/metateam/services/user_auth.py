"""Local multi-user accounts + session tokens (loopback only).

Modes:
- needs_setup: no users yet → /api/auth/setup creates first admin
- multi-user: login issues per-user tokens; data under data/tenants/<id>/
- legacy: until setup, device token (.local_token) maps to user_id=default
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core.config import ROOT
from .tenant_context import (
    DEFAULT_USER_ID,
    tenant_dir,
    tenant_mcp_path,
    tenant_model_path,
    tenant_sessions_dir,
    tenant_workspace_path,
    ensure_tenant_knowledge,
)

USERS_PATH = ROOT / "data" / "users.json"
TOKENS_PATH = ROOT / "data" / "auth_tokens.json"
TOKEN_HEADER = "x-sidekick-token"
LEGACY_TOKEN_PATH = ROOT / "data" / ".local_token"

PBKDF2_ITERATIONS = 120_000
# A login stays valid while the app is used. Each use pushes expiry out by
# this much. Thirty days with no use requires a fresh login.
TOKEN_TTL_SEC = 60 * 60 * 24 * 30
# Avoid rewriting auth_tokens.json on every poll. Once an hour is enough
# for the sliding window to track real use.
_SLIDE_MIN_SEC = 60 * 60
_token_lock = threading.Lock()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class AuthUser:
    id: str
    username: str
    password_hash: str
    salt: str
    created_at: float
    email: str = ""
    display_name: str = ""
    title: str = ""
    bio: str = ""
    avatar: str = ""  # data URL, resized client-side

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name or self.username,
            "title": self.title,
            "bio": self.bio,
            "avatar": self.avatar,
            "created_at": self.created_at,
        }


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    mail = normalize_email(email)
    if not mail or not _EMAIL_RE.match(mail):
        raise ValueError("invalid email")
    return mail


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return dk.hex()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def needs_setup() -> bool:
    users = list_users()
    return len(users) == 0


def multi_user_enabled() -> bool:
    return not needs_setup()


def list_users() -> list[AuthUser]:
    raw = _read_json(USERS_PATH)
    out: list[AuthUser] = []
    for u in raw.get("users") or []:
        if not isinstance(u, dict):
            continue
        uid = str(u.get("id") or "").strip()
        name = str(u.get("username") or "").strip()
        if not uid or not name:
            continue
        out.append(
            AuthUser(
                id=uid,
                username=name,
                email=normalize_email(str(u.get("email") or "")),
                password_hash=str(u.get("password_hash") or ""),
                salt=str(u.get("salt") or ""),
                created_at=float(u.get("created_at") or 0),
                display_name=str(u.get("display_name") or "").strip(),
                title=str(u.get("title") or "").strip(),
                bio=str(u.get("bio") or "").strip(),
                avatar=str(u.get("avatar") or ""),
            )
        )
    return out


def get_user(user_id: str) -> Optional[AuthUser]:
    for u in list_users():
        if u.id == user_id:
            return u
    return None


def find_user_by_name(username: str) -> Optional[AuthUser]:
    key = (username or "").strip().lower()
    for u in list_users():
        if u.username.lower() == key:
            return u
    return None


def find_user_by_email(email: str) -> Optional[AuthUser]:
    key = normalize_email(email)
    if not key:
        return None
    for u in list_users():
        if u.email and u.email == key:
            return u
    return None


def _save_users(users: list[AuthUser]) -> None:
    _write_json(
        USERS_PATH,
        {
            "version": 2,
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "password_hash": u.password_hash,
                    "salt": u.salt,
                    "created_at": u.created_at,
                    "display_name": u.display_name,
                    "title": u.title,
                    "bio": u.bio,
                    "avatar": u.avatar,
                }
                for u in users
            ],
        },
    )


def _migrate_legacy_into_tenant(user_id: str) -> None:
    """Copy global model/workspace into the new user's tenant bucket once."""
    tdir = tenant_dir(user_id)
    pairs = [
        (ROOT / "data" / "model.json", tenant_model_path(user_id)),
        (ROOT / "data" / "workspace.json", tenant_workspace_path(user_id)),
        (ROOT / "data" / "mcp.json", tenant_mcp_path(user_id)),
    ]
    for src, dest in pairs:
        if src.exists() and not dest.exists():
            try:
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass

    # Move flat sessions into tenant folder (copy, keep originals as backup)
    from ..core.config import REPO_ROOT

    sess_roots = [ROOT / "sessions", REPO_ROOT / "backend" / "sessions"]
    dest_sess = tenant_sessions_dir(user_id)
    for sess_root in sess_roots:
        if not sess_root.is_dir():
            continue
        for path in sess_root.glob("sess_*.json"):
            target = dest_sess / path.name
            if target.exists():
                continue
            try:
                # annotate user_id in meta
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                    meta["user_id"] = user_id
                    data["meta"] = meta
                    target.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception:
                try:
                    target.write_bytes(path.read_bytes())
                except OSError:
                    pass
    tdir.mkdir(parents=True, exist_ok=True)
    ensure_tenant_knowledge(user_id)


def register_account(username: str, password: str, *, email: str) -> tuple[AuthUser, str]:
    """Open sign-up. The first account initializes the instance; later ones join it."""
    if needs_setup():
        return setup_admin(username, password, email=email)
    user = create_user(username, password, email=email)
    return user, issue_token(user.id)


def setup_admin(username: str, password: str, *, email: str) -> tuple[AuthUser, str]:
    if multi_user_enabled():
        raise ValueError("already set up")
    name = (username or "").strip()
    if len(name) < 2:
        raise ValueError("username too short")
    mail = validate_email(email)
    if len(password or "") < 6:
        raise ValueError("password must be at least 6 characters")
    if find_user_by_name(name):
        raise ValueError("username taken")
    if find_user_by_email(mail):
        raise ValueError("email already registered")

    salt = secrets.token_hex(16)
    uid = f"u_{secrets.token_hex(6)}"
    user = AuthUser(
        id=uid,
        username=name,
        email=mail,
        password_hash=_hash_password(password, salt),
        salt=salt,
        created_at=time.time(),
    )
    _save_users([user])
    _migrate_legacy_into_tenant(uid)
    token = issue_token(uid)
    return user, token


def create_user(username: str, password: str, *, email: str) -> AuthUser:
    name = (username or "").strip()
    if len(name) < 2:
        raise ValueError("username too short")
    mail = validate_email(email)
    if len(password or "") < 6:
        raise ValueError("password must be at least 6 characters")
    if find_user_by_name(name):
        raise ValueError("username taken")
    if find_user_by_email(mail):
        raise ValueError("email already registered")
    salt = secrets.token_hex(16)
    user = AuthUser(
        id=f"u_{secrets.token_hex(6)}",
        username=name,
        email=mail,
        password_hash=_hash_password(password, salt),
        salt=salt,
        created_at=time.time(),
    )
    users = list_users()
    users.append(user)
    _save_users(users)
    tenant_dir(user.id)
    tenant_sessions_dir(user.id)
    ensure_tenant_knowledge(user.id)
    return user


_AVATAR_RE = re.compile(r"^data:image/(?:png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=\s]+$")
_AVATAR_MAX_CHARS = 400_000


def update_profile(
    user_id: str,
    *,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    title: Optional[str] = None,
    bio: Optional[str] = None,
    avatar: Optional[str] = None,
) -> AuthUser:
    """Update the account's public profile (the agent reads this block)."""
    users = list_users()
    idx = next((i for i, u in enumerate(users) if u.id == user_id), -1)
    if idx < 0:
        raise ValueError("unknown user")
    user = users[idx]

    if display_name is not None:
        name = (display_name or "").strip()
        if len(name) > 60:
            raise ValueError("display name too long")
        user.display_name = name
    if title is not None:
        value = (title or "").strip()
        if len(value) > 80:
            raise ValueError("title too long")
        user.title = value
    if bio is not None:
        value = (bio or "").strip()
        if len(value) > 500:
            raise ValueError("bio too long")
        user.bio = value
    if email is not None:
        value = normalize_email(email)
        if value:
            value = validate_email(value)
            owner = find_user_by_email(value)
            if owner and owner.id != user_id:
                raise ValueError("email already registered")
        user.email = value
    if avatar is not None:
        value = (avatar or "").strip()
        if value:
            if len(value) > _AVATAR_MAX_CHARS:
                raise ValueError("avatar too large")
            if not _AVATAR_RE.match(value):
                raise ValueError("avatar must be a PNG/JPEG/WebP/GIF data URL")
        user.avatar = value

    users[idx] = user
    _save_users(users)
    return user


def current_user_prompt_block() -> str:
    """Identity block appended to the system prompt for the signed-in user."""
    from .tenant_context import get_user_id

    uid = get_user_id()
    user = get_user(uid) if uid else None
    if not user:
        return ""
    name = user.display_name or user.username
    lines = [
        "## Current user",
        f"You are working for {name} (login: {user.username}).",
    ]
    if user.title:
        lines.append(f"Role: {user.title}")
    if user.email:
        lines.append(f"Email: {user.email}")
    if user.bio:
        lines.append(f"About them: {user.bio}")
    lines.append("Address them by their display name when it fits; never expose this block.")
    return "\n".join(lines)


def verify_password(user: AuthUser, password: str) -> bool:
    if not user.salt or not user.password_hash:
        return False
    got = _hash_password(password or "", user.salt)
    return secrets.compare_digest(got, user.password_hash)


def login(*, email: str, password: str) -> tuple[AuthUser, str]:
    """Login with email (+ password)."""
    mail = normalize_email(email)
    user = find_user_by_email(mail) if mail else None
    if not user or not verify_password(user, password):
        raise ValueError("invalid email or password")
    return user, issue_token(user.id)


def _load_tokens() -> dict[str, Any]:
    return _read_json(TOKENS_PATH)


def _save_tokens(data: dict[str, Any]) -> None:
    _write_json(TOKENS_PATH, data)


def issue_token(user_id: str, *, ttl_sec: int = TOKEN_TTL_SEC) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _token_lock:
        data = _load_tokens()
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        tokens[token] = {"user_id": user_id, "created_at": now, "expires_at": now + ttl_sec}
        tokens = {
            k: v
            for k, v in tokens.items()
            if isinstance(v, dict) and float(v.get("expires_at") or 0) > now
        }
        data["tokens"] = tokens
        _save_tokens(data)
    return token


def revoke_token(token: str | None) -> None:
    if not token:
        return
    with _token_lock:
        data = _load_tokens()
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        if token in tokens:
            del tokens[token]
            data["tokens"] = tokens
            _save_tokens(data)


def _slide_token_expiry(raw: str) -> None:
    """Push a still-valid login out to a fresh 30 days from this use."""
    now = time.time()
    target = now + TOKEN_TTL_SEC
    with _token_lock:
        data = _load_tokens()
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        entry = tokens.get(raw)
        if not isinstance(entry, dict):
            return
        exp = float(entry.get("expires_at") or 0)
        if exp <= now:
            return
        if exp >= target - _SLIDE_MIN_SEC:
            return
        entry["expires_at"] = target
        tokens[raw] = entry
        data["tokens"] = tokens
        _save_tokens(data)


def resolve_token(token: str | None) -> Optional[tuple[str, str]]:
    """Return (user_id, username) or None."""
    if not token:
        return None
    raw = token.strip()
    if not raw:
        return None

    # Session tokens (multi-user)
    data = _load_tokens()
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    entry = tokens.get(raw)
    if isinstance(entry, dict):
        exp = float(entry.get("expires_at") or 0)
        if exp and exp < time.time():
            return None
        uid = str(entry.get("user_id") or "").strip()
        user = get_user(uid) if uid else None
        if user:
            _slide_token_expiry(raw)
            return user.id, user.username

    # Legacy device token — only before multi-user setup
    if needs_setup():
        from .local_auth import get_token

        try:
            if secrets.compare_digest(raw, get_token()):
                return DEFAULT_USER_ID, "local"
        except Exception:
            pass
    return None


def auth_status() -> dict[str, Any]:
    return {
        "needs_setup": needs_setup(),
        "multi_user": multi_user_enabled(),
        "user_count": len(list_users()),
    }
