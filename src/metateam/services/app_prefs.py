"""Account-level UI / runtime preferences (not model or workspace)."""

from __future__ import annotations

import json
from typing import Any

from .tenant_context import tenant_prefs_path

_DEFAULTS = {
    "auto_approve_tools": False,
    "systemone_url": "",
    "systemone_model": "",
    "systemone_key": "",
}

_SYSTEMONE_KEYS = ("systemone_url", "systemone_model", "systemone_key")


def load_app_prefs() -> dict[str, Any]:
    path = tenant_prefs_path()
    data: dict[str, Any] = dict(_DEFAULTS)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    data["auto_approve_tools"] = bool(data.get("auto_approve_tools"))
    for key in _SYSTEMONE_KEYS:
        data[key] = str(data.get(key) or "").strip()
    return data


def save_app_prefs(patch: dict[str, Any] | None = None) -> dict[str, Any]:
    current = load_app_prefs()
    if patch:
        if "auto_approve_tools" in patch:
            current["auto_approve_tools"] = bool(patch.get("auto_approve_tools"))
        for key in _SYSTEMONE_KEYS:
            if key in patch:
                current[key] = str(patch.get(key) or "").strip()
    path = tenant_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def auto_approve_tools() -> bool:
    return bool(load_app_prefs().get("auto_approve_tools"))


def set_auto_approve_tools(enabled: bool) -> bool:
    return bool(save_app_prefs({"auto_approve_tools": bool(enabled)}).get("auto_approve_tools"))
