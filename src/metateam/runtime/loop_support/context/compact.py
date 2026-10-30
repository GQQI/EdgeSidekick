"""Context compaction, pins, parking, and compress."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional, TYPE_CHECKING

from ....core.repetition import normalize_content_text
from .tokens import (
    _content_text,
    estimate_tokens,
    message_tokens,
    messages_tokens,
    schemas_tokens,
    context_budget_tokens,
)

if TYPE_CHECKING:
    from ...llm import LLM

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
- ## Files: list paths already read/written/listed/deleted this session (e.g. report.md lines 1-136; deleted: old.txt). Keep deleted paths so the agent does not recreate or re-delete them. The agent can re-read surviving files; do not invent paths.
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


# FS mutations must remain attributable after clearing/parking (path + outcome).
_MUTATION_TOOLS = frozenset({"write_file", "str_replace", "delete_file"})


def is_successful_mutation(msg: dict[str, Any]) -> bool:
    """True for a successful write_file / str_replace / delete_file tool result."""
    if msg.get("role") != "tool":
        return False
    name = str(msg.get("name") or "")
    if name not in _MUTATION_TOOLS:
        return False
    text = _content_text(msg).strip()
    if not text:
        return False
    return not text.upper().startswith("ERROR")


def _tool_result_stub(msg: dict[str, Any]) -> str:
    """Claude-style clear_tool_uses: keep the call, drop the bulky payload."""
    name = str(msg.get("name") or "tool")
    text = _content_text(msg)
    extra = ""
    m = _RANGE_TRAILER_RE.search(text)
    if m:
        extra = f" lines {m.group(1)}-{m.group(2)} of {m.group(3)}"
    elif name in _MUTATION_TOOLS:
        # Keep path + first outcome line so early deletes survive squeeze/park.
        first = text.strip().split("\n", 1)[0][:160]
        if first:
            extra = f" ({first})"
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


def dedupe_repeated_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shrink exact repeats in the model payload without breaking tool chains.

    A retry loop can return the same file dump dozens of times. Every copy is
    sent back to the provider on every round, which is both expensive and a
    strong repetition cue for the model. Tool rows stay (one is required per
    ``tool_call_id``); a repeat's body becomes a back-reference to the earlier
    identical result. Repeated tool-call narration keeps the call and drops the
    retold lead-in; adjacent identical assistant finals collapse to one.
    """
    out: list[dict[str, Any]] = []
    seen_tools: set[tuple[str, str]] = set()
    seen_narration: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            # Within one turn a repeated result is noise. Across turns — and
            # across plan steps — the same file/command result can be the fresh
            # answer to a new request. Internal nudges do not start a new scope.
            meta = msg.get("sidekick") if isinstance(msg.get("sidekick"), dict) else {}
            kind = str((meta or {}).get("kind") or "")
            internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
            if not internal or kind == "plan_step":
                seen_tools.clear()
                seen_narration.clear()
            out.append(msg)
            continue
        if role == "assistant" and msg.get("tool_calls"):
            # Keep the call, drop a repeated lead-in. Models often say the same
            # "let me check X：" before every retry; the retold narration is a
            # strong repetition cue even though each tool call is real. Compare
            # content, not layout: a rephrased comma or line wrap is still the
            # same lead-in, and leaving it in trains the loop.
            text = str(msg.get("content") or "").strip()
            key = normalize_content_text(text)
            if key and key in seen_narration:
                clone = dict(msg)
                clone["content"] = ""
                out.append(clone)
                continue
            if key:
                seen_narration.add(key)
            out.append(msg)
            continue
        if role == "tool":
            content = str(msg.get("content") or "")
            name = str(msg.get("name") or "tool")
            key = (name, content)
            if content and key in seen_tools:
                clone = dict(msg)
                clone["content"] = f"[same {name} result as an earlier call]"
                out.append(clone)
                continue
            if content:
                seen_tools.add(key)
            out.append(msg)
            continue
        if role == "assistant" and not msg.get("tool_calls"):
            text = str(msg.get("content") or "").strip()
            prev = out[-1] if out else None
            if (
                text
                and prev is not None
                and prev.get("role") == "assistant"
                and not prev.get("tool_calls")
                and normalize_content_text(str(prev.get("content") or ""))
                == normalize_content_text(text)
            ):
                continue
        out.append(msg)
    return out


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
    protect_from: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep a working set; return older messages unchanged for the archive.

    Opening a long chat used to load the entire transcript into the live
    window, so the next read was trimmed to free space and the model fetched
    the same file again. The archived prefix is not summarized here — display
    code stitches it back. Only the live tail is shortened, and only when that
    tail alone is still over the limit.

    Successful FS mutations that would leave the live window are kept as
    one-line stubs (path + action). When ``protect_from`` is set (usually
    ``current_turn_start``), messages at/after that index are never archived
    wholesale — bulky ones may be stubbed instead.
    """
    if not messages:
        return [], []
    limit = max(2000, int(context_limit or 0))
    if messages_tokens(messages) <= limit:
        return list(messages), []
    system = messages[0] if messages[0].get("role") == "system" else None
    sys_off = 1 if system else 0
    body = list(messages[1:] if system else messages)
    keep = max(2000, min(int(keep_recent_tokens or 0) or limit, limit))
    head, tail = _split_keep_recent(body, keep)
    tail_ids = {id(m) for m in tail}
    archived: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    protect_body = (
        max(0, int(protect_from) - sys_off) if protect_from is not None else None
    )
    for i, m in enumerate(body):
        if id(m) in tail_ids:
            continue
        abs_i = i + sys_off
        protected = protect_body is not None and abs_i >= protect_body
        if protected or is_successful_mutation(m):
            text = _content_text(m) if m.get("role") == "tool" else ""
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                if is_successful_mutation(m) and len(text) <= 240:
                    retained.append(m)
                elif is_successful_mutation(m) or len(text) > 400:
                    nm = dict(m)
                    nm["content"] = _tool_result_stub(m)
                    retained.append(nm)
                else:
                    retained.append(m)
            else:
                retained.append(m)
            continue
        archived.append(m)
    live = ([system] if system else []) + retained + tail
    if messages_tokens(live) > limit:
        live_protect = (1 if system else 0) if protect_from is not None else None
        live, _did = cheap_compact(live, protect_from=live_protect)
    return live, archived


# Shown inside the compaction stub. Must never read like a user saying the
# assistant reply was truncated — that caused models to apologize and stop.
_COMPACTION_CONTINUE_NOTE = (
    "Internal session memory refresh only. "
    "This is NOT a user message and NOT a truncated assistant reply — "
    "do not apologize about truncation or stop. "
    "Continue the same unfinished user goal using Progress/Next and pinned "
    "facts below. Call tools if work remains; answer only when the goal is "
    "met or blocked on the user."
)


def obligation_from_compaction(text: str) -> str:
    """Pull durable user requests out of a compaction stub for goal checks."""
    raw = (text or "").strip()
    if not raw.startswith(COMPACTION_MARK):
        return ""
    body = raw[len(COMPACTION_MARK) :].lstrip("\n")
    # Prefer explicit user-request bullets under pins.
    if "### User requests" in body:
        section = body.split("### User requests", 1)[1]
        for stop in ("### Artifacts", "### URLs", "### Errors", _MEMORY_MARK, "## "):
            if stop in section:
                section = section.split(stop, 1)[0]
        bullets: list[str] = []
        for line in section.splitlines():
            s = line.strip()
            if s.startswith("- "):
                bullets.append(s[2:].strip())
        if bullets:
            # Latest ask matters most; keep a little history.
            keep = bullets[-3:] if len(bullets) > 3 else bullets
            return " | ".join(keep)
    if _PIN_MARK in body:
        pin = body.split(_PIN_MARK, 1)[1]
        if _MEMORY_MARK in pin:
            pin = pin.split(_MEMORY_MARK, 1)[0]
        pin = pin.strip()
        if pin:
            return pin[:2000]
    return ""


def _build_compaction_message(pins: str, memory: str) -> dict[str, Any]:
    parts = [COMPACTION_MARK, _COMPACTION_CONTINUE_NOTE]
    if pins.strip():
        parts.append(pins.strip())
    if memory.strip():
        parts.append(f"{_MEMORY_MARK}\n{memory.strip()}")
    return {
        "role": "user",
        "content": "\n\n".join(parts),
        # Internal: never treat as a fresh user turn / truncation complaint.
        "sidekick_internal": True,
        "sidekick": {"internal": True, "kind": "compaction"},
    }


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

