"""Surgical text edits (exact substring replace)."""

from __future__ import annotations


def apply_str_replace(
    text: str,
    old: str,
    new: str,
    *,
    replace_all: bool = False,
) -> tuple[str, int]:
    """Replace ``old`` with ``new`` in ``text``.

    Raises ``ValueError`` when ``old`` is empty, missing, or matches more than
    once without ``replace_all``.
    """
    if not old:
        raise ValueError("old_string is empty")
    haystack, needle = _align_newlines(text, old)
    n = haystack.count(needle)
    if n == 0:
        raise ValueError("old_string not found in file")
    if n > 1 and not replace_all:
        raise ValueError(
            f"old_string found {n} times; pass replace_all=true or include more context"
        )
    if replace_all:
        return haystack.replace(needle, new), n
    return haystack.replace(needle, new, 1), 1


def _align_newlines(text: str, old: str) -> tuple[str, str]:
    """Prefer an exact match; otherwise retry with CR/LF normalized to the file."""
    if old in text:
        return text, old
    if "\r\n" in text and "\r\n" not in old:
        crlf = old.replace("\n", "\r\n")
        if crlf in text:
            return text, crlf
    if "\r\n" not in text and "\r\n" in old:
        lf = old.replace("\r\n", "\n")
        if lf in text:
            return text, lf
    return text, old
