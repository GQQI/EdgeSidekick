"""Anti-Piling / Coherence Loop — turn policy without domain hardcoding.

Layers:
  Align   → reuse existing assets before creating parallels (overlay)
  Contract → declare reuse / config / control-flow shape before big edits
  Detect  → checklist for piling (hardcode, branch/loop sprawl, overlays)

Turn kinds decide what runs — not every user message pays the full cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TurnKind(str, Enum):
    """Coarse routing for coherence cost."""

    CHAT = "chat"  # Q&A / meta — no align
    TARGETED = "targeted"  # named file small edit — skip align
    STRUCTURAL = "structural"  # new capability / likely new files — align + contract
    LARGE = "large"  # refactor / greenfield-ish — align + contract + pile check


@dataclass(frozen=True)
class TurnPolicy:
    kind: TurnKind
    require_align: bool
    require_shape_contract: bool
    require_pile_check: bool

    @property
    def label(self) -> str:
        return self.kind.value


_FILE_HINT_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Za-z0-9_./\\-]+\.(?:html?|tsx?|jsx?|mjs|cjs|py|go|rs|java|kt|css|scss|vue|svelte|json|md))"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_META_CUES = (
    "哪些任务",
    "提了哪些",
    "说过什么",
    "总结一下",
    "回顾",
    "什么文件",
    "有哪些文件",
    "看看目录",
    "列出",
    "帮我看看当前",
    "what did i",
    "summarize",
    "list files",
    "what files",
)

_MUTATE_CUES = (
    "改",
    "修",
    "写",
    "加",
    "实现",
    "创建",
    "新建",
    "删除",
    "重构",
    "优化",
    "做",
    "生成",
    "edit",
    "fix",
    "add",
    "create",
    "implement",
    "build",
    "refactor",
    "update",
    "delete",
)

_STRUCTURAL_CUES = (
    "新建",
    "创建",
    "添加",
    "实现",
    "做一个",
    "写一个",
    "帮我做",
    "帮我写",
    "组件",
    "模块",
    "功能",
    "页面",
    "接口",
    "新文件",
    "create",
    "implement",
    "add a",
    "add an",
    "new file",
    "component",
    "module",
    "feature",
)

_LARGE_CUES = (
    "重构",
    "整站",
    "完整",
    "整套",
    "从零",
    "整个项目",
    "大改",
    "rewrite",
    "refactor",
    "from scratch",
    "entire project",
    "whole app",
)


def classify_turn(user_text: str) -> TurnKind:
    """Decide coherence cost for this user message."""
    t = (user_text or "").strip()
    if not t:
        return TurnKind.CHAT

    low = t.lower()
    has_mutate = any(c in t or c in low for c in _MUTATE_CUES)
    has_meta = any(c in t or c in low for c in _META_CUES)

    if has_meta and not has_mutate:
        return TurnKind.CHAT

    if any(c in t or c in low for c in _LARGE_CUES) or len(t) >= 120:
        if has_mutate or any(c in t or c in low for c in _STRUCTURAL_CUES):
            return TurnKind.LARGE
        if len(t) >= 160:
            return TurnKind.LARGE

    # Short instruction that names a concrete file → targeted edit
    file_hits = _FILE_HINT_RE.findall(t)
    if file_hits and len(t) <= 100 and has_mutate:
        if not any(c in t or c in low for c in ("新建", "创建", "create", "new file", "再写一个", "另外做")):
            return TurnKind.TARGETED

    if any(c in t or c in low for c in _STRUCTURAL_CUES):
        return TurnKind.STRUCTURAL

    if has_mutate and len(t) >= 40:
        return TurnKind.STRUCTURAL

    if not has_mutate:
        return TurnKind.CHAT

    return TurnKind.TARGETED if file_hits else TurnKind.STRUCTURAL


def policy_for_turn(user_text: str) -> TurnPolicy:
    kind = classify_turn(user_text)
    if kind == TurnKind.CHAT:
        return TurnPolicy(kind, False, False, False)
    if kind == TurnKind.TARGETED:
        return TurnPolicy(kind, False, False, False)
    if kind == TurnKind.STRUCTURAL:
        return TurnPolicy(kind, True, True, False)
    return TurnPolicy(kind, True, True, True)


def empty_shape_contract() -> dict[str, str]:
    return {
        "reuse": "",
        "create_only_if": "",
        "config_placement": "",
        "control_flow": "",
        "why_not_smaller": "",
        "verify_command": "",
    }


def normalize_shape_contract(raw: Any) -> dict[str, str]:
    """Accept dict or ignore junk; always return stable keys."""
    base = empty_shape_contract()
    if not isinstance(raw, dict):
        return base
    for key in base:
        val = raw.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            base[key] = text[:500]
    return base


def format_shape_contract_markdown(contract: dict[str, str]) -> str:
    c = normalize_shape_contract(contract)
    lines = [
        "### 形态合同（反堆砌）",
        f"- **复用**：{c['reuse'] or '（未填）'}",
        f"- **仅当无复用时新建**：{c['create_only_if'] or '（未填）'}",
        f"- **可变配置放哪**：{c['config_placement'] or '（未填）'}",
        f"- **控制流怎么收**：{c['control_flow'] or '（未填）'}",
        f"- **为何不能更小**：{c['why_not_smaller'] or '（未填）'}",
        f"- **验收命令**：{c['verify_command'] or '（未填）'}",
    ]
    return "\n".join(lines)


PILE_CHECKLIST = """## 检堆砌清单（有证据才算）
1. Overlay：是否平行新建了已有能力？（应扩展而非第二套）
2. Hardcode：会变的规则/文案/名单是否写死在逻辑里？（应进配置/数据）
3. Control-flow piling：是否过长同质 if/循环，而仓库已有表驱动/映射/复用函数？
4. Blast：共享模块改动是否可能分叉调用方？
若有问题：接到已有抽象上再结束；不要再堆一层。"""


def format_turn_policy_block(policy: TurnPolicy) -> str:
    """Pinned into system message for this user turn only."""
    lines = [
        "## Turn coherence policy (Anti-Piling)",
        f"kind: {policy.label}",
        f"require_align: {str(policy.require_align).lower()}",
        f"require_shape_contract: {str(policy.require_shape_contract).lower()}",
        f"require_pile_check: {str(policy.require_pile_check).lower()}",
    ]
    if policy.require_align:
        lines.append(
            "Before creating new code files or parallel modules: call codebase_find_similar "
            "and prefer extending matches."
        )
    else:
        lines.append("Align not required this turn (chat or targeted edit of named files).")

    if policy.require_shape_contract:
        lines.append(
            "Before large writes: keep a mental shape contract — reuse what, create only if, "
            "where config lives, how control flow stays small."
        )

    if policy.require_pile_check:
        lines.append("Before finishing: run through the pile checklist (overlay/hardcode/branches).")
        lines.append(PILE_CHECKLIST)

    lines.append(
        "Do not invent paths. Ground truth above is authoritative for this workspace."
    )
    return "\n".join(lines)


def merge_policy_into_system(system_content: str, policy_block: str) -> str:
    """Replace prior turn policy section; keep the rest of the system prompt."""
    marker = "## Turn coherence policy (Anti-Piling)"
    content = (system_content or "").rstrip()
    if marker in content:
        content = content.split(marker, 1)[0].rstrip()
    return (content + "\n\n" + policy_block).strip()


def shape_contract_from_plan(plan: dict[str, Any]) -> dict[str, str]:
    return normalize_shape_contract(plan.get("shape_contract"))


def inject_contract_into_goal(goal: str, contract: dict[str, str]) -> str:
    """Append contract to a plan-step goal so execution stays constrained."""
    c = normalize_shape_contract(contract)
    if not any(c.values()):
        return goal
    return (
        f"{goal.strip()}\n\n"
        f"{format_shape_contract_markdown(c)}\n"
        "Follow the shape contract; do not parallel-reimplement or hardcode variable rules."
    )
