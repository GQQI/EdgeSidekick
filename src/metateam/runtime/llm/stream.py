"""LLM streaming / loop detection helpers."""
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

from .parse import merge_reasoning_fragment, merge_stream_fragment, coerce_tool_arg_piece

_log = get_logger("metateam.llm")
THINKING_TEMPERATURE_FLOOR = 0.6
SHORT_LOOP_MIN_SPAN = 24
SHORT_LOOP_HARD_REPS = 8
SHORT_LOOP_SOFT_REPS = 4
REASONING_MIN_SPAN = 16
REPEAT_CUT_SEC = 20.0
REASONING_REPEAT_CUT_SEC = 6.0
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
_REASONING_FIELD_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "thought",
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
    "why",
    "how",
    "stop",
    "need",
)
def _stream_timeouts(settings: Any) -> tuple[float, float]:
    idle = float(getattr(settings, "llm_idle_timeout", 0) or 0)
    total = float(getattr(settings, "llm_stream_timeout", 0) or 0)
    # Reasoning / thinking models often emit nothing for a long time before the
    # first token. Under concurrent social turns (group + DM) the provider can
    # also queue, so a short idle watchdog falsely surfaces as 思考超时.
    thinking = bool(getattr(settings, "thinking_enabled", False))
    effort = getattr(settings, "reasoning_effort", None)
    if idle > 0 and (thinking or effort):
        idle = max(idle, 180.0)
    return max(0.0, idle), max(0.0, total)

def _absorb_stream_piece(
    parts: list[str],
    piece: str,
    *,
    emitted: list[str],
    min_span: int = 80,
    max_keep: int = 2,
) -> str:
    """Merge one stream piece into ``parts``; return only the new visible delta.

    Handles providers that resend a growing *snapshot* (like MiniMax tool
    args) by using :func:`merge_reasoning_fragment`, then collapses consecutive
    identical spans so the UI never accumulates a tape-recorder loop.
    """
    if not piece:
        return ""
    prev = "".join(parts)
    merged = merge_reasoning_fragment(prev, piece)
    cleaned, _collapsed = collapse_streaming_repeats(
        merged, min_span=min_span, max_keep=max_keep
    )
    parts[:] = [cleaned] if cleaned else []
    already = emitted[0] if emitted else ""
    if cleaned == already:
        return ""
    if cleaned.startswith(already):
        delta = cleaned[len(already) :]
        emitted[:] = [cleaned]
        return delta
    # Truncated or rewritten (collapse removed a trailing repeat the UI may
    # already have shown). Do not re-append; the agent loop resets the bubble
    # when sidekick_looped fires.
    emitted[:] = [cleaned]
    return ""

def _absorb_reasoning_piece(
    parts: list[str],
    piece: str,
    *,
    emitted: list[str],
) -> str:
    """Reasoning variant: fold short repeated paragraphs, keep one copy.

    Thinking is internal, so a 20-char sentence repeated twice is far more
    likely to be a loop than legitimate prose. Visibility is also stricter:
    the bubble should show the idea once, not the provider's retries.
    """
    return _absorb_stream_piece(
        parts,
        piece,
        emitted=emitted,
        min_span=REASONING_MIN_SPAN,
        max_keep=1,
    )

def _trailing_unit_repeats(blob: str, unit_len: int) -> int:
    """How many identical copies of blob[-unit_len:] sit at the end."""
    if unit_len <= 0 or len(blob) < unit_len:
        return 0
    unit = blob[-unit_len:]
    if not unit.strip():
        return 0
    reps = 1
    while len(blob) >= unit_len * (reps + 1):
        end = -unit_len * reps
        start = -unit_len * (reps + 1)
        chunk = blob[start:end] if end != 0 else blob[start:]
        if chunk != unit:
            break
        reps += 1
    return reps

def streaming_text_short_looped(
    text: str,
    *,
    min_span: int = SHORT_LOOP_MIN_SPAN,
    min_reps: int = SHORT_LOOP_SOFT_REPS,
) -> bool:
    """Short-sentence tape signal for visible prose.

    Uses content (letters/digits only) so a re-wrapped or re-punctuated copy
    still matches. This is the *soft* probe: the caller must wait before
    cutting, because four identical short lines can be legitimate output.
    """
    return (
        repeated_tail_span(
            normalize_content_text(text), min_span=min_span, min_reps=min_reps
        )
        > 0
    )

def streaming_text_hard_looped(
    text: str, *, min_span: int = 80, min_reps: int = 3
) -> bool:
    """True when the same ~80+ char span repeats consecutively at the end."""
    blob = text or ""
    if len(blob) >= SHORT_LOOP_MIN_SPAN * SHORT_LOOP_HARD_REPS and (
        streaming_text_short_looped(
            blob, min_reps=SHORT_LOOP_HARD_REPS
        )
    ):
        return True
    if len(blob) < min_span * min_reps:
        return False
    max_unit = min(MAX_REPEAT_SPAN, len(blob) // min_reps)
    for unit_len in range(min_span, max_unit + 1):
        if _trailing_unit_repeats(blob, unit_len) >= min_reps:
            return True
    return False

def collapse_streaming_repeats(
    text: str, *, min_span: int = 80, max_keep: int = 2
) -> tuple[str, bool]:
    """Keep at most ``max_keep`` trailing copies of a repeated span.

    Returns ``(cleaned_text, did_collapse)``. Used so the UI / history never
    store endless repeated paragraphs even if the provider keeps sending them.
    Comparison ignores whitespace, so a re-wrapped paragraph is still a repeat.
    """
    cleaned, folded = fold_trailing_repeats(
        text, min_span=min_span, max_keep=max_keep, max_span=MAX_REPEAT_SPAN
    )
    # A Chinese loop may never reach the 80-char floor. Fold short spans only
    # once the tail is already an unmistakable tape (8 copies), so legitimate
    # short repeated lines still survive.
    if (
        min_span > SHORT_LOOP_MIN_SPAN
        # Bound the scan: this runs on every streamed piece, so normalizing a
        # whole long answer each time would be quadratic.
        and streaming_text_short_looped(cleaned[-6000:], min_reps=SHORT_LOOP_HARD_REPS)
    ):
        short, did_short = fold_trailing_repeats(
            cleaned,
            min_span=SHORT_LOOP_MIN_SPAN,
            max_keep=max_keep,
            max_span=MAX_REPEAT_SPAN,
        )
        cleaned = short
        folded = folded or did_short
    return cleaned, folded

def streaming_text_looped(text: str) -> bool:
    """True when the model is stuck repeating the same sentence/paragraph."""
    blob = text or ""
    if streaming_text_hard_looped(blob):
        return True
    if len(blob) < 240:
        return False
    # Scale the window with the candidate span so a ~300-char English
    # paragraph that repeats still trips the detector (a fixed 960/96
    # window only fits ~3 copies and never reached the old >=4 threshold).
    for span in (80, 96, 120, 160, 240, 320):
        if len(blob) < span * 3:
            continue
        tail = blob[-span:]
        if not tail.strip():
            continue
        window = blob[-(span * 4) :] if len(blob) > span * 4 else blob
        if window.count(tail) >= 3:
            return True
    # A sentence glued end to end does not share a fixed tail, so count()
    # on that tail stays at 1. Compare the recent sentences themselves.
    # Include ASCII "." — English reasoning ends with periods.
    recent = blob[-1600:]
    parts = [
        p.strip()
        for p in re.split(r"[。！？.!?\n]+", recent)
        if len(p.strip()) >= 12
    ]
    if len(parts) >= 6 and sum(1 for p in parts[-8:] if p == parts[-1]) >= 3:
        return True
    lines = [ln.strip() for ln in blob.splitlines() if len(ln.strip()) >= 16]
    if len(lines) >= 4 and lines[-1] == lines[-2] == lines[-3]:
        return True
    paras = [p.strip() for p in re.split(r"\n\s*\n", blob) if len(p.strip()) >= 40]
    if len(paras) >= 3 and paras[-1] == paras[-2] == paras[-3]:
        return True
    return False

class RepeatCut:
    """Cut a generation after similar text keeps repeating.

    Hard consecutive identity (same 80+ char span x3 for visible prose) aborts
    immediately. Softer "looks stuck" patterns still wait a short while so a
    brief echo does not end the task. ``looped``/``hard`` let the reasoning
    path use a shorter-span probe without changing visible-content rules.
    """

    def __init__(
        self,
        *,
        seconds: float = REPEAT_CUT_SEC,
        now=time.monotonic,
        looped: Optional[Callable[[str], bool]] = None,
        hard: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.seconds = float(seconds)
        self._now = now
        # Reasoning passes a more sensitive probe: internal text loops are
        # shorter than visible prose and should not keep streaming.
        self._looped = looped or streaming_text_looped
        self._hard = hard or streaming_text_hard_looped
        self._since: float | None = None

    def observe(self, text: str) -> bool:
        if self._hard(text):
            return True
        if not self._looped(text):
            self._since = None
            return False
        now = float(self._now())
        if self._since is None:
            self._since = now
            return False
        return (now - self._since) >= self.seconds

class _RawTail:
    """Raw streamed text used for loop detection.

    The display buffers are *folded* (repeated spans are removed before they
    reach the UI/history). Detecting a loop on the folded buffer is therefore
    impossible: it never contains the second or third copy. This keeps an
    unfold raw tail beside them, capped to a small window, and also understands
    providers that re-send a growing snapshot instead of deltas.
    """

    def __init__(self, window: int = 8000) -> None:
        self.window = int(window)
        self.text = ""
        self.total = 0
        self._head = ""

    def push(self, piece: str) -> str:
        if not piece:
            return self.text
        if self.total and len(piece) > self.total and piece.startswith(self._head):
            # Growing snapshot: the piece already contains everything.
            self.text = piece[-self.window :]
            self.total = len(piece)
            self._head = piece[:512]
            return self.text
        self.total += len(piece)
        if len(self._head) < 512:
            self._head = (self._head + piece)[:512]
        self.text = (self.text + piece)[-self.window :]
        return self.text

class StreamWatchdog:
    """Close a hung provider stream when idle or over a hard duration."""

    def __init__(
        self,
        close_fn: Callable[[], None],
        *,
        idle_sec: float,
        max_sec: float,
    ) -> None:
        self._close = close_fn
        self.idle_sec = max(0.0, float(idle_sec or 0))
        self.max_sec = max(0.0, float(max_sec or 0))
        now = time.monotonic()
        self._last = now
        self._start = now
        self._stop = threading.Event()
        self.reason = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.idle_sec <= 0 and self.max_sec <= 0:
            return
        self._thread = threading.Thread(
            target=self._run, name="llm-stall-watch", daemon=True
        )
        self._thread.start()

    def bump(self) -> None:
        self._last = time.monotonic()

    def set_tool_args_open(self, open_: bool) -> None:
        """While tool-call JSON is unfinished, do not apply normal idle/max cuts."""
        self._tool_args_open = bool(open_)
        if open_:
            self._last = time.monotonic()

    def grant_tool_arg_grace(self, extra_sec: float = 120.0) -> None:
        """Refresh activity while tool-call JSON is still unfinished."""
        self.set_tool_args_open(True)
        self._last = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)

    def _fire(self, reason: str) -> None:
        if self.reason or self._stop.is_set():
            return
        self.reason = reason
        try:
            self._close()
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.wait(0.4):
            now = time.monotonic()
            # Open tool JSON: only a long stall / absolute ceiling may close.
            # Normal 75s idle / 180s max was cutting DeepSeek mid-delegate_task
            # and leaving {_incomplete, goal, context} with no tasks[].
            if getattr(self, "_tool_args_open", False):
                if now - self._last >= 300.0:
                    self._fire("idle")
                    return
                if now - self._start >= 1200.0:
                    self._fire("max")
                    return
                continue
            if self.idle_sec > 0 and now - self._last >= self.idle_sec:
                self._fire("idle")
                return
            if self.max_sec > 0 and now - self._start >= self.max_sec:
                self._fire("max")
                return

