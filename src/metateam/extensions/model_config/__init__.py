"""Persisted multi-provider model settings (package facade)."""
from __future__ import annotations

from .models import ModelEntry, ModelProvider, ModelRef, ModelSetup
from .paths import (
    is_auto_ref,
    _global_config_path,
    _workspace_config_path,
    _config_path,
    _uid,
    _mask_key,
)
from .auto import (
    auto_pick_ref,
    resolve_setup_for_task,
    apply_auto_for_task,
    _model_power_tier,
    _task_complexity,
)
from .persist import (
    load_model_config,
    save_model_config,
    _parse_ref,
    _parse_entry,
    _parse_provider,
    _from_v3,
    _from_legacy_flat,
    _default_setup,
    _ensure_refs,
    _read_setup,
    _setup_main_key,
    _pick_model_setup,
)
from .apply import (
    apply_to_settings,
    update_model_config,
    select_model_role,
    _index_keys,
    _merge_keys,
)

__all__ = [
    "ModelEntry",
    "ModelProvider",
    "ModelRef",
    "ModelSetup",
    "is_auto_ref",
    "auto_pick_ref",
    "resolve_setup_for_task",
    "apply_auto_for_task",
    "load_model_config",
    "save_model_config",
    "apply_to_settings",
    "update_model_config",
    "select_model_role",
]
