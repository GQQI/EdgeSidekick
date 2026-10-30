"""Encrypt API keys at rest in model.json (Fernet + local secret file)."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from ..core.config import ROOT

SECRET_PATH = ROOT / "data" / ".secret_key"
ENC_PREFIX = "enc:"

_fernet = None


def _ensure_secret() -> bytes:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        raw = SECRET_PATH.read_bytes().strip()
        if raw:
            return raw
    # 32 url-safe base64-encoded bytes for Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(__import__("secrets").token_bytes(32)).digest())
    SECRET_PATH.write_bytes(key + b"\n")
    try:
        SECRET_PATH.chmod(0o600)
    except OSError:
        pass
    return key


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package required for API key encryption — pip install cryptography"
        ) from exc
    _fernet = Fernet(_ensure_secret())
    return _fernet


def encrypt_secret(plain: str) -> str:
    plain = plain or ""
    if not plain:
        return ""
    if plain.startswith(ENC_PREFIX):
        return plain
    token = _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_secret(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return value
    raw = value[len(ENC_PREFIX) :]
    try:
        return _get_fernet().decrypt(raw.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def looks_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(ENC_PREFIX)
