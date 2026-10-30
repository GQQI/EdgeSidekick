"""Apply model setup onto runtime settings + UI update helpers."""
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

from .models import ModelEntry, ModelProvider, ModelRef, ModelSetup
from .paths import is_auto_ref, _uid
from .persist import (
    load_model_config,
    save_model_config,
    _ensure_refs,
    _default_setup,
)

def apply_to_settings(settings: Any, cfg: ModelSetup | None = None) -> Any:
    cfg = cfg or load_model_config()
    main_name, main_model, main_url, main_key = cfg.resolve(cfg.main)
    if not str(main_key or "").strip():
        keyed_prov, keyed = cfg.first_keyed_entry()
        if keyed:
            main_name = keyed_prov.name if keyed_prov else main_name
            main_model = keyed.name or main_model
            main_url = (keyed.base_url or main_url or "").rstrip("/")
            main_key = keyed.api_key
    _, sub_model, sub_url, sub_key = cfg.resolve(cfg.subagent)
    _, compress_model, compress_url, compress_key = cfg.resolve(cfg.compress)

    settings.api_key = main_key
    settings.base_url = main_url or settings.base_url
    settings.model = main_model or settings.model
    settings.subagent_model = sub_model or main_model or settings.model
    settings.compress_model = compress_model or sub_model or main_model or settings.model
    settings.review_model = settings.compress_model
    settings.demo_mode = not bool(str(main_key or "").strip())
    settings.reasoning_effort = cfg.reasoning_effort
    settings.thinking_enabled = bool(cfg.thinking_enabled)
    settings.temperature = float(cfg.temperature)
    settings.provider = main_name or getattr(settings, "provider", "")

    try:
        from ...session.tenant_context import apply_knowledge_to_settings

        apply_knowledge_to_settings(settings)
    except Exception:
        pass

    # Optional per-role credentials (used when subagent/compress differ from main)
    settings.subagent_api_key = sub_key or main_key
    settings.subagent_base_url = (sub_url or main_url or "").rstrip("/")
    settings.compress_api_key = compress_key or sub_key or main_key
    settings.compress_base_url = (compress_url or sub_url or main_url or "").rstrip("/")
    # Working set is a fraction of the provider window (about 45%, capped at
    # 96k). The full window stays available via infer_context_limit for display.
    # Compression still keys off this working set, not the raw window.
    from ...core.config import infer_context_limit as _infer_window
    from ...core.config import working_context_budget

    window = int(_infer_window(getattr(settings, "model", "") or "") or 0)
    explicit = int(os.getenv("META_CONTEXT_LIMIT", "0") or "0")
    settings.context_limit = working_context_budget(window, explicit)
    if os.getenv("META_KEEP_RECENT") is None:
        settings.keep_recent_tokens = max(16_000, min(int(settings.context_limit) // 3, 32_000))
    return settings


def _index_keys(setup: ModelSetup) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for p in setup.providers:
        for m in p.models:
            if m.api_key.strip():
                out[(p.id, m.id)] = m.api_key
    return out


def _merge_keys(incoming: ModelSetup, previous: ModelSetup) -> None:
    """Keep stored API keys when the UI sends blank / masked placeholders."""
    prev = _index_keys(previous)
    for p in incoming.providers:
        for m in p.models:
            key = (m.api_key or "").strip()
            if not key or key == "***":
                m.api_key = prev.get((p.id, m.id), "")


def update_model_config(patch: dict[str, Any]) -> ModelSetup:
    current = load_model_config()

    # Full v3 payload from the settings UI
    if isinstance(patch.get("providers"), list):
        incoming = _from_v3(patch)
        _merge_keys(incoming, current)
        if "reasoning_effort" in patch:
            incoming.reasoning_effort = str(patch.get("reasoning_effort") or "medium")
        if "thinking_enabled" in patch:
            incoming.thinking_enabled = bool(patch.get("thinking_enabled"))
        if "temperature" in patch and patch.get("temperature") is not None:
            incoming.temperature = float(patch["temperature"])
        if patch.get("demo_mode") is True and not incoming.any_key_set():
            for p in incoming.providers:
                for m in p.models:
                    m.api_key = ""
        _ensure_refs(incoming)
        save_model_config(incoming)
        return incoming

    # Legacy flat patch
    flat = current.to_storage()
    # Project to flat fields via resolved main, then apply patch onto a synthetic legacy dict
    legacy = {
        "provider": next(
            (p.vendor for p in current.providers if p.id == current.main.provider_id),
            "custom",
        ),
        "api_key": current.resolve(current.main)[3],
        "base_url": current.resolve(current.main)[2],
        "model": current.resolve(current.main)[1],
        "subagent_model": current.resolve(current.subagent)[1],
        "compress_model": current.resolve(current.compress)[1],
        "review_model": current.resolve(current.compress)[1],
        "reasoning_effort": current.reasoning_effort,
        "thinking_enabled": current.thinking_enabled,
        "demo_mode": current.demo_mode,
        "temperature": current.temperature,
    }
    for k, v in patch.items():
        if k == "api_key" and (v is None or v == "" or v == "***"):
            continue
        if k in legacy:
            legacy[k] = v
    if patch.get("demo_mode") is True and not str(
        patch.get("api_key") or legacy.get("api_key") or ""
    ).strip():
        legacy["api_key"] = ""
        legacy["demo_mode"] = True

    migrated = _from_legacy_flat(legacy)
    # Prefer keeping existing multi-provider tree if only flat fields changed
    # and providers already exist — update active models' credentials instead.
    if current.providers:
        _, main_entry = current.find_entry(current.main)
        if main_entry is not None:
            if "api_key" in patch and str(patch.get("api_key") or "").strip() not in ("", "***"):
                main_entry.api_key = str(patch["api_key"])
            if "base_url" in patch and patch.get("base_url") is not None:
                main_entry.base_url = str(patch["base_url"])
            if "model" in patch and patch.get("model"):
                main_entry.name = str(patch["model"])
        if "reasoning_effort" in patch:
            current.reasoning_effort = str(patch.get("reasoning_effort") or "medium")
        if "thinking_enabled" in patch:
            current.thinking_enabled = bool(patch.get("thinking_enabled"))
        if "temperature" in patch and patch.get("temperature") is not None:
            current.temperature = float(patch["temperature"])
        _ensure_refs(current)
        save_model_config(current)
        return current

    save_model_config(migrated)
    return migrated


def select_model_role(role: str, provider_id: str, model_id: str) -> ModelSetup:
    cfg = load_model_config()
    if provider_id == AUTO_ID or model_id == AUTO_ID:
        ref = ModelRef(provider_id=AUTO_ID, model_id=AUTO_ID)
        if role == "main":
            cfg.main = ref
        elif role == "subagent":
            cfg.subagent = ref
            cfg.compress = ModelRef(AUTO_ID, AUTO_ID)
        elif role == "compress":
            cfg.compress = ref
        else:
            raise ValueError(f"unknown role: {role}")
        save_model_config(cfg)
        return cfg
    ref = ModelRef(provider_id=provider_id, model_id=model_id)
    prov, entry = cfg.find_entry(ref)
    if not entry:
        # Allow selecting by model name as fallback (UI sometimes sends name)
        for p in cfg.providers:
            if p.id != provider_id:
                continue
            for m in p.models:
                if m.id == model_id or m.name == model_id:
                    ref = ModelRef(p.id, m.id)
                    prov, entry = p, m
                    break
    if not entry:
        raise ValueError("model not found")
    if role == "main":
        cfg.main = ref
    elif role == "subagent":
        cfg.subagent = ref
        cfg.compress = ModelRef(ref.provider_id, ref.model_id)
    elif role == "compress":
        cfg.compress = ref
    else:
        raise ValueError(f"unknown role: {role}")
    save_model_config(cfg)
    return cfg

