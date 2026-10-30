"""Local-only auth token for the Sidekick console.

Token is stored under src/data/.local_token and required on API requests
via the X-Sidekick-Token header (except health + bootstrap + static UI).
"""

from __future__ import annotations

import ipaddress
import secrets
from pathlib import Path

from ..core.config import ROOT

TOKEN_PATH = ROOT / "data" / ".local_token"
TOKEN_HEADER = "x-sidekick-token"

_cached: str | None = None


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower().split("%")[0]
    if h in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def is_loopback_bind(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in ("127.0.0.1", "localhost", "::1"):
        return True
    # Binding 0.0.0.0 / :: is not loopback-only
    return False


def peer_is_loopback(client_host: str | None) -> bool:
    return is_loopback_host(client_host)


def load_or_create_token() -> str:
    global _cached
    if _cached:
        return _cached
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        raw = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if raw:
            _cached = raw
            return raw
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    try:
        TOKEN_PATH.chmod(0o600)
    except OSError:
        pass
    _cached = token
    return token


def get_token() -> str:
    return load_or_create_token()


def token_matches(provided: str | None) -> bool:
    if not provided:
        return False
    expected = get_token()
    return secrets.compare_digest(provided.strip(), expected)
