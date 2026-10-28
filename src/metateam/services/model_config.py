"""Persisted multi-provider model settings (UI-editable).

Supports setup version 3:
  { version, providers[], main/subagent/compress refs, reasoning_effort, ... }

Legacy flat files ({ provider, api_key, base_url, model, ... }) are migrated on load.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import ROOT
from .secret_store import decrypt_secret, encrypt_secret, looks_encrypted
from .tenant_context import (
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


def _model_power_tier(name: str) -> int:
    """1=fast/cheap, 2=balanced, 3=flagship — guessed from the model name only."""
    n = (name or "").lower()
    if any(mk in n for mk in _FAST_MODEL_MARKERS):
        return 1
    if any(mk in n for mk in _POWER_MODEL_MARKERS):
        return 3
    return 2


_COMPLEX_TASK_RE = re.compile(
    r"(重构|架构|设计模式|debug|调试|排查|故障|性能优化|优化性能|完整实现|完整的项目|"
    r"整个项目|多个文件|端到端|深入分析|系统性|全面重写|大型|综合|全部文件|所有文件|"
    r"refactor|architect(?:ure)?|end-to-end|comprehensive|entire project|"
    r"multiple files|deep dive)",
    re.IGNORECASE,
)
_SIMPLE_TASK_RE = re.compile(
    r"^(翻译|总结|摘要|润色|校对|改写|重写一句|简单问一下|solve|translate|summarize|"
    r"define|what is|explain briefly)",
    re.IGNORECASE,
)
_CODE_HINT_RE = re.compile(r"```|\bdef \b|\bclass \w+|function\s*\(|=>|#include|import [a-zA-Z_]")

_TIER_BY_COMPLEXITY = {"simple": 1, "medium": 2, "complex": 3}


def _task_complexity(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "simple"
    length = len(t)
    if _CODE_HINT_RE.search(t) or _COMPLEX_TASK_RE.search(t) or length > 400:
        return "complex"
    if _SIMPLE_TASK_RE.search(t) or length < 60:
        return "simple"
    return "medium"


def auto_pick_ref(
    setup: ModelSetup, task_text: str, *, force_tier: int | None = None
) -> ModelRef | None:
    """Best-effort 'Auto' pick among the user's keyed models for this task."""
    candidates = [
        (p, m) for p in setup.providers for m in p.models if (m.api_key or "").strip()
    ]
    if not candidates:
        return None
    tier = force_tier if force_tier is not None else _TIER_BY_COMPLEXITY[_task_complexity(task_text)]

    def score(pair: tuple[ModelProvider, ModelEntry]) -> tuple[int, int]:
        _, entry = pair
        return (abs(_model_power_tier(entry.name) - tier), -len(entry.name or ""))

    best_provider, best_entry = min(candidates, key=score)
    return ModelRef(provider_id=best_provider.id, model_id=best_entry.id)


def resolve_setup_for_task(setup: ModelSetup, task_text: str) -> ModelSetup:
    """Copy of `setup` with any 'Auto' role resolved to a concrete pick for this task."""
    if not (is_auto_ref(setup.main) or is_auto_ref(setup.subagent) or is_auto_ref(setup.compress)):
        return setup
    resolved = copy.deepcopy(setup)
    if is_auto_ref(resolved.main):
        resolved.main = auto_pick_ref(setup, task_text) or setup.main
    if is_auto_ref(resolved.subagent):
        resolved.subagent = auto_pick_ref(setup, task_text) or setup.subagent
    if is_auto_ref(resolved.compress):
        # Compression summaries are cheap busywork — bias toward the fastest keyed model.
        resolved.compress = (
            auto_pick_ref(setup, task_text, force_tier=1) or resolved.subagent or setup.compress
        )
    return resolved


def apply_auto_for_task(settings: Any, task_text: str, cfg: ModelSetup | None = None) -> bool:
    """Re-apply model config onto `settings`, resolving any 'Auto' role for this task.

    Returns True when something was Auto (caller should rebuild its LLM clients).
    Never persisted — Auto's pick is per-turn; model.json keeps the 'auto' ref.
    """
    cfg = cfg or load_model_config()
    if not (is_auto_ref(cfg.main) or is_auto_ref(cfg.subagent) or is_auto_ref(cfg.compress)):
        return False
    apply_to_settings(settings, resolve_setup_for_task(cfg, task_text))
    return True


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
        from ..core.logutil import get_logger, log_exception

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
        from .tenant_context import apply_knowledge_to_settings

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
    from ..core.config import infer_context_limit as _infer_window
    from ..core.config import working_context_budget

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
