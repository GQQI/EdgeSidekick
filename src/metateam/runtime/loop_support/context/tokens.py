"""Token estimation helpers for context budgeting."""

from __future__ import annotations

import json
from typing import Any, Optional

def estimate_tokens(text: str) -> int:
    """Heuristic token count (not a tokenizer). CJK ~1.6 chars/token, Latin/code ~3.2."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3400 <= o <= 0x4DBF
            or 0x3000 <= o <= 0x303F
            or 0xFF00 <= o <= 0xFFEF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF
        ):
            cjk += 1
        else:
            other += 1
    return max(1, int(cjk / 1.6 + other / 3.2))


def _content_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(content)


def message_tokens(msg: dict[str, Any]) -> int:
    """Tokens that are actually sent.

    ``reasoning`` / ``reasoning_content`` stay on the message for the thinking
    bubble, but the provider payload omits them. Counting them made compression
    delete file bodies to make room for text the model never receives, and the
    model then read those files again.
    """
    n = estimate_tokens(_content_text(msg))
    n += estimate_tokens(str(msg.get("name") or ""))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        n += estimate_tokens(fn.get("name", "")) + estimate_tokens(fn.get("arguments", ""))
        n += 8
    n += estimate_tokens(str(msg.get("tool_call_id") or ""))
    return n + 8


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(message_tokens(m) for m in messages)


def schemas_tokens(schemas: Optional[list[dict[str, Any]]]) -> int:
    """Estimate tokens for the tools[] payload sent every LLM turn."""
    if not schemas:
        return 0
    try:
        return estimate_tokens(json.dumps(schemas, ensure_ascii=False))
    except Exception:
        return 0


def context_budget_tokens(
    messages: list[dict[str, Any]],
    schemas: Optional[list[dict[str, Any]]] = None,
    *,
    overhead: int = 256,
) -> int:
    """Messages + tool schemas + small fixed overhead (closer to real API spend)."""
    return messages_tokens(messages) + schemas_tokens(schemas) + overhead
