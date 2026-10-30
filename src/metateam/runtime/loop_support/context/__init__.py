"""Context engineering package — estimate, compact, fit."""

from .tokens import (
    estimate_tokens,
    message_tokens,
    messages_tokens,
    schemas_tokens,
    context_budget_tokens,
)
from .compact import (
    COMPACTION_MARK,
    is_successful_mutation,
    current_turn_start,
    dedupe_repeated_context,
    cheap_compact,
    squeeze_fresh_tool_results,
    extract_durable_pins,
    park_overflow,
    obligation_from_compaction,
    compress_messages,
)
from .fit import (
    ensure_fit,
    repair_incomplete_tool_chains,
    debug_dump_budget,
)

__all__ = [
    "estimate_tokens",
    "message_tokens",
    "messages_tokens",
    "schemas_tokens",
    "context_budget_tokens",
    "COMPACTION_MARK",
    "is_successful_mutation",
    "current_turn_start",
    "dedupe_repeated_context",
    "cheap_compact",
    "squeeze_fresh_tool_results",
    "extract_durable_pins",
    "park_overflow",
    "obligation_from_compaction",
    "compress_messages",
    "ensure_fit",
    "repair_incomplete_tool_chains",
    "debug_dump_budget",
]
