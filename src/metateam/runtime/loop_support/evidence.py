"""A turn-scoped record of what the tools actually returned.

The goal judge reads tool evidence from ``messages``. That fails by design
whenever context management clears payloads: ``squeeze_fresh_tool_results``
and compaction replace a browser/read result with
``[cleared tool result: …]``. The judge then sees no verification evidence and
answers ``continue`` for work that is already finished, so the agent keeps
going (and repeats itself).

This ledger is appended at execution time, before any squeezing. The judge
still reads the live transcript for freshness, but a cleared row can be
replaced by the substance the ledger kept.
"""

from __future__ import annotations

from collections import deque

DEFAULT_ENTRY_CHARS = 1200
DEFAULT_MAX_ENTRIES = 80


def _clip(text: str, limit: int) -> str:
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


class EvidenceLedger:
    """Bounded, append-only tool-result log for one user turn.

    ``mark()`` returns a monotonic sequence number, so callers can scope the
    view to one loop (or one plan) without depending on list indexes that
    context management rewrites.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        entry_chars: int = DEFAULT_ENTRY_CHARS,
    ) -> None:
        self._entries: deque[tuple[int, str, str]] = deque(
            maxlen=max(1, int(max_entries))
        )
        self._entry_chars = max(80, int(entry_chars))
        self._seq = 0

    def reset(self) -> None:
        self._entries.clear()
        self._seq = 0

    def mark(self) -> int:
        """Sequence number to pass to a later ``rows(start=…)``."""
        return self._seq

    def count(self) -> int:
        """Total results recorded this turn (monotonic, survives trimming)."""
        return self._seq

    def record(self, name: str, content: str) -> None:
        body = _clip(content, self._entry_chars)
        if not body:
            return
        self._seq += 1
        self._entries.append((self._seq, str(name or "tool"), body))

    def rows(self, *, start: int = 0) -> list[tuple[str, str]]:
        """``(name, content)`` for entries recorded after ``start``."""
        floor = int(start or 0)
        return [(name, body) for seq, name, body in self._entries if seq > floor]
