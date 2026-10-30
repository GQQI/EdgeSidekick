from __future__ import annotations

import pytest

from metateam.core.textcodec import decode_bytes, encode_text


def test_decode_prefers_utf8() -> None:
    raw = "你好 Sidekick".encode("utf-8")
    hit = decode_bytes(raw)
    assert hit.ok
    assert hit.encoding == "utf-8"
    assert hit.text == "你好 Sidekick"
    assert not hit.warning


def test_decode_does_not_guess_other_charsets() -> None:
    raw = "中文内容".encode("gb18030")
    hit = decode_bytes(raw)
    assert not hit.ok
    assert hit.text == ""
    assert "encoding=" in hit.error


def test_decode_uses_caller_encoding() -> None:
    raw = "中文内容".encode("gb18030")
    hit = decode_bytes(raw, encoding="gb18030")
    assert hit.ok
    assert hit.text == "中文内容"
    assert hit.encoding == "gb18030"


def test_decode_honors_utf8_bom() -> None:
    hit = decode_bytes("hello".encode("utf-8-sig"))
    assert hit.ok
    assert hit.text == "hello"
    assert hit.encoding == "utf-8-sig"


def test_unknown_encoding_is_error() -> None:
    hit = decode_bytes(b"abc", encoding="not-a-codec")
    assert not hit.ok
    assert hit.text == ""
    with pytest.raises(LookupError):
        encode_text("hello", "not-a-codec")


def test_encode_named_codec() -> None:
    assert encode_text("hello", "utf-8") == b"hello"
    assert encode_text("中文", "gb18030") == "中文".encode("gb18030")
