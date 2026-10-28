"""Classify repeated tool calls: real progress vs. a stuck retry.

A repeated call is not automatically a loop. Polling a job, re-running a
test suite after an edit, or re-reading a page that changed all use the same
name and arguments twice in a row. The previous guard counted every adjacent
identical call for the whole turn and hard-stopped on the third one — even
when an intervening step had done real work and even when the repeated call
returned a *different* result.

This tracker escalates only on consecutive repeats whose results are also
identical, i.e. when the call demonstrably produced no new information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..core.guardrails import tool_call_signature
from .llm import parse_tool_args

_WS_RE = re.compile(r"\s+")

# Results that only say "you already received this" are not new evidence.
_STUB_PREFIXES = (
    "[already in context]",
    "[same ",
    "[cleared tool result:",
)

PROCEED = "proceed"
NUDGE = "nudge"
STOP = "stop"

_INCOMPLETE_BODY_TOOLS = frozenset({"write_file", "str_replace"})


def _function(tc: Mapping[str, Any]) -> Mapping[str, Any]:
    fn = tc.get("function") if isinstance(tc, Mapping) else None
    return fn if isinstance(fn, Mapping) else {}


def tool_call_args(tc: Mapping[str, Any]) -> dict[str, Any]:
    raw = _function(tc).get("arguments")
    args = parse_tool_args(raw if raw is not None else "{}")
    return args if isinstance(args, dict) else {}


def signature_for_tool_call(tc: Mapping[str, Any]) -> str | None:
    """Stable signature, or ``None`` when the call cannot count as a repeat.

    Truncated write/edit payloads collapse to a path-only call; retrying those
    is recovery, not a loop. They must not enter the repeat detector.
    """
    fn = _function(tc)
    name = str(fn.get("name") or "")
    if not name:
        return None
    args = tool_call_args(tc)
    if args.get("_incomplete"):
        return None
    if name in _INCOMPLETE_BODY_TOOLS:
        body = str(
            args.get("content")
            or args.get("new_string")
            or args.get("new_str")
            or ""
        ).strip()
        if not body:
            return None
    return tool_call_signature(name, args)


def call_signature_set(msg: Mapping[str, Any]) -> frozenset[str]:
    signatures = {
        signature_for_tool_call(tc)
        for tc in (msg or {}).get("tool_calls") or []
        if isinstance(tc, Mapping)
    }
    return frozenset(sig for sig in signatures if sig)


def result_is_stub(text: str) -> bool:
    """True for "already returned" placeholders, not new observations."""
    return str(text or "").lstrip().startswith(_STUB_PREFIXES)


def normalize_result(text: str) -> str:
    """Compare results ignoring line wrapping and indentation noise."""
    return _WS_RE.sub(" ", str(text or "")).strip()


def result_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    per_signature: int = 4,
) -> dict[str, list[str]]:
    """Newest-first tool results per call signature.

    Walks the transcript once, mapping each tool row back to the call that
    produced it. The tracker uses the two newest rows to tell a poll that saw
    a new page from one that re-read the same one.
    """
    calls: dict[str, str] = {}
    history: dict[str, list[str]] = {}
    for msg in messages or []:
        role = msg.get("role")
        if role == "assistant":
            calls = {}
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, Mapping):
                    continue
                sig = signature_for_tool_call(tc)
                call_id = str(tc.get("id") or "")
                if sig and call_id:
                    calls[call_id] = sig
            continue
        if role != "tool":
            continue
        sig = calls.get(str(msg.get("tool_call_id") or ""))
        if not sig:
            continue
        bucket = history.setdefault(sig, [])
        bucket.insert(0, str(msg.get("content") or ""))
        del bucket[per_signature:]
    return history


@dataclass(frozen=True)
class CallRound:
    """One assistant tool-call round and the results that followed it."""

    signatures: tuple[str, ...]
    names: tuple[str, ...]
    results: tuple[str, ...]


def call_rounds(
    messages: Sequence[Mapping[str, Any]],
    *,
    start: int = 0,
    limit: int = 40,
) -> list[CallRound]:
    """Recent completed tool rounds, oldest first.

    A round is defined by the signatures of the calls the model emitted, not
    by tool names: ``read_file(a) → write_file(a)`` is a cycle only when the
    exact same calls and observations come back.
    """
    rounds: list[CallRound] = []
    signatures: list[str] = []
    names: list[str] = []
    results: list[str] = []

    def close_round() -> None:
        nonlocal signatures, names, results
        if signatures:
            rounds.append(
                CallRound(tuple(signatures), tuple(names), tuple(results))
            )
        signatures, names, results = [], [], []

    for msg in (messages or [])[max(0, start) :]:
        role = msg.get("role")
        if role == "assistant":
            calls = [
                tc
                for tc in (msg.get("tool_calls") or [])
                if isinstance(tc, Mapping)
            ]
            if not calls:
                continue
            close_round()
            for tc in calls:
                sig = signature_for_tool_call(tc)
                if not sig:
                    continue
                signatures.append(sig)
                names.append(str(_function(tc).get("name") or ""))
            continue
        if role == "tool" and signatures:
            results.append(str(msg.get("content") or ""))
    close_round()
    return rounds[-max(1, int(limit)) :]


def stalled_tool_cycle(
    messages: Sequence[Mapping[str, Any]],
    *,
    start: int = 0,
    max_period: int = 8,
    cycles: int = 3,
    limit: int = 40,
    result_chars: int = 2000,
) -> int:
    """Period, in assistant rounds, of a stalled trailing tool cycle.

    Returns ``0`` when the tail is not a stalled cycle. A cycle is only
    reported when the exact same calls repeated ``cycles`` times **and** the
    observations were identical (after whitespace folding). A re-run after an
    edit, a poll that observed a new page, and a different file read all keep
    changing a result, so they never match.

    The period starts at one: a single round that re-issues the same calls is
    the most common loop, and its results not changing is what makes it a
    stall. Several calls inside one round remain one period.

    Browser rounds are excluded: their result strings do not describe the live
    page (a screenshot path is stable while the pixels change).
    """
    rounds = call_rounds(messages, start=start, limit=limit)
    period_count = max(1, int(cycles))
    if len(rounds) < period_count:
        return 0
    for period in range(1, max(1, int(max_period)) + 1):
        if len(rounds) < period * period_count:
            continue
        end = len(rounds)
        cycles_at = [
            rounds[end - period * (k + 1) : end - period * k]
            for k in range(period_count)
        ]
        if len(cycles_at) < 2:
            continue
        names = {name for rnd in cycles_at[0] for name in rnd.names}
        if any(name.startswith("browser_") for name in names):
            continue
        compared = 0
        changed = False
        same_call_sequence = True
        for newer, older in zip(cycles_at, cycles_at[1:]):
            for cur, ref in zip(newer, older):
                if cur.signatures != ref.signatures:
                    same_call_sequence = False
                    break
                if len(cur.results) != len(ref.results):
                    continue
                for a, b in zip(cur.results, ref.results):
                    if result_is_stub(a) or result_is_stub(b):
                        continue
                    compared += 1
                    if normalize_result(a[:result_chars]) != normalize_result(
                        b[:result_chars]
                    ):
                        changed = True
            if not same_call_sequence:
                break
        if same_call_sequence and compared and not changed:
            return period
    return 0


@dataclass(frozen=True)
class RepeatDecision:
    """What the loop should do with a repeated call."""

    action: str = PROCEED
    repeated: bool = False
    result_changed: bool = False
    stalls: int = 0

    @property
    def should_run(self) -> bool:
        return self.action == PROCEED


class ToolRepeatTracker:
    """Consecutive no-progress repeats per call set.

    ``stall_limit`` counts only repeats that returned the same result as the
    previous run. A different call set, or a changed result, resets the count,
    so legitimate re-runs never accumulate toward a hard stop.
    """

    def __init__(self, *, stall_limit: int = 3) -> None:
        self.stall_limit = max(2, int(stall_limit))
        self._last: frozenset[str] | None = None
        self._stalls = 0

    @property
    def stalls(self) -> int:
        return self._stalls

    def reset(self) -> None:
        self._last = None
        self._stalls = 0

    def observe(
        self,
        signatures: Sequence[str] | frozenset[str] | None,
        results: Mapping[str, Sequence[str]] | None = None,
    ) -> RepeatDecision:
        sigs = frozenset(signatures or ())
        if not sigs:
            self.reset()
            return RepeatDecision()
        if sigs != self._last:
            self._last = sigs
            self._stalls = 0
            return RepeatDecision()
        state = self._result_state(sigs, results or {})
        if state == "changed":
            # Same command, new observation: the world moved since the last
            # run. Let it execute instead of ending the turn.
            self._stalls = 0
            return RepeatDecision(PROCEED, repeated=True, result_changed=True)
        if state == "unknown":
            # Only one prior result exists, so we cannot tell whether this is
            # a stuck retry or a poll/re-run that will observe a change. Run
            # it once; the next repeat is classified from two results.
            return RepeatDecision(PROCEED, repeated=True)
        self._stalls += 1
        action = STOP if self._stalls >= self.stall_limit else NUDGE
        return RepeatDecision(action, repeated=True, stalls=self._stalls)

    @staticmethod
    def _result_state(
        signatures: frozenset[str],
        results: Mapping[str, Sequence[str]],
    ) -> str:
        """``changed`` / ``unknown`` (not enough history) / ``same``."""
        unknown = False
        for sig in signatures:
            rows = [str(row) for row in results.get(sig) or []]
            if not rows:
                # The call never produced a result (dropped/blocked), so there
                # is no evidence of progress to allow another attempt for.
                continue
            if len(rows) < 2:
                unknown = True
                continue
            newest, previous = rows[0], rows[1]
            if result_is_stub(newest):
                # A dedupe stub means "already in context", not fresh data.
                continue
            if normalize_result(newest) != normalize_result(previous):
                return "changed"
        return "unknown" if unknown else "same"
