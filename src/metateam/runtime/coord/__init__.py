"""Coordination layer: AskAct / AskPacket / VisibilitySpec."""

from .policy import (
    build_packet,
    enrich_child_context,
    enrich_consult_context,
    enrich_dialogue_extra,
    format_delegate_payload,
    parse_child_summary,
    prepare_user_ask,
)
from .schemas import AskAct, AskPacket, ChildResult, ReplyContract, VisibilitySpec
from .visibility import render_user_question, render_visible_context, template_for

__all__ = [
    "AskAct",
    "AskPacket",
    "ChildResult",
    "ReplyContract",
    "VisibilitySpec",
    "build_packet",
    "enrich_child_context",
    "enrich_consult_context",
    "enrich_dialogue_extra",
    "format_delegate_payload",
    "parse_child_summary",
    "prepare_user_ask",
    "render_user_question",
    "render_visible_context",
    "template_for",
]
