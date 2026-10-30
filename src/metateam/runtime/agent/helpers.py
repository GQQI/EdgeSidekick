"""Module-level helpers and imports shared by Agent mixins."""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .types import (
    SUBAGENT_ITERATION_BUDGET,
    AgentResult,
    _BROWSER_NAV_RECOVER_NUDGE,
    _EMPTY_ASSISTANT_RECOVER_NUDGE,
    _FILE_RETRY_NUDGE,
    _POST_COMPRESS_CONTINUE_NUDGE,
    _STALL_NUDGE,
)

if TYPE_CHECKING:
    from .core import Agent
from ..approval import ApprovalGate
from ..ask import (
    AskGate,
    MIN_ASK_OPTIONS,
    build_ask_options,
    normalize_option_labels,
    should_keep_as_markdown,
    try_parse_inline_ask,
)
from ...core.config import Settings, get_settings
from ...core.textutil import split_dangling_colon, strip_dangling_colon
from ..context import (
    COMPACTION_MARK,
    cheap_compact,
    context_budget_tokens,
    current_turn_start,
    debug_dump_budget,
    dedupe_repeated_context,
    ensure_fit,
    messages_tokens,
    park_overflow,
    repair_incomplete_tool_chains,
    schemas_tokens,
    squeeze_fresh_tool_results,
)
from ...core.events import EventBus, emit, new_id
from ...core.guardrails import Guardrails, repeating_tool_sequence, tool_call_signature
from ..llm import (
    LLM,
    fold_reasoning_text,
    parse_tool_args,
    streaming_text_looped,
)
from ..evidence import EvidenceLedger
from ..reasoning_gate import ReasoningRoundGate, dedupe_reasoning_round
from ..tool_repeats import (
    STOP as TOOL_REPEAT_STOP,
    ToolRepeatTracker,
    call_signature_set,
    result_history,
    stalled_tool_cycle,
)
from ..plan import (
    PLAN_PREP_HINT,
    PLAN_PREP_MAX_ROUNDS,
    PLAN_PREP_RETRY_HINT,
    PlanGate,
    apply_confirmed_plan,
    digest_plan_prep,
    extract_plan_goal,
    format_plan_markdown,
    generate_plan,
    goal_ready_to_plan,
    needs_plan,
    snapshot_plan_tasks,
)
from ..prompts import (
    apply_extensions_block,
    apply_response_language,
    build_system_prompt,
    language_pin_messages,
)
from ..coherence import inject_contract_into_goal, shape_contract_from_plan
from ..goal_completion import (
    active_obligation,
    assess_goal_state,
    assess_goal_verdict,
    coalesce_internal_nudge,
    extract_named_tool_ids,
    goal_continue_nudge,
    intent_probe_text,
    looks_like_tool_intent_only,
    prior_assistant_substance,
    resolve_mentioned_tools,
    should_continue_for_goal,
    should_continue_for_tool_intent,
    should_run_next_plan_step,
    status_for_goal_verdict,
    tool_evidence,
    tool_intent_unavailable_message,
)
from ..review import run_review
from ...services.skills import Skill, invalidate_skills_cache, load_skills
from ..tool_registry import drop_delegate_schemas, filter_readonly_schemas, skill_tool_name
from ..tools import ToolRegistry, build_registry
from ..tools.build import register_mcp_safely, register_plugins_safely
from ..tools.support import _skill_as_tool

PrintFn = Callable[[str], None]


def _subagent_snapshot_item(
    child: Agent,
    party: str = "",
    *,
    turn: int = 0,
) -> dict[str, Any]:
    kind = "party" if child.full_agent else ("talk" if child.talk_only else "task")
    label = party or (child.goal or "").strip().split("\n")[0][:80]
    transcript: list[dict[str, Any]] = []
    for m in child.messages:
        role = m.get("role")
        if role == "assistant":
            text = str(m.get("content") or "").strip()
            reasoning = str(m.get("reasoning") or "").strip()
            try:
                from ..text_tool_calls import extract_text_tool_calls

                text, xml_calls = extract_text_tool_calls(text)
            except Exception:
                xml_calls = []
            if text or reasoning:
                item: dict[str, Any] = {"kind": "assistant", "text": text}
                if reasoning:
                    item["reasoning"] = reasoning
                transcript.append(item)
            if not m.get("tool_calls"):
                for tc in xml_calls:
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or "").strip()
                    if name:
                        transcript.append({"kind": "tool", "name": name, "result": ""})
        elif role == "tool":
            transcript.append(
                {
                    "kind": "tool",
                    "name": str(m.get("name") or "tool"),
                    "result": str(m.get("content") or "")[:2000],
                }
            )
    return {
        "child_id": child.agent_id,
        "goal": child.goal or label,
        "role": child.role,
        "kind": kind,
        "party": party,
        "label": label,
        "parent_id": child.parent_id,
        "turn": turn,
        "replay": True,
        "transcript": transcript[-30:],
        "status": "running",
        "activity": "运行中…",
        "message": f"resume {child.role}: {label}",
    }


def _evidence_ledger(agent: Any) -> EvidenceLedger:
    """The turn's evidence ledger, created on first use.

    Also covers test doubles and agents restored from a pickle that predates
    the attribute, so every entry point can call it unconditionally.
    """
    ledger = getattr(agent, "_evidence", None)
    if ledger is None:
        ledger = EvidenceLedger()
        agent._evidence = ledger
    return ledger


# Star-import must include leading-underscore helpers used by mixins.
__all__ = [n for n in globals() if not n.startswith("__")]

