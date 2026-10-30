"""Config path helpers and small utilities."""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.config import ROOT
from ...session.secret_store import decrypt_secret, encrypt_secret, looks_encrypted
from ...session.tenant_context import (
    DEFAULT_USER_ID,
    get_user_id,
    tenant_model_path,
    tenant_saved_workspace_path,
    tenant_workspace_model_path,
)

def _global_config_path() -> Path:
    """Account-level default (used when a workspace has no override yet)."""
    uid = get_user_id()
    path = tenant_model_path(uid)
    if path.exists():
        return path
    legacy = ROOT / "data" / "model.json"
    if uid == DEFAULT_USER_ID and legacy.exists():
        return legacy
    return path


def _workspace_config_path() -> Path | None:
    ws = tenant_saved_workspace_path()
    if not ws:
        return None
    return tenant_workspace_model_path(ws)


def _config_path() -> Path:
    """Prefer this workspace's model.json; else account default."""
    ws_path = _workspace_config_path()
    if ws_path and ws_path.exists():
        return ws_path
    return _global_config_path()


# Back-compat alias (legacy global path)
CONFIG_PATH = ROOT / "data" / "model.json"

# Sentinel ref for "Auto" — pick the best configured model per task (see
# auto_pick_ref below). Stored in model.json like any other ModelRef so it
# survives save/reload; only resolved to a concrete model at request time.
AUTO_ID = "auto"


def is_auto_ref(ref: "ModelRef | None") -> bool:
    return bool(ref) and (ref.provider_id == AUTO_ID or ref.model_id == AUTO_ID)


VENDOR_TEMPLATES: dict[str, dict[str, Any]] = {
    "custom": {
        "name": "OpenAI-API-Compatible",
        "vendor": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": [],
    },
    "openai": {
        "name": "OpenAI",
        "vendor": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4o"],
    },
    "anthropic": {
        "name": "Anthropic",
        "vendor": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-5"],
    },
    "ollama": {
        "name": "Ollama",
        "vendor": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": [],
    },
}


def _uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _mask_key(key: str) -> tuple[str, bool]:
    key = str(key or "")
    if len(key) > 10:
        return key[:6] + "…" + key[-4:], True
    return "", bool(key.strip())

