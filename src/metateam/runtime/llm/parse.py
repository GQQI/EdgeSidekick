"""LLM parse / extract / tool-arg helpers."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Iterator, Optional

from openai import OpenAI

from ..think_tags import ThinkTagSplitter, split_think_tags
from ..text_tool_calls import (
    XmlToolStream,
    merge_text_tool_calls,
    split_before_tool_markup,
)
from ...core.config import Settings
from ...core.logutil import get_logger
from ...core.repetition import (
    MAX_REPEAT_SPAN,
    fold_trailing_repeats,
    normalize_content_text,
    reasoning_hard_looped,
    reasoning_looped,
    repeated_tail_span,
)

def coerce_tool_arg_piece(value: Any) -> str:
    """Normalize a streamed tool-argument fragment to text.

    Some gateways send ``arguments`` as a dict (already parsed) instead of a
    JSON string. ``str(dict)`` yields Python repr with single quotes, which is
    not JSON and will 400 on the next request.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def merge_stream_fragment(prev: str, piece: str, *, json_payload: bool = False) -> str:
    """Join one streamed tool name/arguments chunk.

    OpenAI-style streams append *deltas*. MiniMax / some vLLM parsers instead
    resend a growing *snapshot* of the whole JSON each chunk. Concatenating
    snapshots produces ``{"path": ...{"path": ...`` which is not JSON — and
    that illegal string is what the next chat.completions call POSTs back,
    causing ``Unterminated string`` / 400.

    ``json_payload`` marks argument streams. A delta there may legitimately
    equal the opening of the accumulator — e.g. the first ``tasks[]`` item
    starts with ``{"`` while the root already reads ``{"tasks": [``. Treating
    that as a regressive snapshot deleted the brace before ``goal``, leaving
    invalid JSON and collapsing N delegated workers into one.
    """
    if not piece:
        return prev or ""
    if not prev:
        return piece
    if piece == prev:
        return prev
    if piece.startswith(prev):
        return piece
    # A later, shorter snapshot repeats the opening of the accumulator.
    # A one-character "{" delta is also a prefix of every JSON object, and
    # CSS/JS bodies stream that token alone. Dropping it deletes the brace
    # and leaves "*margin:0}" / ".shelldisplay:grid}".
    if piece != "{" and prev.startswith(piece):
        # For JSON arguments the accumulator's opening is not evidence of a
        # snapshot unless it is already a complete object. Otherwise this is
        # a real delta (nested object open) and must be appended.
        if not json_payload or _try_json_object(prev) is not None:
            return prev
    prev_obj = _try_json_object(prev)
    piece_obj = _try_json_object(piece)
    if isinstance(prev_obj, dict) and isinstance(piece_obj, dict):
        # Two complete snapshots of the same call — keep the longer one.
        return piece if len(piece) >= len(prev) else prev
    return prev + piece

def _try_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None

def _tool_call_slot_index(
    tc: Any,
    tool_acc: dict[int, dict[str, str]],
    id_to_idx: dict[str, int],
) -> int:
    raw_idx = getattr(tc, "index", None)
    if raw_idx is not None and str(raw_idx) != "":
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            idx = None
        else:
            cid = getattr(tc, "id", None)
            if cid:
                id_to_idx[str(cid)] = idx
            return idx
    cid = str(getattr(tc, "id", None) or "")
    if cid and cid in id_to_idx:
        return id_to_idx[cid]
    # Argument fragments often omit index and id. A new slot would keep the
    # name on the previous call and drop the arguments (executed as {}).
    if tool_acc and not cid:
        return max(tool_acc)
    idx = (max(tool_acc) + 1) if tool_acc else 0
    if cid:
        id_to_idx[cid] = idx
    return idx

def merge_reasoning_fragment(prev: str, piece: str) -> str:
    """Join one streamed reasoning/content chunk.

    Growing *snapshots* (piece strictly extends prev) replace. An identical
    piece is treated as another delta copy and appended so loop detectors can
    see consecutive repeats. A mere prefix of prev is also appended — unlike
    tool-arg snapshot merging — because prose loops often resend the same
    paragraph.
    """
    if not piece:
        return prev or ""
    if not prev:
        return piece
    if len(piece) > len(prev) and piece.startswith(prev):
        return piece
    return prev + piece

def fold_reasoning_text(text: str) -> str:
    """Fold a finished reasoning block (non-stream responses, fallbacks)."""
    body = str(text or "").strip()
    if not body:
        return ""
    folded, _ = collapse_streaming_repeats(
        body, min_span=REASONING_MIN_SPAN, max_keep=1
    )
    return folded.strip()

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

def salvage_tasks_array(raw: str) -> list[Any] | None:
    """Pull complete objects from a truncated ``"tasks": [ ...`` payload.

    DeepSeek often streams a huge ``delegate_task`` body: top-level ``goal``
    (first lane) closes first, then ``tasks`` is cut mid-array. Salvaging the
    finished worker objects avoids collapsing N lanes into that single goal.
    """
    text = raw or ""
    match = re.search(r'"tasks"\s*:\s*\[', text)
    if not match:
        return None
    decoder = json.JSONDecoder()
    items: list[Any] = []
    idx = match.end()
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or text[idx] == "]":
            break
        try:
            value, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if end <= idx:
            break
        items.append(value)
        idx = end
    return items or None

def _unescape_json_fragment(text: str) -> str:
    return (
        (text or "")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )

def _closed_string_fields(raw: str) -> dict[str, str]:
    """Copy only fully closed JSON string fields — never invent a closer."""
    out: dict[str, str] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if not closed:
            continue
        body = closed.group(1)
        try:
            out[key] = json.loads(f'"{body}"', strict=False)
        except json.JSONDecodeError:
            out[key] = _unescape_json_fragment(body)
    return out

def decode_tool_args_json(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse tool ``arguments`` without fabricating closers.

    Returns ``(object, reason)``. ``reason`` is one of:
    ``ok``, ``control_chars`` (raw newlines/tabs inside strings — models do
    this for write_file content), ``concatenated`` (two complete objects
    glued by snapshot+delta mix-up), ``truncated``, ``invalid``.
    """
    if isinstance(raw, dict):
        return dict(raw), "ok"
    if raw is None:
        return {}, "ok"
    if isinstance(raw, (list, int, float, bool)):
        return None, "invalid"
    text = str(raw).strip()
    if not text:
        return {}, "ok"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, "ok"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    try:
        data = json.loads(text, strict=False)
        if isinstance(data, dict):
            return data, "control_chars"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder(strict=False)
    found: list[dict[str, Any]] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            data, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if end <= idx:
            break
        if isinstance(data, dict):
            found.append(data)
        idx = end
    if found and idx >= n:
        return found[-1], "concatenated" if len(found) > 1 else "ok"
    if found:
        return None, "truncated"
    return None, "truncated"

def canonical_tool_arguments(raw: Any) -> str:
    """Wire-format tool arguments: always a JSON object string.

    Gateways (vLLM / MiniMax / OpenAI-compatible) ``json.loads`` this field
    on the *next* request. An unterminated write_file ``content`` string is
    exactly ``Unterminated string starting at: line 1 column 60``.

    For truncated ``delegate_task`` payloads, keep any fully closed worker
    objects from ``tasks[]`` — DeepSeek often finishes top-level ``goal``
    first; stripping tasks here forced a goal-only ``_incomplete`` placeholder
    and an error loop.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return text
            except json.JSONDecodeError:
                pass
        else:
            return "{}"
    elif raw is None:
        return "{}"
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False)
    text = raw if isinstance(raw, str) else ""
    placeholder: dict[str, Any] = {"_incomplete": True}
    # Only fully closed string fields — never an open write_file content.
    for key, value in _closed_string_fields(text).items():
        placeholder[key] = value
    # Keep finished worker objects from a truncated tasks[] array.
    tasks = salvage_tasks_array(text)
    if tasks:
        placeholder["tasks"] = tasks
    _log.warning(
        "tool arguments are not valid JSON (%s, %d chars, prefix=%r); "
        "sending a placeholder instead of the raw fragment",
        reason,
        len(text),
        text[:96],
    )
    return json.dumps(placeholder, ensure_ascii=False)

def canonicalize_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn_in = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str((fn_in or {}).get("name") or "")
    arguments = canonical_tool_arguments((fn_in or {}).get("arguments"))
    # delegate_task: fold truncated / legacy goal+context into tasks[] so the
    # wire and UI never keep {_incomplete, goal, context} as the call shape.
    if name == "delegate_task":
        try:
            from .tools.delegate import coerce_delegate_task_args

            coerced = coerce_delegate_task_args(parse_tool_args(arguments))
            if coerced.get("tasks"):
                arguments = json.dumps(coerced, ensure_ascii=False)
        except Exception:
            pass
    fn = {
        "name": name,
        "arguments": arguments,
    }
    out = {k: v for k, v in tc.items() if k != "function"}
    out["type"] = out.get("type") or "function"
    out["function"] = fn
    return out

def _loads_partial_object(raw: str) -> dict[str, Any] | None:
    """Best-effort field extract for *error reporting only*.

    Do not use this to decide a write_file payload is complete — appending
    ``"}`` would hide truncation and still leave illegal JSON in history.
    """
    text = (raw or "").strip()
    if not text:
        return None
    obj, reason = decode_tool_args_json(text)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    return None

def _salvage_tool_args(raw: str) -> dict[str, Any]:
    repaired = _loads_partial_object(raw)
    if repaired:
        if "tasks" not in repaired:
            salvaged_tasks = salvage_tasks_array(raw)
            if salvaged_tasks:
                repaired = dict(repaired)
                repaired["tasks"] = salvaged_tasks
        return repaired
    out: dict[str, Any] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if closed:
            out[key] = _unescape_json_fragment(closed.group(1))
            continue
        opened = re.search(rf'"{key}"\s*:\s*"(.*)$', raw or "", re.DOTALL)
        if opened:
            out[key] = _unescape_json_fragment(opened.group(1).rstrip('"'))
    salvaged_tasks = salvage_tasks_array(raw)
    if salvaged_tasks:
        out["tasks"] = salvaged_tasks
    return out if out else {"_raw": raw}

def parse_tool_args(raw: Any) -> dict[str, Any]:
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    text = raw if isinstance(raw, str) else str(raw or "")
    salvaged = dict(_salvage_tool_args(text) or {})
    salvaged["_incomplete"] = True
    # Keep the wire fragment so delegate_task can detect a truncated tasks[].
    salvaged["_raw"] = text
    return salvaged

def align_tool_chains(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool results the provider would reject.

    A ``tool`` message is only valid as the reply to the assistant message that
    issued its id. A compacted transcript can append an older result again, or
    repeat a whole assistant/tool span. Either one makes the next request 400
    with "tool must be a response to a preceding message with tool_calls".
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if isinstance(calls, list) and calls:
            ids = [
                str(tc.get("id") or "")
                for tc in calls
                if isinstance(tc, dict) and str(tc.get("id") or "")
            ]
            j = i + 1
            found: dict[str, dict[str, Any]] = {}
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid and tid in ids and tid not in found and tid not in seen:
                    found[tid] = messages[j]
                j += 1
            if ids and all(tid in seen for tid in ids):
                i = j
                continue
            if not ids or j == i + 1:
                # No results followed. Keep a trailing call so argument repair
                # still runs; an interrupted call is text-only.
                if j < n:
                    kept = dict(m)
                    kept.pop("tool_calls", None)
                    out.append(kept)
                else:
                    out.append(m)
                i = j
                continue
            if all(tid in found for tid in ids):
                out.append(m)
                for tid in ids:
                    out.append(found[tid])
                    seen.add(tid)
            else:
                kept = dict(m)
                kept.pop("tool_calls", None)
                out.append(kept)
            i = j
            continue
        if m.get("role") == "tool":
            i += 1
            continue
        out.append(m)
        i += 1
    return out

