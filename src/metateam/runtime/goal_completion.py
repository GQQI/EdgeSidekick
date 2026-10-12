"""Decide whether a tool-using turn has actually met its goal.

The agent loop used to treat every tool-less assistant message as the end of
the turn. Models often stop after an intermediate observation. This module
asks for a structured verdict over the active obligation and the tool
evidence, then tells the loop whether to keep working.

Verdicts are model judgments, not keyword lists over the user's task.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..core.guardrails import looks_failed
from ..core.textutil import safe_clip

# Safety cap for "still not done, but each nudge produced new tool work".
GOAL_CONTINUE_LIMIT = 6

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

state=done only when the tool evidence shows the user's requested outcome is already met. A successful intermediate tool is not enough. One successful edit does not finish a goal that also asks to create, open, or verify something else. A working version is enough for a single action. Do not require extra polish once that outcome is present and the latest check did not fail.

state=blocked when the remaining work depends on a choice, secret, or action only the user can provide, and the candidate asks for that.

state=continue when any requested action is still absent, the latest check failed, or the reply is only a status update, a plan, or a promise of later work. Repeating the same page, console, or file read is not missing work.

When unsure: inspection only, or a later requested action with no successful tool evidence, is continue. Done requires evidence for each requested action, not merely the first one.
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


def tool_result_count(messages: list[dict[str, Any]], start: int = 0) -> int:
    window = (messages or [])[max(0, start) :]
    return sum(1 for msg in window if msg.get("role") == "tool")


# Looking at the workspace or the page is not delivery. A later judge must
# not reopen a turn that already changed the requested outcome and stopped.
_INSPECT_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "search_text",
        "codebase_overview",
        "codebase_find_similar",
        "codebase_impact",
        "web_search",
        "web_fetch",
        "browser_observe",
        "browser_snapshot",
        "browser_console",
        "browser_diagnostics",
        "browser_screenshot",
        "browser_network",
        "browser_get_page_content",
        "browser_find_elements",
        "browser_tabs",
        "browser_extract_content",
        "browser_wait",
    }
)


def _tool_rows(messages: list[dict[str, Any]], start: int = 0) -> list[dict[str, Any]]:
    return [msg for msg in (messages or [])[max(0, start) :] if msg.get("role") == "tool"]


def _usable_tool_text(msg: dict[str, Any]) -> str:
    text = str(msg.get("content") or "").lstrip()
    if not text or text.startswith("[cleared tool result:"):
        return ""
    return text


def turn_delivery(messages: list[dict[str, Any]], start: int = 0) -> str:
    """How this turn stands once the model stops calling tools.

    delivered: a non-inspect tool succeeded and the latest call did not fail.
    failed: the latest tool call failed.
    inspect_only: the turn only read or observed.
    none: no usable tool result.
    """
    rows = _tool_rows(messages, start)
    if not rows:
        return "none"
    last = _usable_tool_text(rows[-1])
    if last and looks_failed(last):
        return "failed"
    for msg in rows:
        name = str(msg.get("name") or "")
        if name in _INSPECT_TOOLS:
            continue
        text = _usable_tool_text(msg)
        if text and not looks_failed(text):
            return "delivered"
    if any(_usable_tool_text(msg) for msg in rows):
        return "inspect_only"
    return "none"


def useful_tool_result_count(messages: list[dict[str, Any]], start: int = 0) -> int:
    """Tool results that changed the task, not failed or discarded retries.

    A blocked repeat, an ERROR string, or a cleared stub is not new work.
    Counting those as progress kept the loop editing and re-checking forever.
    """
    count = 0
    for msg in (messages or [])[max(0, start) :]:
        if msg.get("role") != "tool":
            continue
        text = str(msg.get("content") or "").lstrip()
        if not text or text.startswith("[cleared tool result:"):
            continue
        if looks_failed(text):
            continue
        count += 1
    return count


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


_ACTION_VERBS = ("删除", "写入", "创建", "打开", "验证", "修改", "修复", "运行", "测试", "审查", "优化")


def owner_goal_text(goal: str) -> str:
    """Drop a clause the user explicitly gave to other members."""
    parts = re.split(r"然后(?:其他|别的|其余)", goal or "", maxsplit=1)
    return parts[0] if parts else ""


def requested_actions_missing(goal: str, evidence: str, candidate: str) -> bool:
    """True when a later requested action has no trace in the evidence or reply.

    A goal joined by 然后 is not finished just because the first action succeeded.
    """
    owned = owner_goal_text(goal)
    parts = [part.strip() for part in re.split(r"然后|接着|之后", owned) if len(part.strip()) >= 2]
    if len(parts) < 2:
        return False
    blob = f"{evidence or ''}\n{candidate or ''}"
    for part in parts:
        wanted = [verb for verb in _ACTION_VERBS if verb in part]
        if wanted and not any(verb in blob for verb in wanted):
            return True
    return False


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
    new_tools: bool,
    nudges: int,
    limit: int = GOAL_CONTINUE_LIMIT,
) -> bool:
    """Whether a tool-less reply should be rejected so the turn keeps working.

    ``done`` means the produced result matches the user's request, and the
    turn stops. A successful tool call is not that verdict. ``blocked`` stops.
    An unreadable verdict allows one continuation. ``continue`` keeps going
    while new tool work appears, and a reply that adds no tools gets one more
    nudge, then stops.
    """
    if state in {"done", "blocked"}:
        return False
    if nudges >= limit:
        return False
    if state is None:
        return nudges == 0
    if nudges >= 2 and not new_tools:
        return False
    return True
