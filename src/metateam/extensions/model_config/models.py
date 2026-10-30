from __future__ import annotations
import copy
from .paths import VENDOR_TEMPLATES, _mask_key
from dataclasses import dataclass, field
from typing import Any
from ...session.secret_store import decrypt_secret, encrypt_secret, looks_encrypted

@dataclass
class ModelEntry:
    id: str
    name: str
    base_url: str = ""
    api_key: str = ""
    max_tokens: int = 0

    def masked_dict(self) -> dict[str, Any]:
        masked, set_ = _mask_key(self.api_key)
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": "",
            "api_key_masked": masked,
            "api_key_set": set_,
            "max_tokens": self.max_tokens or 0,
        }


@dataclass
class ModelProvider:
    id: str
    name: str
    vendor: str = "openai"
    market_id: str = "custom"
    models: list[ModelEntry] = field(default_factory=list)

    def masked_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "market_id": self.market_id,
            "models": [m.masked_dict() for m in self.models],
        }


@dataclass
class ModelRef:
    provider_id: str = ""
    model_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "model_id": self.model_id}


@dataclass
class ModelSetup:
    version: int = 3
    providers: list[ModelProvider] = field(default_factory=list)
    main: ModelRef = field(default_factory=ModelRef)
    subagent: ModelRef = field(default_factory=ModelRef)
    compress: ModelRef = field(default_factory=ModelRef)
    reasoning_effort: str = "medium"
    thinking_enabled: bool = True
    temperature: float = 0.2
    demo_mode: bool = True

    def find_entry(self, ref: ModelRef) -> tuple[ModelProvider | None, ModelEntry | None]:
        if not ref or not (ref.model_id or ref.provider_id):
            return None, None
        for p in self.providers:
            if ref.provider_id and p.id != ref.provider_id:
                continue
            for m in p.models:
                if m.id == ref.model_id or (ref.model_id and m.name == ref.model_id):
                    return p, m
        if ref.model_id:
            for p in self.providers:
                for m in p.models:
                    if m.id == ref.model_id or m.name == ref.model_id:
                        return p, m
        return None, None

    def first_keyed_entry(self) -> tuple[ModelProvider | None, ModelEntry | None]:
        for p in self.providers:
            for m in p.models:
                if (m.api_key or "").strip():
                    return p, m
        return None, None

    def resolve(self, ref: ModelRef) -> tuple[str, str, str, str]:
        """Return (provider_name, model_name, base_url, api_key)."""
        prov, entry = self.find_entry(ref)
        if not entry:
            return "", "", "", ""
        return (
            prov.name if prov else "",
            entry.name,
            (entry.base_url or "").rstrip("/"),
            entry.api_key or "",
        )

    def any_key_set(self) -> bool:
        return any(bool(m.api_key.strip()) for p in self.providers for m in p.models)

    def to_storage(self) -> dict[str, Any]:
        return {
            "version": 3,
            "providers": [
                {
                    "id": p.id,
                    "name": p.name,
                    "vendor": p.vendor,
                    "market_id": p.market_id,
                    "models": [
                        {
                            "id": m.id,
                            "name": m.name,
                            "base_url": m.base_url,
                            "api_key": encrypt_secret(m.api_key) if m.api_key else "",
                        }
                        for m in p.models
                    ],
                }
                for p in self.providers
            ],
            "main": self.main.as_dict(),
            "subagent": self.subagent.as_dict(),
            "compress": self.compress.as_dict(),
            "reasoning_effort": self.reasoning_effort,
            "thinking_enabled": self.thinking_enabled,
            "temperature": self.temperature,
            "demo_mode": self.demo_mode,
        }

    def masked(self) -> dict[str, Any]:
        d = self.to_storage()
        d["providers"] = [p.masked_dict() for p in self.providers]
        d["vendor_templates"] = copy.deepcopy(VENDOR_TEMPLATES)
        # Single source of truth: demo iff no provider key is configured
        d["demo_mode"] = not self.any_key_set()
        # Flat hints for health / older UI
        _, model, base_url, _ = self.resolve(self.main)
        _, sub_model, _, _ = self.resolve(self.subagent)
        _, compress_model, _, _ = self.resolve(self.compress)

        def _auto_preview(role_ref: ModelRef, resolved_name: str) -> str:
            """When a role is 'Auto' and unresolved, show what it would pick right now."""
            from .paths import is_auto_ref
            from .auto import auto_pick_ref
            if resolved_name or not is_auto_ref(role_ref):
                return resolved_name
            preview = auto_pick_ref(self, "")
            if not preview:
                return resolved_name
            _, entry = self.find_entry(preview)
            return entry.name if entry else resolved_name

        model = _auto_preview(self.main, model)
        sub_model = _auto_preview(self.subagent, sub_model)
        compress_model = _auto_preview(self.compress, compress_model)
        d["model"] = model
        d["subagent_model"] = sub_model or model
        d["compress_model"] = compress_model or sub_model or model
        d["base_url"] = base_url
        d["provider"] = next(
            (p.vendor for p in self.providers if p.id == self.main.provider_id),
            "",
        )
        return d


# --- Back-compat alias used by older imports ---
ModelConfig = ModelSetup


# --- "Auto" model selection (heuristic, not a real router) ---
#
# Cursor-style "pick the best model for this task" without any extra network
# call: classify the task text locally, then rank the user's *already keyed*
# models by a name-based power tier and take the closest match. Good enough to
# stop always burning the flagship model on "翻译这句话" style asks; nowhere
# near a real capability-aware router, but it degrades safely (falls back to
# the first keyed model — see apply_to_settings) when nothing matches.

_FAST_MODEL_MARKERS = (
    "mini", "flash", "lite", "turbo", "haiku", "nano", "small", "fast",
    "instant", "8b", "7b", "3b", "1.5b", "4b", "2b",
)
_POWER_MODEL_MARKERS = (
    "o1", "o3", "o4", "gpt-5", "gpt-4.5", "gpt-4.1", "opus", "claude-3.7",
    "claude-4", "deepseek-r1", "deepseek-v3", "gemini-2.5-pro", "gemini-2-pro",
    "gemini-1.5-pro", "qwen-max", "qwen3-max", "qwen2.5-max", "glm-4.6",
    "glm-4-plus", "kimi-k2", "minimax-m2", "minimax-m3", "grok-4", "grok-3",
    "ultra", "480b", "236b", "685b",
)
