"""Auto model picking by task complexity."""
from __future__ import annotations

import re
from typing import Any

from .models import ModelRef, ModelSetup
from .paths import is_auto_ref

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

