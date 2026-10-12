"""Context engineering: estimate, structured compress, hard trim, dual-pressure."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM


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


COMPACTION_MARK = "[CONTEXT COMPACTION]"
_PIN_MARK = "## Pinned (do not drop)"
_MEMORY_MARK = "## Working memory"

_COMPRESS_PROMPT = """You write durable working memory for an ongoing agent session.

The pinned facts are already preserved separately — do NOT repeat them.
Write ONLY these sections, in the user's language:

## Goal
## Progress
## Decisions
## Files
## Open Issues
## Next

Rules:
- Keep every user request, named decision, file path (with line ranges if read), URL, command, error, and ID that still matters.
- ## Files: list paths already read/written/listed this session (e.g. report.md lines 1-136). The agent can re-read them; do not invent paths.
- Never invent files, folders, or APIs that were not observed.
- Drop raw tool dumps, screenshots, HTML, and chit-chat.
- If a previous working-memory block is provided, UPDATE it — do not shrink it into a vague recap.
- Keep under 1600 tokens. Prefer concrete leftovers over prose."""

_URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+", re.IGNORECASE)
_DATA_URI_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]{80,}")


def _stringify_for_summary(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = _content_text(m)
        if m.get("tool_calls"):
            names = [(tc.get("function") or {}).get("name", "?") for tc in m["tool_calls"]]
            content = (content + "\n" if content else "") + f"[tools: {', '.join(names)}]"
        if role == "tool":
            name = str(m.get("name") or "tool")
            clip = content[:700] + ("…" if len(content) > 700 else "")
            parts.append(f"tool {name}: {clip}")
        elif role == "user":
            parts.append(f"user: {content[:4000]}")
        else:
            parts.append(f"{role}: {content[:1800]}")
    return "\n\n".join(parts)


def _strip_heavy_payloads(text: str) -> str:
    if not text:
        return text
    cleaned = _DATA_URI_RE.sub("[image omitted]", text)
    if "<html" in cleaned.lower() and len(cleaned) > 2500:
        cleaned = cleaned[:1200] + "\n…[html truncated]"
    return cleaned


_CLEARED_PREFIX = "[cleared tool result:"
_RANGE_TRAILER_RE = re.compile(r"lines (\d+)-(\d+) of (\d+)")


_BROWSER_STUB_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_console",
        "browser_snapshot",
        "browser_network",
        "browser_diagnostics",
        "browser_get_page_content",
    }
)


def _browser_digest(text: str) -> str:
    """Keep url / title / entry count when a browser payload is cleared."""
    raw = (text or "").strip()
    if not raw or raw.lower().startswith("error"):
        return ""
    bits: list[str] = []
    data: Optional[dict[str, Any]] = None
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
    if data is not None:
        url = str(data.get("url") or "")[:160]
        if url:
            bits.append(url)
        title = str(data.get("title") or "")[:60]
        if title:
            bits.append(title)
        if "count" in data:
            bits.append(f"entries={data.get('count')}")
        obs = data.get("observation")
        if isinstance(obs, str) and obs.strip():
            bits.append(obs.strip().split("\n", 1)[0][:80])
    if not bits and not raw.startswith("{"):
        bits.append(raw.split("\n", 1)[0][:100])
    return "; ".join(bit for bit in bits if bit)[:180]


def _tool_result_stub(msg: dict[str, Any]) -> str:
    """Claude-style clear_tool_uses: keep the call, drop the bulky payload."""
    name = str(msg.get("name") or "tool")
    text = _content_text(msg)
    extra = ""
    m = _RANGE_TRAILER_RE.search(text)
    if m:
        extra = f" lines {m.group(1)}-{m.group(2)} of {m.group(3)}"
    elif name == "read_file":
        first = text.strip().split("\n", 1)[0][:80]
        if first and not first.startswith("ERROR"):
            extra = f" ({first})"
    elif name in _BROWSER_STUB_TOOLS:
        digest = _browser_digest(text)
        if digest:
            extra = f" ({digest})"
    return f"{_CLEARED_PREFIX} {name}{extra}. The payload was dropped to free context.]"


def current_turn_start(messages: list[dict[str, Any]]) -> int:
    """Index of the latest REAL user message (not compaction/internal).

    Everything from there on is the active turn — the agent is still working
    with those tool results, so mid-turn clearing must never touch them.
    """
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "user":
            continue
        if m.get("sidekick_internal") or m.get("internal"):
            continue
        text = str(m.get("content") or "")
        if text.startswith(COMPACTION_MARK) or text.lstrip().startswith("[Plan step "):
            continue
        return i
    return len(messages)


def cheap_compact(
    messages: list[dict[str, Any]],
    *,
    old_cap: int = 900,
    recent_cap: int = 2200,
    recent_tools: int = 4,
    protect_from: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Shrink context without an LLM.

    Tool results from PREVIOUS turns get truncated, and bulky old payloads are
    replaced with a one-line stub that still names the call (Claude Code
    clear_tool_uses / Cursor: keep the record, re-fetch if needed).

    Messages at or after ``protect_from`` (the active turn) are left intact —
    clearing a read_file result the agent is still working from forces it into
    a re-read loop. Pass ``protect_from=None`` to compact everything (only
    safe for snapshots that are about to be summarized anyway).
    """
    if not messages:
        return messages, False
    guard_idx = len(messages) if protect_from is None else max(0, protect_from)
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    # NB: tool_idxs[-0:] would be the whole list — 0 must mean "none recent".
    recent = set(tool_idxs[-recent_tools:]) if recent_tools > 0 else set()
    changed = False
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool" or not isinstance(m.get("content"), str):
            out.append(m)
            continue
        if i >= guard_idx:
            out.append(m)
            continue
        raw = _strip_heavy_payloads(m["content"])
        if i not in recent and not raw.startswith(_CLEARED_PREFIX) and len(raw) > old_cap:
            nm = dict(m)
            nm["content"] = _tool_result_stub(m)
            out.append(nm)
            changed = True
            continue
        cap = recent_cap if i in recent else old_cap
        if len(raw) > cap:
            raw = raw[:cap] + "\n…[truncated]"
        if raw != m["content"]:
            nm = dict(m)
            nm["content"] = raw
            out.append(nm)
            changed = True
        else:
            out.append(m)
    return out, changed


def _hard_trim_tool_payloads(messages: list[dict[str, Any]], cap: int = 1200) -> list[dict[str, Any]]:
    out, _ = cheap_compact(messages, old_cap=cap, recent_cap=cap, recent_tools=0)
    return out


_SQUEEZE_NOTE = "\n…[truncated]"


def squeeze_fresh_tool_results(
    messages: list[dict[str, Any]],
    start: int,
    *,
    target_tokens: int,
    floor_chars: int = 600,
) -> tuple[list[dict[str, Any]], bool]:
    """Shrink tool results appended since ``start`` until the transcript fits.

    Compression only runs at the top of the next loop iteration, so one
    parallel batch of fat tool dumps could push the context far past the
    limit before anything reacts. This trims the *freshest* results, largest
    first, so the window is back under ``target_tokens`` before the next
    provider call. Everything before ``start`` is untouched.
    """
    if start < 0 or start >= len(messages):
        return messages, False
    total = messages_tokens(messages)
    if total <= target_tokens:
        return messages, False
    out = list(messages)
    fresh = [
        i
        for i in range(start, len(out))
        if out[i].get("role") == "tool" and isinstance(out[i].get("content"), str)
    ]
    if not fresh:
        return messages, False
    changed = False
    # Repeatedly halve the largest fresh result until we fit or hit the floor.
    for _ in range(40):
        total = messages_tokens(out)
        if total <= target_tokens:
            break
        fresh.sort(key=lambda i: len(out[i]["content"]), reverse=True)
        idx = fresh[0]
        text = out[idx]["content"]
        if len(text) <= floor_chars:
            break
        body = text
        if body.endswith(_SQUEEZE_NOTE):
            body = body[: -len(_SQUEEZE_NOTE)]
        new_len = max(floor_chars, len(body) // 2)
        nm = dict(out[idx])
        nm["content"] = body[:new_len].rstrip() + _SQUEEZE_NOTE
        out[idx] = nm
        changed = True
    return out, changed


def extract_durable_pins(messages: list[dict[str, Any]], *, prior: str = "") -> str:
    """Lossless-ish facts that must survive even if the LLM summary is lossy."""
    users: list[str] = []
    artifacts: list[str] = []
    errors: list[str] = []
    urls: list[str] = []
    seen_url: set[str] = set()

    def add_url(u: str) -> None:
        u = (u or "").rstrip(").,;]")
        if u and u not in seen_url and not u.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        ):
            seen_url.add(u)
            urls.append(u)

    for m in messages:
        role = m.get("role")
        text = _content_text(m)
        if role == "user" and text and not text.startswith(COMPACTION_MARK):
            clip = text.strip()
            if clip and clip not in users:
                users.append(clip[:2000])
        if role == "tool":
            name = str(m.get("name") or "tool")
            if text.lstrip().upper().startswith("ERROR"):
                errors.append(f"{name}: {text.strip()[:360]}")
            if name in {
                "write_file",
                "str_replace",
                "delete_file",
                "read_file",
                "list_dir",
                "browser_navigate",
                "browser_screenshot",
            }:
                artifacts.append(f"{name}: {text.strip()[:240]}")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            args = str((fn or {}).get("arguments") or "")
            if name and args:
                artifacts.append(f"{name}({args[:180]})")
        for u in _URL_RE.findall(text):
            add_url(u)

    if prior:
        for line in prior.splitlines():
            s = line.strip()
            if s.startswith("- "):
                s = s[2:].strip()
            if s.startswith("http"):
                add_url(s)

    lines = [_PIN_MARK]
    if users:
        lines.append("### User requests")
        # Keep the earliest goals and the latest asks — those are what models forget.
        keep_u = users[:6]
        if len(users) > 6:
            keep_u = users[:3] + users[-3:]
        for u in keep_u:
            lines.append(f"- {u.replace(chr(10), ' / ')[:500]}")
    if artifacts:
        lines.append("### Artifacts")
        for a in artifacts[-16:]:
            lines.append(f"- {a.replace(chr(10), ' ')[:220]}")
    if urls:
        lines.append("### URLs")
        for u in urls[-12:]:
            lines.append(f"- {u}")
    if errors:
        lines.append("### Errors")
        for e in errors[-8:]:
            lines.append(f"- {e.replace(chr(10), ' ')[:220]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _peel_prior_compaction(body: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    pins: list[str] = []
    memories: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in body:
        text = _content_text(m)
        if m.get("role") == "user" and text.startswith(COMPACTION_MARK):
            payload = text[len(COMPACTION_MARK) :].lstrip("\n")
            if _PIN_MARK in payload and _MEMORY_MARK in payload:
                pre, _, mem = payload.partition(_MEMORY_MARK)
                pin = pre
                if _PIN_MARK in pin:
                    pin = pin.split(_PIN_MARK, 1)[1]
                    pins.append(_PIN_MARK + "\n" + pin.strip())
                memories.append(mem.strip())
            else:
                memories.append(payload.strip())
        else:
            rest.append(m)
    return "\n\n".join(p for p in pins if p), "\n\n".join(m for m in memories if m), rest


def _split_keep_recent(
    messages: list[dict[str, Any]], keep_recent_tokens: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not messages:
        return [], []
    # Always keep the latest user utterance even if it is older than the tool tail.
    last_user = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user" and not str(m.get("content") or "").startswith(COMPACTION_MARK):
            last_user = i
    tail: list[dict[str, Any]] = []
    used = 0
    i = len(messages) - 1
    while i >= 0:
        m = messages[i]
        if m.get("role") == "tool":
            block = [m]
            j = i - 1
            while j >= 0 and messages[j].get("role") == "tool":
                block.insert(0, messages[j])
                j -= 1
            if j >= 0 and messages[j].get("role") == "assistant" and messages[j].get("tool_calls"):
                block.insert(0, messages[j])
                j -= 1
            block_cost = sum(message_tokens(x) for x in block)
            if tail and used + block_cost > keep_recent_tokens:
                break
            tail = block + tail
            used += block_cost
            i = j
            continue
        cost = message_tokens(m)
        if tail and used + cost > keep_recent_tokens:
            break
        tail.insert(0, m)
        used += cost
        i -= 1
    head = messages[: i + 1] if i >= 0 else []
    if last_user >= 0:
        held = messages[last_user]
        if held not in tail and held in head:
            head = [m for m in head if m is not held]
            tail = [held] + tail
    return head, tail


def _detach_oldest_group(
    rest: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove the oldest non-user message and any tool results that belong to it."""
    if len(rest) <= 2:
        return rest, []
    for i, m in enumerate(rest[:-1]):
        if m.get("role") == "user":
            continue
        start = i
        end = i + 1
        if m.get("role") == "tool" and i > 0:
            prev = rest[i - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                start = i - 1
        if rest[start].get("role") == "assistant" and rest[start].get("tool_calls"):
            end = start + 1
            while end < len(rest) and rest[end].get("role") == "tool":
                end += 1
        return rest[:start] + rest[end:], rest[start:end]
    return rest, []


def park_overflow(
    messages: list[dict[str, Any]],
    *,
    context_limit: int,
    keep_recent_tokens: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep a working set; return older messages unchanged for the archive.

    Opening a long chat used to load the entire transcript into the live
    window, so the next read was trimmed to free space and the model fetched
    the same file again. The archived prefix is not summarized here — display
    code stitches it back. Only the live tail is shortened, and only when that
    tail alone is still over the limit.
    """
    if not messages:
        return [], []
    limit = max(2000, int(context_limit or 0))
    if messages_tokens(messages) <= limit:
        return list(messages), []
    system = messages[0] if messages[0].get("role") == "system" else None
    body = list(messages[1:] if system else messages)
    keep = max(2000, min(int(keep_recent_tokens or 0) or limit, limit))
    head, tail = _split_keep_recent(body, keep)
    live = ([system] if system else []) + tail
    if messages_tokens(live) > limit:
        live, _did = cheap_compact(live)
    return live, head


def _build_compaction_message(pins: str, memory: str) -> dict[str, Any]:
    parts = [COMPACTION_MARK]
    if pins.strip():
        parts.append(pins.strip())
    if memory.strip():
        parts.append(f"{_MEMORY_MARK}\n{memory.strip()}")
    return {"role": "user", "content": "\n\n".join(parts)}


def compress_messages(
    messages: list[dict[str, Any]],
    *,
    context_limit: int,
    keep_recent_tokens: int,
    trigger_ratio: float,
    llm: Optional["LLM"],
    prior_summary: str = "",
) -> tuple[list[dict[str, Any]], str, bool, list[dict[str, Any]]]:
    """Return (messages, summary, did_compress, dropped_raw_messages).

    ``dropped_raw_messages`` are the ORIGINAL (never summarized/truncated)
    messages that were folded into the synthetic compaction stub — the
    caller should archive them verbatim so a full transcript can still be
    reconstructed for display/persistence even after the working set shrinks.
    """
    if not messages:
        return messages, prior_summary, False, []

    total = messages_tokens(messages)
    if total < int(context_limit * trigger_ratio):
        return messages, prior_summary, False, []

    system = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system else list(messages)
    prior_pins, prior_mem, body = _peel_prior_compaction(body)
    if prior_mem and not prior_summary:
        prior_summary = prior_mem

    pruned, cheap_did = cheap_compact(body, protect_from=current_turn_start(body))
    if messages_tokens(([system] if system else []) + pruned) < int(context_limit * trigger_ratio):
        out_body = pruned
        if prior_pins or prior_summary:
            out_body = [_build_compaction_message(prior_pins, prior_summary)] + pruned
        return ([system] if system else []) + out_body, prior_summary, True, []

    keep = max(4000, int(keep_recent_tokens or 0) or 16000)
    head, tail = _split_keep_recent(pruned, keep)
    extra_did = False
    if not head:
        tail, extra_did = cheap_compact(
            tail, old_cap=700, recent_cap=700, recent_tools=2,
            protect_from=current_turn_start(tail),
        )
        head, tail = _split_keep_recent(tail, max(3000, keep // 2))
    if not head:
        out = tail
        if prior_pins or prior_summary:
            out = [_build_compaction_message(prior_pins, prior_summary)] + tail
        return (
            ([system] if system else []) + out,
            prior_summary,
            cheap_did or extra_did or bool(prior_pins or prior_summary),
            [],
        )

    pins = extract_durable_pins(head, prior=prior_pins)
    blob = _stringify_for_summary(head)
    if prior_summary:
        blob = f"PREVIOUS WORKING MEMORY:\n{prior_summary}\n\nNEW TURNS:\n{blob}"

    summary = ""
    if llm is not None:
        try:
            summary = llm.complete_text(_COMPRESS_PROMPT, blob[:140_000])
        except Exception as exc:  # noqa: BLE001
            summary = f"(compress failed: {exc})\n" + (prior_summary or blob[:2500])
    if not summary:
        summary = prior_summary or blob[:2500]

    synthetic = _build_compaction_message(pins or prior_pins, summary)
    rebuilt = ([system] if system else []) + [synthetic] + tail
    # `head` (cheap-compacted: very long tool dumps may be clipped, but no
    # whole message is dropped) is archived so the full turn history can
    # still be reconstructed for display/persistence after compaction.
    return rebuilt, summary, True, head


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


def debug_dump_budget(messages: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"messages": len(messages), "tokens_est": messages_tokens(messages)},
        ensure_ascii=False,
    )
