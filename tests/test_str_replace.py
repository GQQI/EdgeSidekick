from __future__ import annotations

import pytest

from metateam.services.file_edit import apply_str_replace


def test_replace_once() -> None:
    out, n = apply_str_replace("a = 1\nb = 2\n", "a = 1", "a = 3")
    assert n == 1
    assert out == "a = 3\nb = 2\n"


def test_missing_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        apply_str_replace("hello", "nope", "x")


def test_empty_old_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        apply_str_replace("hello", "", "x")


def test_multiple_requires_flag() -> None:
    with pytest.raises(ValueError, match="2 times"):
        apply_str_replace("foo foo", "foo", "bar")
    out, n = apply_str_replace("foo foo", "foo", "bar", replace_all=True)
    assert n == 2
    assert out == "bar bar"


def test_crlf_alignment() -> None:
    text = "line1\r\nline2\r\n"
    out, n = apply_str_replace(text, "line1\nline2", "LINE")
    assert n == 1
    assert out == "LINE\r\n"
