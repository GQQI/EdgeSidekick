from __future__ import annotations
"""LLM message / reasoning text extraction helpers."""


from typing import Any

from .think_tags import split_think_tags

_REASONING_FIELD_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "thought",
)

_THINK_PART_TYPES = frozenset(
    {
        "thinking",
        "reasoning",
        "reasoning_content",
        "reasoning_text",
        "reasoning.text",
        "thought",
    }
)
_REASONING_TEXT_KEYS = (
    "thinking",
    "reasoning_content",
    "reasoning",
    "thought",
    "text",
    "content",
    "summary",
)


def _as_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for name in ("model_dump", "to_dict"):
        fn = getattr(obj, name, None)
        if not callable(fn):
            continue
        try:
            dumped = fn(exclude_none=True) if name == "model_dump" else fn()
        except TypeError:
            try:
                dumped = fn()
            except Exception:
                dumped = None
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            out.update(dumped)
            break
    extra = getattr(obj, "model_extra", None)
    if extra is None:
        extra = getattr(obj, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        out.update(extra)
    return out


def _choice_reasoning_extras(choice: Any) -> dict[str, Any]:
    """Choice-level extras only — never re-read nested delta/message."""
    data = _as_mapping(choice)
    data.pop("delta", None)
    data.pop("message", None)
    data.pop("logprobs", None)
    return data


def _is_thinking_part(part: Any) -> bool:
    if isinstance(part, dict):
        if part.get("thought") is True:
            return True
        return str(part.get("type") or "").lower() in _THINK_PART_TYPES
    if getattr(part, "thought", None) is True:
        return True
    return str(getattr(part, "type", "") or "").lower() in _THINK_PART_TYPES


def _part_visible_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        return text if isinstance(text, str) else ""
    text = getattr(part, "text", None)
    return text if isinstance(text, str) else ""


def _coerce_reasoning_text(value: Any, *, _depth: int = 0) -> str:
    """Turn provider-specific reasoning payloads into a plain string."""
    if value is None or _depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool, bytes)):
        return ""
    if isinstance(value, list):
        return "".join(_coerce_reasoning_text(item, _depth=_depth + 1) for item in value)
    if isinstance(value, dict):
        ptype = str(value.get("type") or "").lower()
        if ptype in {"tool_use", "tool_call", "function_call", "reasoning.encrypted"}:
            return ""
        for key in _REASONING_TEXT_KEYS:
            if key not in value:
                continue
            text = _coerce_reasoning_text(value.get(key), _depth=_depth + 1)
            if text:
                return text
        return ""
    for key in _REASONING_TEXT_KEYS:
        if not hasattr(value, key):
            continue
        text = _coerce_reasoning_text(getattr(value, key, None), _depth=_depth + 1)
        if text:
            return text
    return _coerce_reasoning_text(_as_mapping(value), _depth=_depth + 1)


def _reasoning_from_content_parts(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not _is_thinking_part(part):
            continue
        text = _coerce_reasoning_text(part)
        if text:
            parts.append(text)
    return "".join(parts)


def _is_thought_flag(msg: Any) -> bool:
    if msg is None:
        return False
    if isinstance(msg, dict):
        return msg.get("thought") is True
    return getattr(msg, "thought", None) is True


def extract_content_text(msg: Any) -> str:
    """Visible assistant content only (never reasoning/thinking)."""
    if msg is None:
        return ""
    # Gemini-style: the whole content increment is a thought.
    if _is_thought_flag(msg):
        return ""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
                continue
            if _is_thinking_part(p):
                continue
            text = _part_visible_text(p)
            if text:
                parts.append(text)
        return "".join(parts)
    return ""


def extract_reasoning_text(msg: Any) -> str:
    """Model thinking / reasoning stream pieces only.

    Providers scatter the same increment across many shapes:
    string fields (reasoning_content), objects ({thinking: ...}),
    content-array parts (Claude type=thinking), or choice-level extras.
    Take the first non-empty source only — never concatenate duplicates.
    """
    if msg is None:
        return ""

    data = _as_mapping(msg)
    for key in _REASONING_FIELD_KEYS:
        raw = getattr(msg, key, None) if not isinstance(msg, dict) else None
        if raw is None:
            raw = data.get(key)
        text = _coerce_reasoning_text(raw)
        if text:
            return text

    details = getattr(msg, "reasoning_details", None) if not isinstance(msg, dict) else None
    if details is None:
        details = data.get("reasoning_details")
    text = _coerce_reasoning_text(details)
    if text:
        return text

    content = getattr(msg, "content", None) if not isinstance(msg, dict) else None
    if content is None:
        content = data.get("content")
    if _is_thought_flag(msg) and isinstance(content, str) and content:
        return content
    return _reasoning_from_content_parts(content)


def extract_message_text(msg: Any) -> str:
    """Prefer visible content; do not fall back to reasoning (kept for compat)."""
    content = extract_content_text(msg)
    if content.strip():
        visible, _ = split_think_tags(content)
        return visible
    return ""


_PARTIAL_ARG_KEYS = (
    "goal",
    "task",
    "context",
    "role",
    "topic",
    "query",
    "prompt",
    "description",
    "path",
    "command",
    "content",
    "old_string",
    "new_string",
    "offset",
    "limit",
    "pattern",
)


