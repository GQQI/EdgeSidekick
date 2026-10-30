"""Goal-state / verdict assessment over tool evidence."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ....core.textutil import safe_clip
from ...context import COMPACTION_MARK, obligation_from_compaction

_GOAL_STATE_SYSTEM = """You judge whether a ReAct turn has finished the active goal.

The agent loop is Think → (optional) Act/tools → Observe → Think again.
A tool-less candidate is a proposed stop. You decide if that stop is valid.

Reply with ONLY valid JSON (no markdown fences):
{"state": "done"|"continue"|"blocked", "reason": "one short sentence"}

# Hard rule — narrated intent is never completion
If Evidence present is no, and the Active goal asked for an action, lookup,
change, generation, or verification, and the Candidate only plans, promises,
or narrates calling a tool/plugin/MCP (e.g. "让我调用…工具", "I will use…",
"接下来调用…") without already delivering the outcome — state MUST be
continue. Describing a future call does not satisfy the goal.

# Scope
Judge only what the user asked for. Do not invent follow-up work, refactoring,
tests, documentation, or polish. Do not continue merely because tools exist
in the schema.

Earlier assistant replies this turn count as part of the answer: if a prior
reply already fully met the goal and evidence supports it, state=done even
when the latest candidate only says "done" / "no further action".

state=done when ANY of these hold:
- Pure conversation (greeting, thanks, acknowledgement) already answered, or
  a question already fully answered in the candidate / earlier replies.
- Tool evidence shows every requested outcome is already met.
- An earlier reply this turn already delivered the requested answer and
  evidence shows the supporting tool work happened.

state=blocked when remaining work needs a choice, secret, or action only the
user can provide, and the candidate asks for that.

state=continue when ANY of these hold:
- Evidence present is no for an action/lookup/change/verify request
  (including narrated tool intent with no results).
- The latest check failed, or the reply is only a status/partial plan with no
  earlier complete answer this turn.
- Multiple requested actions exist and only some have successful evidence.

When unsure: unanswered action + no successful tool evidence → continue.
Completed exchange with no pending action → done. Once the requested outcome
is present, do not keep polishing or re-checking.
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


def parse_goal_verdict(raw: str) -> dict[str, Any]:
    """Parse ``{state, reason}`` from a judge reply.

    ``state`` is done|continue|blocked, or None when unusable. ``reason`` is
    the model's short explanation (may be empty).
    """
    data = _parse_json_object(raw)
    reason = str(data.get("reason") or data.get("why") or "").strip()
    state = str(data.get("state") or "").strip().lower()
    if state not in _STATES:
        if "satisfied" in data:
            state = "done" if bool(data.get("satisfied")) else "continue"
        else:
            low = (raw or "").strip().lower()
            state = ""
            if low:
                matched = re.search(r'"state"\s*:\s*"(done|continue|blocked)"', low)
                if matched:
                    state = matched.group(1)
                elif re.search(r'"satisfied"\s*:\s*true', low):
                    state = "done"
                elif re.search(r'"satisfied"\s*:\s*false', low):
                    state = "continue"
            if not reason:
                rm = re.search(r'"reason"\s*:\s*"([^"]{1,200})"', raw or "", re.I)
                if rm:
                    reason = rm.group(1).strip()
    if state not in _STATES:
        return {"state": None, "reason": reason}
    return {"state": state, "reason": reason}


def parse_goal_state(raw: str) -> Optional[str]:
    """Return done|continue|blocked, or None when the reply is unusable."""
    return parse_goal_verdict(raw).get("state")  # type: ignore[return-value]


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
    """True for runtime nudges. Plan-step prompts stay visible as the obligation.

    Goal-continue / tool-intent prompts are injected as internal system (or
    developer) instructions — never as fake user requests. Older builds used
    role=user; both shapes are treated as control so they never become the
    active obligation.
    """
    role = str(msg.get("role") or "")
    kind = _message_kind(msg)
    meta = msg.get("sidekick")
    internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
    if isinstance(meta, dict) and meta.get("internal"):
        internal = True
    if kind in {"goal_continue", "tool_intent"} and role in {"user", "system", "developer"}:
        return True
    if role != "user":
        return False
    if kind == "goal_continue":
        return True
    if kind == "compaction":
        return True
    content = str(msg.get("content") or "")
    if content.startswith(COMPACTION_MARK):
        return True
    if internal and kind not in _OBLIGATION_KINDS:
        return True
    return False


def active_obligation(
    messages: list[dict[str, Any]],
    *,
    assigned_goal: str = "",
    assigned_context: str = "",
) -> str:
    """Latest user obligation, including the current plan step.

    Compaction stubs are internal working memory (often the only place the
    original ask survives after a compress). Never treat the raw
    ``[CONTEXT COMPACTION]`` blob as the goal — that made the model think the
    user reported a truncated reply and stop. Prefer pinned user requests.

    ``assigned_goal`` is for delegated workers. A leaf agent's user turn is a
    bootstrap line ("Begin now. Use tools, then summarize."), not the task; the
    task lives in the agent's assigned goal + context. Without this the goal
    judge never sees what the worker was asked to deliver, so a finished worker
    keeps getting "continue". The context (scope boundary, expected output) is
    the same brief the worker saw, so the judge evaluates the same contract.
    """
    goal = (assigned_goal or "").strip()
    if goal:
        scope = (assigned_context or "").strip()
        if scope:
            return _clip_goal(f"{goal}\n\nScope / context:\n{scope}")
        return _clip_goal(goal)
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "").strip()
        if text.startswith(COMPACTION_MARK):
            extracted = obligation_from_compaction(text)
            if extracted:
                return _clip_goal(extracted)
            continue
        if is_control_user_message(msg):
            continue
        if text:
            return _clip_goal(text)
    return ""


_EVIDENCE_MUTATION_TOOLS = frozenset({"write_file", "str_replace", "delete_file"})


def tool_evidence(
    messages: list[dict[str, Any]],
    *,
    start: int = 0,
    rows: list[tuple[str, str]] | None = None,
    limit_items: int = 8,
    item_chars: int = 500,
    digest_chars: int = 140,
    digest_items: int = 12,
) -> str:
    """Everything this turn did, at a bounded size.

    The judge used to see only the last ``limit_items`` tool rows. A long turn
    that mutated a file early and then kept browsing for verification looked as
    if it had produced no result, so the judge answered "continue" after the job
    was already done. A per-tool digest keeps every action visible; recent rows
    stay in full, and successful filesystem mutations are still force-included.

    ``rows`` lets the caller pass an execution-time ledger. Context management
    replaces large payloads with ``[cleared tool result: …]`` stubs, so reading
    the transcript alone can make completed verification look like no evidence
    at all. The ledger keeps the substance the judge needs.
    """
    if rows is None:
        rows = []
        for msg in (messages or [])[max(0, start) :]:
            if msg.get("role") != "tool":
                continue
            name = str(msg.get("name") or "tool")
            content = str(msg.get("content") or "").strip()
            rows.append((name, content))
    else:
        rows = [
            (str(name or "tool"), str(content or "").strip())
            for name, content in rows
        ]
    if not rows:
        return ""

    recent = rows[-limit_items:] if len(rows) > limit_items else list(rows)
    mutations = [
        (name, content)
        for name, content in rows
        if name in _EVIDENCE_MUTATION_TOOLS
        and content
        and not content.upper().startswith("ERROR")
    ]

    by_name: dict[str, list[str]] = {}
    last_seen: dict[str, int] = {}
    for i, (name, content) in enumerate(rows):
        by_name.setdefault(name, []).append(content)
        last_seen[name] = i
    # Bound the digest by the most recently used tools; an unconfigured plugin
    # registry can expose dozens of names in one turn.
    digest_names = sorted(by_name, key=lambda n: last_seen[n])[-max(1, digest_items) :]
    digest: list[str] = []
    for name in digest_names:
        results = by_name[name]
        last = results[-1]
        state = "error" if last.upper().startswith("ERROR") else "ok"
        digest.append(
            f"{name} x{len(results)} (last {state}): "
            f"{safe_clip(last, digest_chars)}"
        )

    lines = ["Actions this turn:", *digest, "Recent results:"]
    seen: set[tuple[str, str]] = set()
    for name, content in mutations + recent:
        key = (name, content)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{name}: {safe_clip(content, item_chars)}")
    return "\n".join(lines)


def coalesce_internal_nudge(
    messages: list[dict[str, Any]],
    *,
    kind: str,
    content: str,
    role: str = "system",
) -> None:
    """Append an internal nudge, or replace prior same-kind ones.

    Prevents stacking duplicate goal_continue / stall essays each loop — a
    major amplifier of LLM tape-recorder repetition.
    """
    kind_s = str(kind or "").strip()
    body = str(content or "").strip()
    if not kind_s or not body:
        return
    kept: list[dict[str, Any]] = []
    for msg in messages:
        meta = msg.get("sidekick") if isinstance(msg.get("sidekick"), dict) else {}
        mk = str((meta or {}).get("kind") or "")
        internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
        if isinstance(meta, dict) and meta.get("internal"):
            internal = True
        if internal and mk == kind_s:
            continue
        kept.append(msg)
    messages[:] = kept
    messages.append(
        {
            "role": role,
            "content": body,
            "sidekick_internal": True,
            "sidekick": {"internal": True, "kind": kind_s},
        }
    )


def prior_assistant_substance(
    messages: list[dict[str, Any]],
    *,
    start: int = 0,
    limit_chars: int = 3500,
    max_replies: int = 3,
) -> str:
    """Earlier tool-less assistant replies this turn (for the goal judge).

    When the model already delivered a full answer and a later round only
    says "done", the judge must still see the substance — otherwise it keeps
    answering continue and the loop never settles.
    """
    parts: list[str] = []
    for msg in (messages or [])[max(0, start) :]:
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        parts.append(text)
    if len(parts) <= 1:
        # Only the current candidate exists; caller already passes it separately.
        return ""
    # Drop the latest message (the candidate under review); keep prior substance.
    chosen = parts[:-1][-max(1, max_replies) :]
    joined = "\n---\n".join(chosen)
    return safe_clip(joined, limit_chars)


def _worker_contract_block(contract: dict[str, Any] | None) -> str:
    """Context for judging a delegated worker's stop.

    A worker's assigned task and its reporting contract are not in the chat
    history: the lead's request is the system prompt, and the user turn is a
    bootstrap line. The judge needs the contract, or a finished worker looks
    unfinished whenever its report hands findings/open items back to the lead.
    """
    if not isinstance(contract, dict) or not contract:
        return ""
    stop_when = str(contract.get("stop_when") or "").strip()
    raw_slots = contract.get("deliverable") or []
    if isinstance(raw_slots, str):
        raw_slots = [raw_slots]
    slots = [str(x).strip() for x in raw_slots if str(x).strip()]
    lines = ["# Delegated worker contract"]
    lines.append(
        "This turn is a scoped subtask delegated by a lead agent, not a user "
        "conversation. The lead owns follow-up work."
    )
    if slots:
        lines.append("Report sections: " + ", ".join(slots))
    if stop_when:
        lines.append("Stop when: " + stop_when)
    lines.append(
        "The report is the deliverable. Open questions, blockers, and remaining "
        "issues written in the report are outputs handed to the lead — they are "
        "not unfinished actions for this worker. state=done once the report "
        "covers the assigned goal, even when it names such items."
    )
    return "\n".join(lines) + "\n\n"


def assess_goal_verdict(
    llm: Any,
    *,
    goal: str,
    evidence: str,
    candidate: str,
    prior_replies: str = "",
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the model for ``{state, reason, evidence_present}``.

    Callers should pass the same LLM used for ReAct Think. ``continue`` sends
    the loop back into Think; ``done``/``blocked`` settle the turn. The reason
    is surfaced to the UI/logs so stop/continue is auditable — not a silent
    heuristic.
    """
    obligation = (goal or "").strip()
    if not obligation:
        return {
            "state": None,
            "reason": "",
            "evidence_present": False,
        }
    evidence_text = (evidence or "").strip()
    has_evidence = bool(evidence_text)
    prior = (prior_replies or "").strip()
    prior_block = (
        f"Earlier assistant replies this turn:\n{prior}\n\n" if prior else ""
    )
    evidence_block = (
        evidence_text
        if has_evidence
        else "(none — zero tool results this turn)"
    )
    payload = (
        f"Active goal:\n{obligation}\n\n"
        f"{_worker_contract_block(contract)}"
        f"Tool evidence this turn:\n{evidence_block}\n\n"
        f"Evidence present: {'yes' if has_evidence else 'no'}\n\n"
        f"{prior_block}"
        f"Candidate reply (this message has no native tool_calls):\n"
        f"{safe_clip((candidate or '').strip(), 2500) or '(empty)'}\n\n"
        "JSON:"
    )
    try:
        raw = llm.complete_text(_GOAL_STATE_SYSTEM, payload, temperature=0.0)
    except Exception:
        return {
            "state": None,
            "reason": "judge unavailable",
            "evidence_present": has_evidence,
        }
    verdict = parse_goal_verdict(str(raw or ""))
    return {
        "state": verdict.get("state"),
        "reason": str(verdict.get("reason") or "").strip(),
        "evidence_present": has_evidence,
    }


def assess_goal_state(
    llm: Any,
    *,
    goal: str,
    evidence: str,
    candidate: str,
    prior_replies: str = "",
    contract: dict[str, Any] | None = None,
) -> Optional[str]:
    """Compatibility wrapper: return only the state from ``assess_goal_verdict``."""
    return assess_goal_verdict(
        llm,
        goal=goal,
        evidence=evidence,
        candidate=candidate,
        prior_replies=prior_replies,
        contract=contract,
    ).get("state")  # type: ignore[return-value]

