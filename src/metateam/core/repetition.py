"""Fold tape-recorder repetition at any span length.

Providers occasionally re-send the same paragraph for a while. The previous
helper compared *exact* 80+ char trailing spans, so a shorter Chinese
paragraph (``< 80`` chars), or the same wording with different whitespace,
was never folded — the thinking bubble painted it again and again.

The helpers here compare whitespace-insensitive blocks, which lets callers
pick the minimum span their surface can tolerate:

* reasoning/thinking: short spans are cheap to fold (the text is internal),
* visible answers: keep the conservative 80-char floor.

Both the live stream and the stored history use the same rule, so a loop can
no longer be ``collapsed`` for the UI while surviving in ``messages``.
"""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")

# A stuck model can repeat a whole long paragraph, not only a sentence. The
# window is bounded so the scan stays cheap; 4000 chars covers a long block.
MAX_REPEAT_SPAN = 4000


def normalize_repeat_text(text: str) -> str:
    """Compare block content without whitespace differences."""
    return _WS_RE.sub("", text or "")


def normalize_content_text(text: str) -> str:
    """Compare meaning, not layout: keep letters and digits only.

    Whitespace, punctuation, and emoji are formatting. Dropping them lets two
    copies of the same sentence match even when one stops at a different
    sentence boundary or switches between English and full-width punctuation,
    without enumerating any punctuation characters.
    """
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum())


def _last_significant_char(text: str) -> str:
    body = (text or "").rstrip()
    return body[-1:] if body else ""


def trailing_repeat_count(text: str, unit_len: int, *, limit: int = 8) -> int:
    """How many consecutive *equal* blocks of ``unit_len`` end ``text``.

    Equality ignores whitespace, so a paragraph re-sent with a different line
    wrap still counts as a repeat. The final char is compared first because
    that rejects almost every candidate length in non-repeating prose without
    normalizing the whole block.
    """
    blob = text or ""
    if unit_len <= 0 or len(blob) < unit_len:
        return 0
    # Cheap rejection before slicing/normalizing a whole candidate unit. The
    # caller tries hundreds of span lengths per stream piece; without this gate
    # every candidate costs O(unit_len) and a long answer becomes quadratic.
    key_len = min(8, unit_len)
    tail_key = normalize_repeat_text(blob[-key_len:])
    if not tail_key:
        tail_key = normalize_repeat_text(blob[-max(key_len, 32) :])
        key_len = max(key_len, 32)
    if not tail_key:
        return 0
    prev_end = len(blob) - unit_len
    if prev_end <= 0:
        return 1
    prev_key = normalize_repeat_text(blob[max(0, prev_end - key_len) : prev_end])
    if prev_key != tail_key:
        return 0
    unit = blob[-unit_len:]
    norm_unit = normalize_repeat_text(unit)
    if not norm_unit:
        return 0
    last = _last_significant_char(unit)
    reps = 1
    pos = prev_end
    while reps < limit and pos - unit_len >= 0:
        prev = blob[pos - unit_len : pos]
        if _last_significant_char(prev) != last:
            break
        if normalize_repeat_text(prev) != norm_unit:
            break
        reps += 1
        pos -= unit_len
    return reps


def repeated_tail_span(
    text: str,
    *,
    min_span: int = 24,
    min_reps: int = 3,
    max_span: int = MAX_REPEAT_SPAN,
) -> int:
    """Length of the largest trailing block that repeats ``min_reps`` times.

    Returns ``0`` when the tail is not periodic. Callers use this as a loop
    signal: a 24-char sentence emitted three times in a row is a stuck model,
    not content.
    """
    blob = text or ""
    upper = min(max_span, len(blob) // max(1, min_reps))
    for unit_len in range(upper, min_span - 1, -1):
        if trailing_repeat_count(blob, unit_len, limit=min_reps) >= min_reps:
            return unit_len
    return 0


def _reasoning_key(text: str) -> str:
    """Compare thought content, not its punctuation or line layout.

    A stuck reasoning stream often re-sends the same sentence with a different
    comma, full-width stop, or line wrap. Dropping everything that is not a
    letter/digit lets that copy match without enumerating any character list.
    """
    return normalize_content_text(text)


def reasoning_hard_looped(text: str, *, min_span: int = 16, min_reps: int = 3) -> bool:
    """A reasoning stream that is clearly repeating itself."""
    body = _reasoning_key(text)
    return repeated_tail_span(body, min_span=min_span, min_reps=min_reps) > 0


def reasoning_looped(text: str, *, min_span: int = 16, min_reps: int = 2) -> bool:
    """A softer repeat signal, used with a time budget before cutting."""
    body = _reasoning_key(text)
    return repeated_tail_span(body, min_span=min_span, min_reps=min_reps) > 0


def fold_trailing_repeats(
    text: str,
    *,
    min_span: int = 80,
    max_keep: int = 2,
    max_span: int = MAX_REPEAT_SPAN,
    window: int = 6000,
    rounds: int = 2,
) -> tuple[str, bool]:
    """Keep at most ``max_keep`` trailing copies of a repeated block.

    Returns ``(folded_text, did_fold)``. Detection runs on the last ``window``
    chars only, so a long conversation cannot make every streamed token scan
    the whole buffer.
    """
    blob = text or ""
    if len(blob) < min_span * (max_keep + 1):
        return blob, False
    folded = False
    for _ in range(max(1, rounds)):
        start = max(0, len(blob) - window)
        tail = blob[start:]
        cut, did = _fold_once(
            tail, min_span=min_span, max_keep=max_keep, max_span=max_span
        )
        if not did:
            break
        blob = blob[:start] + cut
        folded = True
    return blob, folded


def _fold_once(
    blob: str,
    *,
    min_span: int,
    max_keep: int,
    max_span: int,
) -> tuple[str, bool]:
    n = len(blob)
    upper = min(max_span, n // (max_keep + 1))
    for unit_len in range(upper, min_span - 1, -1):
        reps = trailing_repeat_count(blob, unit_len, limit=max_keep + 8)
        if reps > max_keep:
            return blob[: n - unit_len * (reps - max_keep)], True
    return blob, False
