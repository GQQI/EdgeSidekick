"""Ensure messages fit the context budget."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional, TYPE_CHECKING

from .tokens import messages_tokens
from .compact import (
    COMPACTION_MARK,
    _CLEARED_PREFIX,
    _detach_oldest_group,
    cheap_compact,
    compress_messages,
    current_turn_start,
)

if TYPE_CHECKING:
    from ...llm import LLM

def ensure_fit(
    messages: list[dict[str, Any]],
    *,
    context_limit: int,
    keep_recent_tokens: int,
    trigger_ratio: float,
    max_attempts: int,
    llm: Optional["LLM"],
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit messages into the budget.

    One cheap pass (always), at most one LLM summary, then hard trim.
    ``max_attempts`` is kept for callers but no longer runs serial LLM rounds.

    ``meta["dropped_messages"]`` carries every whole message removed from the
    working set (folded into the compaction summary, or hard-trimmed) so the
    caller can archive them for a lossless display/persistence transcript.
    """
    del max_attempts  # single-pass; reserved so existing callers keep working
    cur = list(messages)
    dropped: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"attempts": 0, "compressed": False, "final_tokens": 0, "llm_passes": 0}
    tokens_now = messages_tokens(cur)
    if on_progress:
        on_progress(
            {
                "phase": "cheap",
                "attempt": 1,
                "max_attempts": 1,
                "tokens": tokens_now,
                "limit": context_limit,
                "message": "正在整理上下文…",
            }
        )
    cur, cheap_did = cheap_compact(cur, protect_from=current_turn_start(cur))
    if cheap_did:
        meta["compressed"] = True
        meta["cheap"] = True

    trigger = int(context_limit * trigger_ratio)
    if messages_tokens(cur) < trigger:
        meta["final_tokens"] = messages_tokens(cur)
        meta["dropped_messages"] = dropped
        return cur, meta

    cur, summary, did, head_dropped = compress_messages(
        cur,
        context_limit=context_limit,
        keep_recent_tokens=keep_recent_tokens,
        trigger_ratio=trigger_ratio,
        llm=llm,
        prior_summary="",
    )
    dropped.extend(head_dropped)
    meta["attempts"] = 1
    if did:
        meta["compressed"] = True
        meta["llm_passes"] = 1 if llm is not None else 0
        meta["summary_preview"] = (summary or "")[:400]

    if messages_tokens(cur) < trigger:
        meta["final_tokens"] = messages_tokens(cur)
        meta["dropped_messages"] = dropped
        return cur, meta

    if on_progress:
        on_progress(
            {
                "phase": "hard_trim",
                "attempt": 1,
                "max_attempts": 1,
                "tokens": messages_tokens(cur),
                "limit": context_limit,
                "message": "正在裁剪过长工具输出…",
            }
        )
    system = cur[0] if cur and cur[0].get("role") == "system" else None
    body = cur[1:] if system else cur
    body, _ = cheap_compact(
        body, old_cap=500, recent_cap=900, recent_tools=2,
        protect_from=current_turn_start(body),
    )
    # Never drop the compaction pin or the latest user line if we can help it.
    guard: list[dict[str, Any]] = []
    rest = body
    if body and str(body[0].get("content") or "").startswith(COMPACTION_MARK):
        guard.append(body[0])
        rest = body[1:]
    while messages_tokens(([system] if system else []) + guard + rest) > context_limit and len(rest) > 2:
        # Drop one assistant together with the tool results it owns. Removing
        # only the assistant leaves role=tool messages and the next request 400s.
        nxt, removed = _detach_oldest_group(rest)
        if not removed:
            break
        dropped.extend(removed)
        rest = nxt
    out = ([system] if system else []) + guard + rest
    meta["final_tokens"] = messages_tokens(out)
    meta["hard_trimmed"] = True
    meta["compressed"] = True
    meta["dropped_messages"] = dropped
    return out, meta




def repair_incomplete_tool_chains(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make post-compaction history safe for the next provider call.

    Compaction / park can leave an assistant ``tool_calls`` row without every
    matching tool result (or orphan tool rows). Providers then 400, the loop
    sees ``assistant is None``, and the UI shows an empty round. Stub any
    missing results and drop orphan tool messages so the same user task can
    continue.
    """
    if not messages:
        return messages
    out: list[dict[str, Any]] = []
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
            names = {
                str(tc.get("id") or ""): str(
                    ((tc.get("function") or {}) if isinstance(tc, dict) else {}).get("name")
                    or "tool"
                )
                for tc in calls
                if isinstance(tc, dict)
            }
            j = i + 1
            found: dict[str, dict[str, Any]] = {}
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid and tid in ids and tid not in found:
                    found[tid] = messages[j]
                j += 1
            out.append(m)
            for tid in ids:
                if tid in found:
                    out.append(found[tid])
                else:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "name": names.get(tid) or "tool",
                            "content": (
                                f"{_CLEARED_PREFIX} {names.get(tid) or 'tool'}. "
                                "Payload unavailable after context compaction; "
                                "re-call the tool if you still need it.]"
                            ),
                        }
                    )
            i = j
            continue
        if m.get("role") == "tool":
            # Orphan tool row (no preceding assistant call in the rebuilt set).
            i += 1
            continue
        out.append(m)
        i += 1
    return out


def debug_dump_budget(messages: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"messages": len(messages), "tokens_est": messages_tokens(messages)},
        ensure_ascii=False,
    )

