"""Stop identical tool loops and consecutive explore-only thrashing."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any


_PATH_KEY_SUFFIXES = ("path", "file", "dir", "directory")


def _looks_path_key(key: str) -> bool:
    k = (key or "").lower().replace("-", "_")
    if k in {"path", "symbol_or_path"}:
        return True
    return any(k == suffix or k.endswith("_" + suffix) for suffix in _PATH_KEY_SUFFIXES)


def _canonical_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if _looks_path_key(key):
            text = text.replace("\\", "/")
            while text.startswith("./"):
                text = text[2:]
            while "//" in text:
                text = text.replace("//", "/")
            return text
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
        return text
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {
            str(k): _canonical_value(str(k), v)
            for k, v in value.items()
            if not str(k).startswith("_")
        }
    if isinstance(value, list):
        return [_canonical_value(key, item) for item in value]
    return value


def _canonical_args(args: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if str(key).startswith("_"):
            continue
        out[str(key)] = _canonical_value(str(key), value)
    return out


def _sig(name: str, args: dict[str, Any] | None) -> str:
    blob = json.dumps(
        {"n": name, "a": _canonical_args(args)},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# Polling these in a fixed order is a stuck loop, not new work.
_POLL_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_console",
        "browser_snapshot",
        "browser_observe",
        "browser_screenshot",
        "browser_network",
        "browser_diagnostics",
        "shell_job_log",
        "shell_job_list",
        "shell_job_wait",
    }
)


def repeating_tool_sequence(names: list[str]) -> bool:
    """True when recent tool names are the same short cycle.

    Three repeats of a 2–8 name pattern, or four identical polls in a row.
    Writing different files does not match. Reopening a page, reading logs,
    and restarting a server in a fixed order does.
    """
    cleaned = [str(name or "") for name in names if name]
    if len(cleaned) >= 4 and len(set(cleaned[-4:])) == 1 and cleaned[-1] in _POLL_TOOLS:
        return True
    tail = cleaned[-24:]
    for period in range(2, 9):
        if len(tail) < period * 3:
            continue
        unit = tail[-period:]
        # One name repeated is only a loop for poll tools, handled above.
        # run_shell six times can be six different commands.
        if len(set(unit)) < 2:
            continue
        if tail[-period * 2 : -period] == unit and tail[-period * 3 : -period * 2] == unit:
            return True
    return False


def tool_call_signature(name: str, args: dict[str, Any] | None) -> str:
    """Stable id for a tool name + canonical args (used by execute / tests)."""
    return _sig(name, args)


# A successful payload may mention errors (console level, page text). Those
# keys mean the tool itself returned a result, not that the call failed.
_RESULT_KEYS = frozenset(
    {
        "logs",
        "observation",
        "url",
        "count",
        "content",
        "stdout",
        "files",
        "path",
        "text",
        "title",
    }
)


def looks_failed(content: str) -> bool:
    """True when the tool call itself failed.

    Console and page JSON often contain the word "error" inside a successful
    result. Only a failure prefix, a traceback, or a top-level error object
    counts. Nested log text does not.
    """
    text = (content or "").lstrip()
    if not text:
        return False
    head = text[:120].lower()
    if head.startswith("error") or head.startswith("failed") or head.startswith("traceback"):
        return True
    if "traceback" in head:
        return True
    if not text.startswith("{"):
        return False
    try:
        data = json.loads(text)
    except Exception:
        return bool(re.match(r'\{\s*"error"\s*:\s*"', text[:200]))
    if not isinstance(data, dict):
        return False
    if data.get("ok") is False:
        return True
    err = data.get("error")
    if isinstance(err, str) and err.strip() and not (_RESULT_KEYS & set(data.keys())):
        return True
    return False


# Tools that only inspect state. Consecutive use without an action means the
# agent is stuck browsing — including read_file, which used to be exempt.
_EXPLORE_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "search_text",
        "codebase_overview",
        "codebase_find_similar",
        "codebase_impact",
        "coherence_checklist",
        "memory_read",
        "memory_search",
        "memory_list",
    }
)

# Live page state. The same arguments are not the same result: the page,
# console, and network change, and earlier payloads are dropped when context
# is tight. These may be called again. A second copy that is still in flight
# stays blocked so two navigations do not abort each other.
_BROWSER_LIVE_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_screenshot",
        "browser_snapshot",
        "browser_observe",
        "browser_get_page_content",
        "browser_console",
        "browser_find_elements",
        "browser_network",
        "browser_diagnostics",
        "browser_wait",
        "browser_click",
        "browser_input",
        "browser_type",
        "browser_scroll",
        "browser_scroll_to_text",
        "browser_send_keys",
        "browser_press_key",
        "browser_go_back",
        "browser_go_forward",
        "browser_switch_tab",
        "browser_select",
    }
)

# Same args, same result — repeating them is a stuck loop, not progress.
# Browser tools are listed so an in-flight duplicate is rejected, but a
# finished call does not ban the next one.
_IDEMPOTENT_TOOLS = _BROWSER_LIVE_TOOLS | frozenset({"web_search"})

# Explore tools that may be called again with the same args (Claude: re-fetch
# if you still need the bytes). Identical list_dir/search is still blocked.
_REREAD_OK = frozenset({"read_file"})

# Successful identical FS deletes must not be re-fired the same turn.
_REPEAT_BLOCK_MUTATIONS = frozenset({"delete_file"})
_RANGE_TOOLS = frozenset({"read_file"})
_RANGE_META_RE = re.compile(r"lines (\d+)-(\d+) of (\d+)")
_VISIBLE_LINE_RE = re.compile(r"(?m)^(\d+)\|")


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    m = re.match(r"-?\d+", text)
    if not m:
        return default
    try:
        return int(m.group(0))
    except ValueError:
        return default


def _visible_read_span(text: str) -> tuple[int, int] | None:
    """Contiguous numbered lines still present in a read_file result."""
    raw = text or ""
    if raw.startswith("[cleared tool result:") or raw.startswith("[already in context]"):
        return None
    if raw.lstrip().startswith("ERROR"):
        return None
    nums = sorted({int(n) for n in _VISIBLE_LINE_RE.findall(raw)})
    if not nums:
        return None
    start = nums[0]
    end = start
    for n in nums[1:]:
        if n > end + 1:
            break
        end = n
    return start, end


def _range_path_key(args: dict[str, Any] | None) -> str:
    raw = str((args or {}).get("path") or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    while "//" in raw:
        raw = raw.replace("//", "/")
    return raw.lower()


@dataclass
class Guardrails:
    same_call_fail_limit: int = 2
    # Identical successful explore calls: 1 means "result is already in history".
    same_call_ok_limit: int = 1
    # Kept for backwards-compatible construction in tests.
    max_reads_per_path: int = 0
    max_reads_total: int = 0
    max_explore_streak: int = 48
    fails: dict[str, int] = field(default_factory=dict)
    ok_counts: dict[str, int] = field(default_factory=dict)
    # Signature → tool name, so a later write can drop stale file reads
    # without forgetting that a browser URL was already opened.
    ok_names: dict[str, str] = field(default_factory=dict)
    # Short digest of last successful result per signature — for context nudges.
    ok_digests: dict[str, str] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    blocked: set[str] = field(default_factory=set)
    explore_streak: int = 0
    # Plan-prep / gather-only: consecutive reads are the job, not thrashing.
    explore_only: bool = False
    # path -> merged list of (start, end) inclusive line ranges read this turn.
    read_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # path -> total line count, learned from the last successful read.
    read_totals: dict[str, int] = field(default_factory=dict)
    # (path, offset, end) -> how many times this exact covered slice was served a stub.
    stub_serves: dict[tuple[str, int, int], int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def begin_turn(self) -> None:
        """Reset per-user-turn explore budget. Keep fail memory across turns."""
        with self._lock:
            self.explore_streak = 0
            self.ok_counts.clear()
            self.ok_names.clear()
            self.ok_digests.clear()
            self.pending.clear()
            self.explore_only = False
            self.read_ranges.clear()
            self.read_totals.clear()
            self.stub_serves.clear()

    def set_explore_only(self, enabled: bool) -> None:
        with self._lock:
            self.explore_only = bool(enabled)
            if enabled:
                self.explore_streak = 0

    def begin_plan_step(self) -> None:
        """New plan step may explore new files; identical prior reads stay blocked."""
        with self._lock:
            self.explore_streak = 0
            self.pending.clear()

    def _requested_range(self, args: dict[str, Any]) -> tuple[str, int, int] | None:
        path = _range_path_key(args)
        if not path:
            return None
        offset = max(1, _as_int((args or {}).get("offset"), 1))
        limit = _as_int((args or {}).get("limit"), 0)
        total = self.read_totals.get(path)
        if limit > 0:
            req_end = offset + limit - 1
        elif total:
            req_end = total
        else:
            return None
        return path, offset, req_end

    def dedup_read(self, name: str, args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        """File-space dedupe (Cursor-style): never resend bytes already in context.

        Returns (served_content, adjusted_args):
        - (stub, None)      → the whole request is already in context; skip the
          handler and return the short stub instead of duplicate payload.
        - (None, new_args)  → head of the request was already read; run the
          handler with offset moved forward so only NEW lines are returned.
        - (None, None)      → nothing covered; run the handler as-is.
        """
        if name not in _RANGE_TOOLS:
            return None, None
        with self._lock:
            req = self._requested_range(args)
            if req is None:
                return None, None
            path, offset, req_end = req
            ranges = self.read_ranges.get(path, [])
            for start, end in ranges:
                if start <= offset and req_end <= end:
                    key = (path, offset, req_end)
                    self.stub_serves[key] = self.stub_serves.get(key, 0) + 1
                    total = self.read_totals.get(path)
                    if total and start <= 1 and end >= total:
                        return (
                            f"[already in context] {path} ({total} lines) is already in this conversation.",
                            None,
                        )
                    of_total = f" of {total}" if total else ""
                    return (
                        f"[already in context] lines {offset}-{req_end}{of_total} of {path} "
                        f"were returned earlier (covered {start}-{end}).",
                        None,
                    )
            for start, end in ranges:
                # Head overlap: 1-136 already read, request 40-200 → serve 137-200.
                if start <= offset <= end < req_end:
                    new_args = dict(args or {})
                    new_args["offset"] = end + 1
                    new_args["limit"] = req_end - end
                    return None, new_args
        return None, None

    def digest_for_signature(self, sig: str) -> str:
        with self._lock:
            return str(self.ok_digests.get(sig) or "")

    def reset_read_coverage(self) -> None:
        """Compaction removed messages from the window — earlier reads may be
        gone, so dedup must not claim they are still in context."""
        with self._lock:
            self.read_ranges.clear()
            self.read_totals.clear()
            self.stub_serves.clear()

    def reconcile_read_coverage(self, messages: list[dict[str, Any]]) -> None:
        """Keep only read ranges whose numbered lines are still in the transcript.

        Clearing or truncating a read_file result drops the bytes but used to
        leave read_ranges unchanged, so the next read of that gap returned
        "[already in context]" and the model could not recover the middle.
        """
        calls: dict[str, str] = {}
        for msg in messages or []:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if str((fn or {}).get("name") or "") != "read_file":
                    continue
                raw = (fn or {}).get("arguments") or {}
                args: dict[str, Any] = raw if isinstance(raw, dict) else {}
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = None
                    args = parsed if isinstance(parsed, dict) else {}
                path = _range_path_key(args)
                cid = str(tc.get("id") or "")
                if path and cid:
                    calls[cid] = path

        ranges: dict[str, list[tuple[int, int]]] = {}
        totals: dict[str, int] = {}
        for msg in messages or []:
            if msg.get("role") != "tool" or str(msg.get("name") or "") != "read_file":
                continue
            path = calls.get(str(msg.get("tool_call_id") or ""))
            if not path:
                continue
            text = str(msg.get("content") or "")
            span = _visible_read_span(text)
            if span is None:
                continue
            ranges.setdefault(path, []).append(span)
            meta = _RANGE_META_RE.search(text)
            if meta and span[1] >= int(meta.group(2)):
                totals[path] = int(meta.group(3))

        with self._lock:
            merged_all: dict[str, list[tuple[int, int]]] = {}
            for path, items in ranges.items():
                items.sort()
                merged: list[tuple[int, int]] = []
                for start, end in items:
                    if merged and start <= merged[-1][1] + 1:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                merged_all[path] = merged
            self.read_ranges = merged_all
            self.read_totals = {path: total for path, total in totals.items() if path in merged_all}
            self.stub_serves.clear()

    def read_coverage_lines(self) -> list[str]:
        """Compact ledger of files/ranges already read this turn."""
        with self._lock:
            items: list[str] = []
            for path, ranges in self.read_ranges.items():
                total = self.read_totals.get(path)
                bits = ", ".join(f"{s}-{e}" for s, e in ranges)
                extra = f" of {total}" if total else ""
                items.append(f"{path}: lines {bits}{extra}")
            return items

    def _record_range(self, args: dict[str, Any], content: str) -> None:
        path = _range_path_key(args)
        if not path:
            return
        m = _RANGE_META_RE.search(content or "")
        if not m:
            return
        start, end, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        self.read_totals[path] = total
        ranges = self.read_ranges.setdefault(path, [])
        ranges.append((start, end))
        ranges.sort()
        merged: list[tuple[int, int]] = []
        for s, e in ranges:
            if merged and s <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        self.read_ranges[path] = merged

    def before(self, name: str, args: dict[str, Any]) -> str | None:
        sig = _sig(name, args)
        with self._lock:
            if sig in self.blocked or self.fails.get(sig, 0) >= self.same_call_fail_limit:
                self.blocked.add(sig)
                tip = (
                    f"ERROR: blocked repeated failing call `{name}` with identical args "
                    f"({self.same_call_fail_limit}x). Change strategy — do not stop the "
                    "whole task. Use a different URL/args or another tool."
                )
                if str(name or "").startswith("browser_"):
                    tip += (
                        " For localhost connection refused: start the static/dev server "
                        "(bind 127.0.0.1), confirm the port, or open a workspace .html "
                        "by relative path; then navigate with the new target."
                    )
                return tip

            ok_n = self.ok_counts.get(sig, 0)
            # Only block after a completed success. In-flight (pending) duplicates
            # happen in parallel batches and must not surface as a fake ERROR —
            # except serial tools like browser_navigate, where two loadURLs abort.
            if name in _IDEMPOTENT_TOOLS and sig in self.pending:
                return (
                    f"ERROR: `{name}` with these arguments is already running. "
                    "Wait for that result; do not fire the same call again."
                )
            if (
                ok_n >= self.same_call_ok_limit
                and name not in _REREAD_OK
                and name not in _BROWSER_LIVE_TOOLS
            ):
                digest = (self.ok_digests.get(sig) or "").strip()
                tip = (
                    f"ERROR: `{name}` with these arguments already returned a result "
                    "this turn. Do NOT call it again with the same args — use the "
                    "result below (or earlier in this conversation). Change path/"
                    "query/offset only if you need something new."
                )
                if digest:
                    tip += "\n\n[prior result digest]\n" + digest
                return tip

            if (
                name in _EXPLORE_TOOLS
                and not self.explore_only
                and self.explore_streak >= self.max_explore_streak
            ):
                return (
                    f"ERROR: explore streak limit ({self.max_explore_streak} consecutive "
                    "read/search tools). Use results already in this conversation, "
                    "ask_user if a decision is still missing, or take a write/edit action."
                )

            self.pending.add(sig)
        return None

    def after(self, name: str, args: dict[str, Any], content: str) -> None:
        sig = _sig(name, args)
        failed = looks_failed(content)
        with self._lock:
            self.pending.discard(sig)
            if failed:
                self.fails[sig] = self.fails.get(sig, 0) + 1
                if self.fails[sig] >= self.same_call_fail_limit:
                    self.blocked.add(sig)
            else:
                self.fails.pop(sig, None)
                # Keep a compact digest so later "already returned" / repeat
                # recovery can put the substance back in front of the model.
                body = (content or "").strip()
                if body and not looks_failed(body):
                    clip = body[:1200]
                    if len(body) > 1200:
                        clip = clip.rstrip() + "\n…"
                    self.ok_digests[sig] = clip
                if name in _EXPLORE_TOOLS or (
                    name in _IDEMPOTENT_TOOLS and name not in _BROWSER_LIVE_TOOLS
                ):
                    if name not in _REREAD_OK:
                        self.ok_counts[sig] = self.ok_counts.get(sig, 0) + 1
                        self.ok_names[sig] = name
                elif name not in _BROWSER_LIVE_TOOLS:
                    # A write invalidates file listings. Browser observations
                    # are not counted here — the page can be read again.
                    # Keep successful delete_file digests so same_call_ok still
                    # blocks an identical re-delete after other mutations.
                    stale = [
                        sig_key
                        for sig_key, tool_name in self.ok_names.items()
                        if tool_name not in _IDEMPOTENT_TOOLS
                        and tool_name not in _REPEAT_BLOCK_MUTATIONS
                    ]
                    for sig_key in stale:
                        self.ok_counts.pop(sig_key, None)
                        self.ok_names.pop(sig_key, None)
                        self.ok_digests.pop(sig_key, None)
                    self.read_ranges.clear()
                    self.read_totals.clear()
                    self.stub_serves.clear()
                    if name in _REPEAT_BLOCK_MUTATIONS:
                        self.ok_counts[sig] = self.ok_counts.get(sig, 0) + 1
                        self.ok_names[sig] = name
                if name in _RANGE_TOOLS:
                    self._record_range(args, content)

            if name in _EXPLORE_TOOLS:
                self.explore_streak += 1
            else:
                self.explore_streak = 0

    def progress_nudge(self) -> str | None:
        """Deprecated no-op.

        Injecting a new ``role: user`` message made models treat the nudge as a
        fresh task (think → read_file → nudge → think → read_file). Blocking
        happens in ``before()`` instead.
        """
        return None
