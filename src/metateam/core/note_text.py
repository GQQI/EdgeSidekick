"""Fold repetition inside a stored note before it reaches the prompt.

A looping model can write the same line hundreds of times in one memory note,
after which the library re-injects every copy into every system prompt. That
kind of note carries no information, and the repeated text teaches the model
to repeat. Deduplication therefore belongs at the storage boundary, not in a
prompt instruction.

Two notes are compared by content, not layout: ``normalize_content_text``
drops whitespace, punctuation and width differences, so a line copied with a
different terminal punctuation mark still counts as the same line. Fenced code
is kept verbatim, because repeated-looking lines can be meaningful there.
"""

from __future__ import annotations

import re
from typing import Iterator

from .repetition import normalize_content_text

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")


def _scan(text: str) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, line)`` where kind is ``text``, ``code`` or ``fence``."""
    fence = ""
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in body.split("\n"):
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if not fence:
                fence = marker
                yield "fence", line
                continue
            if marker[0] == fence[0]:
                fence = ""
                yield "fence", line
                continue
        yield ("code" if fence else "text"), line


def _line_key(line: str) -> str:
    """Identity of a prose line: its words, or its formatting when wordless."""
    return normalize_content_text(line) or line.strip().lower()


def compact_note(text: str) -> str:
    """Keep the first copy of each line and collapse runs of blank lines."""
    out: list[str] = []
    seen: set[str] = set()
    for kind, line in _scan(text):
        if kind != "text":
            out.append(line.rstrip())
            continue
        if not line.strip():
            if out and out[-1]:
                out.append("")
            continue
        key = _line_key(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def note_has_body(text: str) -> bool:
    """True when the note says something besides headings.

    A note that only echoes ``### category / title`` repeats the header the
    library already prints, so injecting it adds nothing but noise.
    """
    for kind, line in _scan(text):
        if kind == "fence":
            continue
        body = line if kind == "code" else ("" if _HEADING_RE.match(line) else line)
        if normalize_content_text(body):
            return True
    return False
