"""Show only the part of a round's thinking that is actually new.

Reasoning models often reopen every tool round with the same status sentence
("Let me continue downloading the remaining images…"). Each round is a
separate completion, so the model keeps producing it; the loop cannot prevent
that, but it can avoid painting the same thought again and again.

The gate holds a round's reasoning until it says something the previous round
did not. A pure restatement is dropped; once the text diverges, only the part
after the shared prefix is released. Equality ignores layout (whitespace,
punctuation, emoji), so a re-wrapped or re-punctuated copy still matches, and
no language-specific character list is involved.
"""

from __future__ import annotations

from ..core.repetition import normalize_content_text

# How far to walk back from the divergence point to avoid releasing half a
# word. Layout only: the nearest space inside the new text, never into the
# shared prefix (the caller supplies that floor).
_BOUNDARY_LOOKBACK = 24


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    i = 0
    while i < limit and left[i] == right[i]:
        i += 1
    return i


def _release_start(pending: str, cut: int, floor: int) -> int:
    """First raw index to show: ``cut``, moved back to a token boundary.

    ``floor`` is the start of the piece that produced the divergence, so the
    backoff never re-shows text that was already held as shared prefix.
    """
    lower = max(floor, cut - _BOUNDARY_LOOKBACK)
    for pos in range(cut - 1, lower - 1, -1):
        if pending[pos].isspace():
            return pos + 1
    return cut


class ReasoningRoundGate:
    """Forward only the new part of one round's reasoning."""

    def __init__(self, previous: str = "") -> None:
        self._previous = normalize_content_text(previous)
        self._pending = ""
        self._pending_norm = ""
        # Raw index of each normalized character, so the divergence point can
        # be mapped back to readable text.
        self._norm_raw: list[int] = []
        self._released = not self._previous

    @property
    def suppressed(self) -> bool:
        """True when the round never said anything beyond the previous one."""
        return not self._released

    def observe(self, piece: str) -> str:
        """Return text to show now (``""`` while the round adds nothing)."""
        if not piece:
            return ""
        if self._released:
            return piece
        start = len(self._pending)
        self._pending += piece
        fresh: list[str] = []
        for offset, ch in enumerate(piece):
            norm = normalize_content_text(ch)
            if not norm:
                continue
            fresh.append(norm)
            self._norm_raw.extend([start + offset] * len(norm))
        if fresh:
            self._pending_norm += "".join(fresh)
        if self._previous.startswith(self._pending_norm):
            # Still a prefix of the previous thought: it adds nothing yet.
            return ""
        shared = _common_prefix_len(self._previous, self._pending_norm)
        cut = self._norm_raw[shared] if shared < len(self._norm_raw) else len(self._pending)
        cut = min(max(cut, start), len(self._pending))
        visible_start = _release_start(self._pending, cut, start)
        self._released = True
        released, self._pending = self._pending[visible_start:], ""
        self._pending_norm = ""
        self._norm_raw = []
        return released

    def flush(self) -> str:
        """Finish the round. A full restatement yields nothing."""
        self._pending = ""
        self._pending_norm = ""
        self._norm_raw = []
        return ""


def dedupe_reasoning_round(previous: str, current: str) -> str:
    """Whole-block variant for non-streaming responses and stored messages."""
    gate = ReasoningRoundGate(previous)
    released = gate.observe(str(current or "").strip())
    if not released:
        return ""
    return released + gate.flush()
