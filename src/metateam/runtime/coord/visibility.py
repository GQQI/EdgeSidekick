"""Visibility templates: what A may show B when asking."""

from __future__ import annotations

from typing import Iterable

from .schemas import AskPacket, VisibilitySpec

# Default share sets by relation.
# Siblings may see each other's summaries by default; full sibling transcripts stay redacted.
TEMPLATES: dict[str, VisibilitySpec] = {
    "parent_to_child": VisibilitySpec(
        share=["goal_slice", "need", "constraints", "evidence", "sibling_summary"],
        redact=["secrets", "raw_chain_of_thought", "full_history", "sibling_full"],
        quote_policy="citations_only",
        sticky=True,
        relation="parent_to_child",
    ),
    "child_to_parent": VisibilitySpec(
        share=["goal_slice", "need", "findings", "open_questions", "evidence"],
        redact=["secrets", "raw_chain_of_thought", "full_history"],
        quote_policy="capped_excerpts",
        sticky=True,
        relation="child_to_parent",
    ),
    "peer_consult": VisibilitySpec(
        share=["goal_slice", "need", "constraints", "evidence", "sibling_summary", "prior_answers"],
        redact=["secrets", "raw_chain_of_thought", "full_history", "sibling_full"],
        quote_policy="citations_only",
        sticky=False,
        relation="peer_consult",
    ),
    "dialogue_turn": VisibilitySpec(
        share=["goal_slice", "need", "prior_answers", "constraints", "sibling_summary"],
        redact=["secrets", "raw_chain_of_thought", "full_history", "sibling_full", "tool_raw"],
        quote_policy="capped_excerpts",
        sticky=True,
        relation="dialogue_turn",
    ),
    "ask_user": VisibilitySpec(
        share=["goal_slice", "need", "constraints"],
        redact=[
            "secrets",
            "other_subtree",
            "raw_chain_of_thought",
            "full_history",
            "internal_roles",
            "token_budget",
        ],
        quote_policy="none",
        sticky=True,
        relation="ask_user",
    ),
}


def template_for(relation: str) -> VisibilitySpec:
    key = (relation or "ask_user").strip().lower() or "ask_user"
    base = TEMPLATES.get(key) or TEMPLATES["ask_user"]
    return VisibilitySpec(
        share=list(base.share),
        redact=list(base.redact),
        quote_policy=base.quote_policy,
        sticky=base.sticky,
        relation=base.relation,
    ).normalized()


def merge_visibility(
    relation: str,
    override: VisibilitySpec | None = None,
) -> VisibilitySpec:
    base = template_for(relation)
    if override is None:
        return base
    o = override.normalized()
    # Explicit share/redact replace when provided non-empty on override relation match
    share = list(o.share) if o.share else list(base.share)
    redact = list(o.redact) if o.redact else list(base.redact)
    # Always keep secrets redacted
    if "secrets" not in redact:
        redact.append("secrets")
    return VisibilitySpec(
        share=share,
        redact=redact,
        quote_policy=o.quote_policy or base.quote_policy,
        sticky=o.sticky if override.sticky is not None else base.sticky,
        relation=relation or base.relation,
    ).normalized()


_FIELD_MAP = {
    "goal_slice": "goal_slice",
    "need": "need",
    "evidence": "evidence",
    "constraints": "constraints",
    "prior_answers": "prior_answers",
    "sibling_summary": "sibling_summary",
    "findings": "prior_answers",
}


def _cap(text: str, limit: int = 1200) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 20].rstrip() + "\n…(truncated)"


def render_visible_context(packet: AskPacket) -> str:
    """Build the context string a recipient is allowed to see."""
    p = packet.normalized()
    vis = p.visibility.normalized()
    lines: list[str] = []
    lines.append("[AskPacket]")
    act = p.act
    lines.append(
        f"act: what={act.what}; how={act.how}; who={act.who}; when={act.when}"
    )
    if act.why:
        lines.append(f"why: {act.why}")
    if act.stop:
        lines.append(f"stop_when: {act.stop}")
    lines.append(
        "visibility: share="
        + ",".join(vis.share)
        + "; redact="
        + ",".join(vis.redact)
        + f"; quote={vis.quote_policy}"
    )

    for key in vis.share:
        attr = _FIELD_MAP.get(key)
        if not attr:
            continue
        val = getattr(p, attr, "") or ""
        if not str(val).strip():
            continue
        body = str(val).strip()
        if vis.quote_policy == "none" and attr in ("evidence", "prior_answers"):
            continue
        if vis.quote_policy == "capped_excerpts":
            body = _cap(body, 800)
        elif vis.quote_policy == "citations_only" and attr == "evidence":
            body = _cap(body, 600)
        lines.append(f"{attr}:\n{body}")

    # reply contract always visible to agents (not to user-facing question text separately)
    rc = p.reply_contract
    lines.append(
        "reply_contract: format="
        + rc.format
        + "; slots="
        + ",".join(rc.slots)
    )
    if p.target:
        lines.append(f"target: {p.target}")
    return "\n".join(lines).strip() + "\n"


def render_user_question(packet: AskPacket, question: str) -> str:
    """User-facing question: keep short; attach only allowed slices."""
    p = packet.normalized()
    q = (question or p.need or "").strip()
    bits: list[str] = []
    if p.goal_slice and "goal_slice" in p.visibility.share:
        bits.append(f"（背景：{_cap(p.goal_slice, 160)}）")
    if p.constraints and "constraints" in p.visibility.share:
        bits.append(f"（约束：{_cap(p.constraints, 120)}）")
    if act_why := p.act.why:
        bits.append(f"（原因：{_cap(act_why, 120)}）")
    prefix = " ".join(bits)
    if prefix:
        return f"{prefix}\n{q}".strip()
    return q
