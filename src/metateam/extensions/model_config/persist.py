"""Load / save / migrate model setup files."""
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
from .paths import (
    _config_path,
    _global_config_path,
    _mask_key,
    _uid,
    _workspace_config_path,
    is_auto_ref,
)

def _parse_ref(raw: Any) -> ModelRef:
    if isinstance(raw, dict):
        return ModelRef(
            provider_id=str(raw.get("provider_id") or ""),
            model_id=str(raw.get("model_id") or ""),
        )
    return ModelRef()


def _parse_entry(raw: dict[str, Any]) -> ModelEntry:
    key = str(raw.get("api_key") or "")
    try:
        max_tokens = int(raw.get("max_tokens") or 0)
    except (TypeError, ValueError):
        max_tokens = 0
    return ModelEntry(
        id=str(raw.get("id") or _uid("mdl")),
        name=str(raw.get("name") or ""),
        base_url=str(raw.get("base_url") or ""),
        api_key=decrypt_secret(key),
        max_tokens=max_tokens,
    )


def _parse_provider(raw: dict[str, Any]) -> ModelProvider:
    models_raw = raw.get("models") if isinstance(raw.get("models"), list) else []
    return ModelProvider(
        id=str(raw.get("id") or _uid("prov")),
        name=str(raw.get("name") or "Provider"),
        vendor=str(raw.get("vendor") or "openai"),
        market_id=str(raw.get("market_id") or raw.get("vendor") or "custom"),
        models=[_parse_entry(m) for m in models_raw if isinstance(m, dict)],
    )


def _from_v3(data: dict[str, Any]) -> ModelSetup:
    providers_raw = data.get("providers") if isinstance(data.get("providers"), list) else []
    providers = [_parse_provider(p) for p in providers_raw if isinstance(p, dict)]
    setup = ModelSetup(
        version=3,
        providers=providers,
        main=_parse_ref(data.get("main")),
        subagent=_parse_ref(data.get("subagent")),
        compress=_parse_ref(data.get("compress")),
        reasoning_effort=str(data.get("reasoning_effort") or "medium"),
        thinking_enabled=bool(data.get("thinking_enabled", True)),
        temperature=float(data.get("temperature") or 0.2),
        demo_mode=bool(data.get("demo_mode", True)),
    )
    _ensure_refs(setup)
    setup.demo_mode = not setup.any_key_set()
    return setup


def _from_legacy_flat(data: dict[str, Any]) -> ModelSetup:
    provider_name = str(data.get("provider") or "deepseek")
    base_url = str(data.get("base_url") or "")
    api_key = str(data.get("api_key") or "")
    names = [
        str(data.get("model") or "").strip(),
        str(data.get("subagent_model") or "").strip(),
        str(data.get("compress_model") or "").strip(),
        str(data.get("review_model") or "").strip(),
    ]
    unique: list[str] = []
    for n in names:
        if n and n not in unique:
            unique.append(n)
    if not unique:
        unique = ["model"]

    prov_id = _uid("prov")
    models = [
        ModelEntry(
            id=_uid("mdl"),
            name=n,
            base_url=base_url,
            api_key=api_key,
        )
        for n in unique
    ]
    by_name = {m.name: m for m in models}
    main_m = by_name.get(str(data.get("model") or ""), models[0])
    sub_m = by_name.get(str(data.get("subagent_model") or data.get("model") or ""), models[0])
    cmp_m = by_name.get(
        str(data.get("compress_model") or data.get("subagent_model") or data.get("model") or ""),
        models[0],
    )
    market = "deepseek" if provider_name == "deepseek" else "custom"
    setup = ModelSetup(
        version=3,
        providers=[
            ModelProvider(
                id=prov_id,
                name=provider_name,
                vendor="deepseek" if provider_name == "deepseek" else "openai",
                market_id=market,
                models=models,
            )
        ],
        main=ModelRef(prov_id, main_m.id),
        subagent=ModelRef(prov_id, sub_m.id),
        compress=ModelRef(prov_id, cmp_m.id),
        reasoning_effort=str(data.get("reasoning_effort") or "medium"),
        thinking_enabled=bool(data.get("thinking_enabled", True)),
        temperature=float(data.get("temperature") or 0.2),
        demo_mode=not bool(api_key.strip()),
    )
    return setup


def _default_setup() -> ModelSetup:
    env_key = (
        os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    ).strip()
    base_url = os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = os.getenv("OPENAI_MODEL") or "deepseek-v4-pro"
    prov_id = _uid("prov")
    mdl = ModelEntry(id=_uid("mdl"), name=model, base_url=base_url, api_key=env_key)
    return ModelSetup(
        providers=[
            ModelProvider(
                id=prov_id,
                name="DeepSeek",
                vendor="deepseek",
                market_id="deepseek",
                models=[mdl],
            )
        ],
        main=ModelRef(prov_id, mdl.id),
        subagent=ModelRef(prov_id, mdl.id),
        compress=ModelRef(prov_id, mdl.id),
        demo_mode=not bool(env_key),
    )


def _ensure_refs(setup: ModelSetup) -> None:
    """Point empty/broken role refs at the first available model."""
    first_p = next((p for p in setup.providers if p.models), None)
    first_m = first_p.models[0] if first_p else None
    fallback = (
        ModelRef(first_p.id, first_m.id) if first_p and first_m else ModelRef()
    )

    def fix(ref: ModelRef) -> ModelRef:
        if is_auto_ref(ref):
            return ref
        prov, entry = setup.find_entry(ref)
        if entry:
            return ref
        return ModelRef(fallback.provider_id, fallback.model_id)

    setup.main = fix(setup.main)
    setup.subagent = fix(setup.subagent)
    setup.compress = fix(setup.compress)


def _read_setup(path: Path) -> ModelSetup | None:
    if not path.exists():
        return None
    try:
        file_data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(file_data, dict):
            return None
        if isinstance(file_data.get("providers"), list):
            setup = _from_v3(file_data)
        else:
            setup = _from_legacy_flat(file_data)
        needs_rewrite = False
        if not isinstance(file_data.get("providers"), list):
            needs_rewrite = True
        else:
            for p in file_data.get("providers") or []:
                if not isinstance(p, dict):
                    continue
                for m in p.get("models") or []:
                    if isinstance(m, dict) and m.get("api_key") and not looks_encrypted(
                        str(m.get("api_key") or "")
                    ):
                        needs_rewrite = True
                        break
        if needs_rewrite:
            save_model_config(setup, persist_workspace=path == _workspace_config_path())
        return setup
    except Exception as exc:
        from ...core.logutil import get_logger, log_exception

        log_exception(
            get_logger("metateam.model"),
            f"load_model_config failed for {path}; trying fallback",
            exc,
        )
        return None


def _setup_main_key(setup: ModelSetup | None) -> str:
    if not setup:
        return ""
    if is_auto_ref(setup.main):
        return "auto" if setup.any_key_set() else ""
    return (setup.resolve(setup.main)[3] or "").strip()


def _pick_model_setup(
    workspace: ModelSetup | None, account: ModelSetup | None
) -> ModelSetup:
    """Workspace override wins only when its main model actually has a key.

    A stale workspace file (Demo / empty keys) must not hide the account
    default — otherwise new chats fall back to Demo while an older in-memory
    session still has the key from a previous save.
    """
    if workspace and _setup_main_key(workspace):
        return workspace
    if account and (_setup_main_key(account) or account.any_key_set()):
        return account
    return workspace or account or _default_setup()


def load_model_config() -> ModelSetup:
    ws_path = _workspace_config_path()
    workspace = _read_setup(ws_path) if ws_path else None
    account = _read_setup(_global_config_path())
    return _pick_model_setup(workspace, account)


def save_model_config(cfg: ModelSetup, *, persist_workspace: bool = True) -> None:
    """Write this workspace's override (if any) and refresh the account default.

    Workspaces without an override file inherit the account default, so the
    first project's models show up as the default in the next project.
    """
    cfg.demo_mode = not cfg.any_key_set()
    text = json.dumps(cfg.to_storage(), ensure_ascii=False, indent=2) + "\n"
    default_path = tenant_model_path(get_user_id())
    default_path.parent.mkdir(parents=True, exist_ok=True)
    default_path.write_text(text, encoding="utf-8")
    if persist_workspace:
        ws_path = _workspace_config_path()
        if ws_path:
            ws_path.parent.mkdir(parents=True, exist_ok=True)
            ws_path.write_text(text, encoding="utf-8")

