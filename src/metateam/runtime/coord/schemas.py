"""AskAct / AskPacket / VisibilitySpec / reply contracts for multi-agent coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

WhatKind = Literal[
    "decision",
    "fact",
    "preference",
    "constraint",
    "review",
    "clarify",
    "execute",
]
HowKind = Literal[
    "options",
    "short_answer",
    "structured",
    "evidence",
    "consult",
    "critique",
    "debate",
    "negotiate",
]
WhoKind = Literal["user", "peer", "child", "tool", "self"]
WhenKind = Literal["now", "batch", "after_subtask", "on_conflict", "defer"]

RelationKind = Literal[
    "parent_to_child",
    "child_to_parent",
    "peer_consult",
    "dialogue_turn",
    "ask_user",
]


@dataclass
class AskAct:
    what: str = "clarify"
    how: str = "options"
    who: str = "user"
    when: str = "now"
    why: str = ""
    stop: str = ""

    def normalized(self) -> "AskAct":
        what = (self.what or "clarify").strip().lower() or "clarify"
        how = (self.how or "options").strip().lower() or "options"
        who = (self.who or "user").strip().lower() or "user"
        when = (self.when or "now").strip().lower() or "now"
        return AskAct(
            what=what,
            how=how,
            who=who,
            when=when,
            why=(self.why or "").strip(),
            stop=(self.stop or "").strip(),
        )


@dataclass
class VisibilitySpec:
    """What the recipient may see. Default = minimum necessary."""

    share: list[str] = field(
        default_factory=lambda: ["goal_slice", "need", "constraints"]
    )
    redact: list[str] = field(
        default_factory=lambda: [
            "secrets",
            "other_subtree",
            "raw_chain_of_thought",
            "full_history",
        ]
    )
    quote_policy: str = "citations_only"  # none | citations_only | capped_excerpts
    sticky: bool = True
    relation: str = "ask_user"

    def normalized(self) -> "VisibilitySpec":
        share = [str(x).strip() for x in (self.share or []) if str(x).strip()]
        redact = [str(x).strip() for x in (self.redact or []) if str(x).strip()]
        qp = (self.quote_policy or "citations_only").strip().lower()
        if qp not in ("none", "citations_only", "capped_excerpts"):
            qp = "citations_only"
        return VisibilitySpec(
            share=share or ["goal_slice", "need", "constraints"],
            redact=redact
            or ["secrets", "other_subtree", "raw_chain_of_thought", "full_history"],
            quote_policy=qp,
            sticky=bool(self.sticky),
            relation=(self.relation or "ask_user").strip() or "ask_user",
        )


@dataclass
class ReplyContract:
    format: str = "prose"  # prose | json_slots | options
    slots: list[str] = field(
        default_factory=lambda: ["findings", "recommendation", "open_questions"]
    )

    def normalized(self) -> "ReplyContract":
        fmt = (self.format or "prose").strip().lower() or "prose"
        slots = [str(s).strip() for s in (self.slots or []) if str(s).strip()]
        return ReplyContract(
            format=fmt,
            slots=slots or ["findings", "recommendation", "open_questions"],
        )


@dataclass
class AskPacket:
    act: AskAct = field(default_factory=AskAct)
    goal_slice: str = ""
    need: str = ""
    evidence: str = ""
    constraints: str = ""
    prior_answers: str = ""
    sibling_summary: str = ""
    visibility: VisibilitySpec = field(default_factory=VisibilitySpec)
    reply_contract: ReplyContract = field(default_factory=ReplyContract)
    target: str = ""  # roster name / child id / "user"

    def normalized(self) -> "AskPacket":
        return AskPacket(
            act=self.act.normalized(),
            goal_slice=(self.goal_slice or "").strip(),
            need=(self.need or "").strip(),
            evidence=(self.evidence or "").strip(),
            constraints=(self.constraints or "").strip(),
            prior_answers=(self.prior_answers or "").strip(),
            sibling_summary=(self.sibling_summary or "").strip(),
            visibility=self.visibility.normalized(),
            reply_contract=self.reply_contract.normalized(),
            target=(self.target or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        p = self.normalized()
        return {
            "act": asdict(p.act),
            "goal_slice": p.goal_slice,
            "need": p.need,
            "evidence": p.evidence,
            "constraints": p.constraints,
            "prior_answers": p.prior_answers,
            "sibling_summary": p.sibling_summary,
            "visibility": asdict(p.visibility),
            "reply_contract": asdict(p.reply_contract),
            "target": p.target,
        }


@dataclass
class ChildResult:
    findings: str = ""
    recommendation: str = ""
    open_questions: list[str] = field(default_factory=list)
    ask_requests: list[dict[str, Any]] = field(default_factory=list)
    raw_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": self.findings,
            "recommendation": self.recommendation,
            "open_questions": list(self.open_questions),
            "ask_requests": list(self.ask_requests),
            "raw_summary": self.raw_summary,
        }


def act_from_mapping(data: Optional[dict[str, Any]]) -> AskAct:
    if not isinstance(data, dict):
        return AskAct()
    return AskAct(
        what=str(data.get("what") or "clarify"),
        how=str(data.get("how") or "options"),
        who=str(data.get("who") or "user"),
        when=str(data.get("when") or "now"),
        why=str(data.get("why") or ""),
        stop=str(data.get("stop") or ""),
    ).normalized()


def visibility_from_mapping(data: Optional[dict[str, Any]]) -> VisibilitySpec:
    if not isinstance(data, dict):
        return VisibilitySpec()
    share = data.get("share")
    redact = data.get("redact")
    return VisibilitySpec(
        share=list(share) if isinstance(share, list) else VisibilitySpec().share,
        redact=list(redact) if isinstance(redact, list) else VisibilitySpec().redact,
        quote_policy=str(data.get("quote_policy") or "citations_only"),
        sticky=bool(data.get("sticky", True)),
        relation=str(data.get("relation") or "ask_user"),
    ).normalized()


def reply_from_mapping(data: Optional[dict[str, Any]]) -> ReplyContract:
    if not isinstance(data, dict):
        return ReplyContract()
    slots = data.get("slots")
    return ReplyContract(
        format=str(data.get("format") or "prose"),
        slots=list(slots) if isinstance(slots, list) else ReplyContract().slots,
    ).normalized()


def packet_from_mapping(data: Optional[dict[str, Any]]) -> AskPacket:
    if not isinstance(data, dict):
        return AskPacket()
    return AskPacket(
        act=act_from_mapping(data.get("act") if isinstance(data.get("act"), dict) else data),
        goal_slice=str(data.get("goal_slice") or data.get("goal") or ""),
        need=str(data.get("need") or data.get("question") or ""),
        evidence=str(data.get("evidence") or ""),
        constraints=str(data.get("constraints") or ""),
        prior_answers=str(data.get("prior_answers") or ""),
        sibling_summary=str(data.get("sibling_summary") or data.get("sibling_summaries") or ""),
        visibility=visibility_from_mapping(
            data.get("visibility") if isinstance(data.get("visibility"), dict) else None
        ),
        reply_contract=reply_from_mapping(
            data.get("reply_contract") if isinstance(data.get("reply_contract"), dict) else None
        ),
        target=str(data.get("target") or data.get("who_target") or ""),
    ).normalized()
