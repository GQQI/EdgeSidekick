"""Decide whether a tool-using turn has actually met its goal.

A tool-less assistant reply is the model's own stopping decision, but it can
also be an early stop after an intermediate observation. This module asks the
cheap model for one structured verdict over the active obligation and the tool
evidence, and the loop follows that verdict.

Verdicts are model judgments, not keyword lists over the user's task. A
capped number of continuations is allowed when the verdict says a requested
action is still missing; after that the turn ends instead of polishing forever.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..core.textutil import safe_clip

# Hard safety cap: each continuation costs a full model round. The judge is
# allowed to ask twice, never to keep the turn alive indefinitely.
GOAL_CONTINUE_LIMIT = 2

GOAL_CONTINUE_NUDGE = (
    "The active goal is not finished. Continue from the tool results already "
    "in this conversation and complete the remaining outcome. "
    "Do not repeat a step that already succeeded, and do not stop with a "
    "status update, a plan, or a partial result. "
    "If the requested outcome is already present, stop. Do not keep polishing "
    "or re-checking the same files or page. "
    "If you are blocked on a decision only the user can make, ask them and stop."
)

_GOAL_STATE_SYSTEM = """You judge whether an agent has finished the active goal.

Reply with ONLY valid JSON (no markdown fences):
{"state": "done"|"continue"|"blocked", "reason": "one short sentence"}

Judge only what the user asked for. Do not invent follow-up work, refactoring,
tests, documentation, or polish that the request did not include.

state=done when the tool evidence shows the requested outcome is already met.
A working version is enough for a single action; extra checks or a prettier
result are not required. One successful edit is not enough when the request
also asks to create, open, run, or verify something else.

state=blocked when the remaining work depends on a choice, secret, or action
only the user can provide, and the candidate asks for that.

state=continue only when a requested action is still absent, the latest check
failed, or the reply is only a status update, a plan, or a promise of later
work. Repeating the same page, console, or file read is not missing work.

When unsure: inspection only, or a later requested action with no successful
tool evidence, is continue. Done requires evidence for each requested action,
not merely the first one.
"""

_STATES = frozenset({"done", "continue", "blocked"})
# Internal user turns that are not the work obligation.
_OBLIGATION_KINDS = frozenset({"plan_step"})


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def parse_goal_state(raw: str) -> Optional[str]:
    """Return done|continue|blocked, or None when the reply is unusable."""
    data = _parse_json_object(raw)
    state = str(data.get("state") or "").strip().lower()
    if state in _STATES:
        return state
    if "satisfied" in data:
        return "done" if bool(data.get("satisfied")) else "continue"
    low = (raw or "").strip().lower()
    if not low:
        return None
    matched = re.search(r'"state"\s*:\s*"(done|continue|blocked)"', low)
    if matched:
        return matched.group(1)
    if re.search(r'"satisfied"\s*:\s*true', low):
        return "done"
    if re.search(r'"satisfied"\s*:\s*false', low):
        return "continue"
    return None


def _clip_goal(text: str, limit: int = 3500) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    head = limit // 2
    tail = max(0, limit - head - 1)
    return raw[:head].rstrip() + "\n…\n" + raw[-tail:].lstrip()


def _message_kind(msg: dict[str, Any]) -> str:
    meta = msg.get("sidekick")
    if isinstance(meta, dict):
        return str(meta.get("kind") or "")
    return ""


def is_control_user_message(msg: dict[str, Any]) -> bool:
    """True for runtime nudges. Plan-step prompts stay visible as the obligation."""
    if msg.get("role") != "user":
        return False
    kind = _message_kind(msg)
    if kind == "goal_continue":
        return True
    meta = msg.get("sidekick")
    internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
    if isinstance(meta, dict) and meta.get("internal"):
        internal = True
    if internal and kind not in _OBLIGATION_KINDS:
        return True
    return False


def active_obligation(messages: list[dict[str, Any]]) -> str:
    """Latest user obligation, including the current plan step."""
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        if is_control_user_message(msg):
            continue
        text = str(msg.get("content") or "").strip()
        if text:
            return _clip_goal(text)
    return ""


def tool_evidence(
    messages: list[dict[str, Any]],
    *,
    start: int = 0,
    limit_items: int = 8,
    item_chars: int = 500,
) -> str:
    rows: list[str] = []
    for msg in (messages or [])[max(0, start) :]:
        if msg.get("role") != "tool":
            continue
        name = str(msg.get("name") or "tool")
        content = str(msg.get("content") or "").strip()
        rows.append(f"{name}: {safe_clip(content, item_chars)}")
    if len(rows) > limit_items:
        rows = rows[-limit_items:]
    return "\n".join(rows)


def assess_goal_state(
    llm: Any,
    *,
    goal: str,
    evidence: str,
    candidate: str,
) -> Optional[str]:
    """Ask the compress model. None means the verdict could not be read."""
    obligation = (goal or "").strip()
    if not obligation:
        return None
    payload = (
        f"Active goal:\n{obligation}\n\n"
        f"Tool evidence:\n{(evidence or '').strip() or '(none)'}\n\n"
        f"Candidate final reply:\n{safe_clip((candidate or '').strip(), 2500) or '(empty)'}\n\n"
        "JSON:"
    )
    try:
        raw = llm.complete_text(_GOAL_STATE_SYSTEM, payload, temperature=0.0)
    except Exception:
        return None
    return parse_goal_state(str(raw or ""))


def should_continue_for_goal(
    state: Optional[str],
    *,
    nudges: int,
    limit: int = GOAL_CONTINUE_LIMIT,
) -> bool:
    """Whether a tool-less reply should be rejected so the turn keeps working.

    Only an explicit ``continue`` verdict reopens the turn. ``done`` and
    ``blocked`` stop. An unreadable verdict also stops: spending another
    model round on a guess is what made finished turns keep "improving".
    """
    return state == "continue" and nudges < limit
