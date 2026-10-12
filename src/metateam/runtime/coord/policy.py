"""Policy helpers: build packets, enrich delegate context, shape child results."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .schemas import (
    AskAct,
    AskPacket,
    ChildResult,
    ReplyContract,
    VisibilitySpec,
    act_from_mapping,
    packet_from_mapping,
    reply_from_mapping,
    visibility_from_mapping,
)
from .visibility import (
    merge_visibility,
    render_user_question,
    render_visible_context,
    template_for,
)


def build_packet(
    *,
    relation: str,
    what: str = "",
    how: str = "",
    who: str = "",
    when: str = "",
    why: str = "",
    stop: str = "",
    goal_slice: str = "",
    need: str = "",
    evidence: str = "",
    constraints: str = "",
    prior_answers: str = "",
    sibling_summary: str = "",
    target: str = "",
    visibility: Optional[dict[str, Any]] = None,
    reply_contract: Optional[dict[str, Any]] = None,
    share: Optional[list[str]] = None,
    redact: Optional[list[str]] = None,
) -> AskPacket:
    act = AskAct(
        what=what or _default_what(relation),
        how=how or _default_how(relation),
        who=who or _default_who(relation),
        when=when or "now",
        why=why,
        stop=stop,
    )
    vis_over: VisibilitySpec | None = None
    if visibility or share or redact:
        base = visibility_from_mapping(visibility)
        if share:
            base.share = list(share)
        if redact:
            base.redact = list(redact)
        vis_over = base
    vis = merge_visibility(relation, vis_over)
    rc = reply_from_mapping(reply_contract) if reply_contract else _default_reply(relation)
    return AskPacket(
        act=act,
        goal_slice=goal_slice,
        need=need,
        evidence=evidence,
        constraints=constraints,
        prior_answers=prior_answers,
        sibling_summary=sibling_summary,
        visibility=vis,
        reply_contract=rc,
        target=target,
    ).normalized()


def _default_what(relation: str) -> str:
    return {
        "parent_to_child": "execute",
        "peer_consult": "fact",
        "dialogue_turn": "review",
        "ask_user": "decision",
        "child_to_parent": "clarify",
    }.get(relation, "clarify")


def _default_how(relation: str) -> str:
    return {
        "parent_to_child": "consult",
        "peer_consult": "consult",
        "dialogue_turn": "debate",
        "ask_user": "options",
        "child_to_parent": "structured",
    }.get(relation, "options")


def _default_who(relation: str) -> str:
    return {
        "parent_to_child": "child",
        "peer_consult": "peer",
        "dialogue_turn": "peer",
        "ask_user": "user",
        "child_to_parent": "user",
    }.get(relation, "user")


def _default_reply(relation: str) -> ReplyContract:
    if relation == "ask_user":
        return ReplyContract(format="options", slots=["answer"])
    if relation == "dialogue_turn":
        return ReplyContract(
            format="prose",
            slots=["stance", "arguments", "open_questions"],
        )
    return ReplyContract(
        format="json_slots",
        slots=["findings", "recommendation", "open_questions"],
    )


def enrich_child_context(
    *,
    goal: str,
    context: str = "",
    role: str = "leaf",
    what: str = "",
    how: str = "",
    why: str = "",
    stop: str = "",
    need: str = "",
    evidence: str = "",
    constraints: str = "",
    prior_answers: str = "",
    sibling_summary: str = "",
    visibility: Optional[dict[str, Any]] = None,
    reply_contract: Optional[dict[str, Any]] = None,
    share: Optional[list[str]] = None,
    redact: Optional[list[str]] = None,
) -> tuple[str, AskPacket]:
    """Return (context_for_child, packet)."""
    packet = build_packet(
        relation="parent_to_child",
        what=what or "execute",
        how=how or "consult",
        who="child",
        when="now",
        why=why or f"delegate role={role or 'leaf'}",
        stop=stop or "goal satisfied or blocked with open_questions",
        goal_slice=goal,
        need=need or goal,
        evidence=evidence,
        constraints=constraints,
        prior_answers=prior_answers,
        sibling_summary=sibling_summary,
        visibility=visibility,
        reply_contract=reply_contract,
        share=share,
        redact=redact,
    )
    visible = render_visible_context(packet)
    # Keep any extra parent context only if allowed (treated as evidence/constraints blend)
    extra = (context or "").strip()
    if extra and "full_history" not in packet.visibility.redact:
        # still avoid dumping huge extras: cap
        if len(extra) > 2000:
            extra = extra[:1980].rstrip() + "\n…(truncated)"
        if "evidence" in packet.visibility.share or not packet.evidence:
            visible = visible + "\nparent_context:\n" + extra + "\n"
    instruction = (
        "\n[Reply contract]\n"
        "When finished, structure your final answer with sections:\n"
        f"- findings\n- recommendation\n- open_questions (list what you still need; "
        f"do not silently ask the user unless you are a full party agent)\n"
        f"stop_when: {packet.act.stop or 'task done or blocked'}\n"
    )
    return (visible + instruction).strip(), packet


def enrich_consult_context(
    *,
    question: str,
    goal_slice: str = "",
    evidence: str = "",
    constraints: str = "",
    prior_answers: str = "",
    sibling_summary: str = "",
    target: str = "",
    why: str = "",
    stop: str = "",
    visibility: Optional[dict[str, Any]] = None,
    share: Optional[list[str]] = None,
    redact: Optional[list[str]] = None,
) -> tuple[str, AskPacket]:
    packet = build_packet(
        relation="peer_consult",
        what="fact",
        how="consult",
        who="peer",
        when="now",
        why=why or "peer consult",
        stop=stop or "answer the need; list unknowns",
        goal_slice=goal_slice,
        need=question,
        evidence=evidence,
        constraints=constraints,
        prior_answers=prior_answers,
        sibling_summary=sibling_summary,
        target=target,
        visibility=visibility,
        share=share,
        redact=redact,
    )
    return render_visible_context(packet).strip(), packet


def enrich_dialogue_extra(
    *,
    topic: str,
    extra: str = "",
    mode: str = "",
    why: str = "",
    share: Optional[list[str]] = None,
    redact: Optional[list[str]] = None,
) -> tuple[str, AskPacket]:
    packet = build_packet(
        relation="dialogue_turn",
        what="review",
        how="debate" if "debate" in (mode or "").lower() else "critique",
        who="peer",
        when="now",
        why=why or f"dialogue mode={mode or 'session'}",
        stop="complete assigned rounds or reach stop condition",
        goal_slice=topic,
        need=topic,
        constraints=extra,
        share=share,
        redact=redact,
    )
    header = render_visible_context(packet)
    merged = header
    if (extra or "").strip():
        merged = header + "\nextra_rules:\n" + extra.strip() + "\n"
    return merged.strip(), packet


def prepare_user_ask(
    *,
    question: str,
    what: str = "",
    how: str = "",
    why: str = "",
    stop: str = "",
    goal_slice: str = "",
    constraints: str = "",
    evidence: str = "",
    prior_answers: str = "",
    visibility: Optional[dict[str, Any]] = None,
    share: Optional[list[str]] = None,
    redact: Optional[list[str]] = None,
) -> tuple[str, AskPacket]:
    packet = build_packet(
        relation="ask_user",
        what=what or "decision",
        how=how or "options",
        who="user",
        when="now",
        why=why,
        stop=stop or "user selects an option or custom answer",
        goal_slice=goal_slice,
        need=question,
        evidence=evidence,
        constraints=constraints,
        prior_answers=prior_answers,
        visibility=visibility,
        share=share,
        redact=redact,
    )
    return render_user_question(packet, question), packet


_SECTION_RE = re.compile(
    r"(?is)\b(findings|recommendation|open_questions|stance|arguments)\b\s*[:：]\s*"
)


def parse_child_summary(summary: str, *, goal: str = "") -> ChildResult:
    text = (summary or "").strip()
    result = ChildResult(raw_summary=text)
    if not text:
        return result

    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result.findings = str(data.get("findings") or data.get("summary") or "")
            result.recommendation = str(data.get("recommendation") or "")
            oq = data.get("open_questions") or data.get("open_questions") or []
            if isinstance(oq, str):
                result.open_questions = [oq] if oq.strip() else []
            elif isinstance(oq, list):
                result.open_questions = [str(x).strip() for x in oq if str(x).strip()]
            ar = data.get("ask_requests") or []
            if isinstance(ar, list):
                result.ask_requests = [x for x in ar if isinstance(x, dict)]
            if not result.findings:
                result.findings = text
            return result
    except Exception:
        pass

    # Section heuristics
    lower = text
    def _after(label: str) -> str:
        m = re.search(
            rf"(?is)\b{label}\b\s*[:：]\s*(.*?)(?=\n\s*\b(?:findings|recommendation|open_questions|stance|arguments)\b\s*[:：]|\Z)",
            text,
        )
        return (m.group(1).strip() if m else "")

    findings = _after("findings")
    recommendation = _after("recommendation")
    oq_block = _after("open_questions")
    if findings or recommendation or oq_block:
        result.findings = findings or text
        result.recommendation = recommendation
        if oq_block:
            lines = []
            for line in oq_block.splitlines():
                s = re.sub(r"^\s*[-*\d\.\)\、]+\s*", "", line).strip()
                if s:
                    lines.append(s)
            result.open_questions = lines
    else:
        result.findings = text
        # Detect question marks as soft open questions
        soft = [ln.strip() for ln in text.splitlines() if "?" in ln or "？" in ln]
        result.open_questions = soft[:5]
    if goal and not result.recommendation:
        result.recommendation = ""
    return result


def format_delegate_payload(
    rows: list[dict[str, Any]],
) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2)
