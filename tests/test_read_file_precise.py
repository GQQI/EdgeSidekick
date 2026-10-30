from __future__ import annotations

import re

from metateam.core.config import Settings
from metateam.runtime.tool_registry import ToolRegistry
from metateam.runtime.tools.context import ToolContext
from metateam.runtime.tools.files import _DEFAULT_READ_CAP, _FINISH_IF_REMAINING, register_file_tools


def _read_file_fn(tmp_path, tool_result_cap: int | None = None):
    settings = Settings(workspace=tmp_path, demo_mode=True, api_key="")
    if tool_result_cap is not None:
        settings.tool_result_cap = tool_result_cap
    ctx = ToolContext(settings=settings, skills=[])
    reg = ToolRegistry()
    register_file_tools(reg, ctx)
    return reg.get("read_file").handler


def test_read_file_small_file_reads_whole_thing(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("a.txt")
    assert "line1" in out
    assert "line10" in out
    assert re.search(r"lines 1-10 of 10", out)
    assert "auto-capped" not in out


def test_read_file_modest_html_is_one_call(tmp_path) -> None:
    n = 468
    (tmp_path / "tsinghua.html").write_text(
        "\n".join(f"L{i}" for i in range(1, n + 1)), encoding="utf-8"
    )
    read_file = _read_file_fn(tmp_path)
    out = read_file("tsinghua.html")
    assert re.search(rf"lines 1-{n} of {n}", out)
    assert "more below" not in out
    assert f"{n}|L{n}" in out
    out2 = read_file("tsinghua.html", offset=193)
    assert re.search(rf"lines 193-{n} of {n}", out2)
    assert "more below" not in out2


def test_read_file_large_file_auto_caps_by_default(tmp_path) -> None:
    n = _FINISH_IF_REMAINING + 200
    (tmp_path / "big.txt").write_text("\n".join(f"line{i}" for i in range(1, n + 1)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("big.txt")
    m = re.search(r"lines 1-(\d+) of (\d+)", out)
    assert m
    assert int(m.group(1)) == _DEFAULT_READ_CAP
    assert int(m.group(2)) == n
    assert f"next offset {_DEFAULT_READ_CAP + 1}" in out
    assert f"line{n}" not in out


def test_read_file_explicit_limit_overrides_cap(tmp_path) -> None:
    n = _DEFAULT_READ_CAP + 50
    (tmp_path / "big2.txt").write_text("\n".join(f"line{i}" for i in range(1, n + 1)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("big2.txt", offset=1, limit=n)
    assert f"line{n}" in out
    assert re.search(rf"lines 1-{n} of {n}", out)


def test_read_file_entire_file_marker_and_eof(tmp_path) -> None:
    (tmp_path / "t.html").write_text("\n".join(f"L{i}" for i in range(1, 469)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("t.html")
    assert "lines 1-468 of 468" in out
    assert "ENTIRE file" in out
    # Tail slice ends the file → explicit end-of-file marker.
    out2 = read_file("t.html", offset=330)
    assert "lines 330-468 of 468" in out2
    assert "end of file" in out2
    # Probing past EOF must not error-loop; it says the file is fully read.
    out3 = read_file("t.html", offset=469, limit=1)
    assert out3.startswith("EOF:")
    assert "468 lines" in out3


def test_read_file_char_budget_keeps_trailer_honest(tmp_path) -> None:
    """A wide file must be split by SIZE with an accurate trailer — never
    claim ENTIRE file while the executor would have clipped the tail."""
    wide = "\n".join(("x" * 100) for _ in range(468))
    (tmp_path / "t.html").write_text(wide, encoding="utf-8")
    read_file = _read_file_fn(tmp_path, tool_result_cap=8000)
    out = read_file("t.html")
    m = re.search(r"lines 1-(\d+) of 468", out)
    assert m
    end = int(m.group(1))
    assert end < 468
    assert "ENTIRE file" not in out
    assert "chunk capped by size" in out
    assert f"next offset {end + 1}" in out
    # Result already fits the executor cap — nothing left to truncate.
    assert len(out) <= 8000
    # Follow-up continues exactly where it stopped and eventually ends cleanly.
    out2 = read_file("t.html", offset=end + 1)
    assert re.search(rf"lines {end + 1}-(\d+) of 468", out2)


def test_read_file_limit_hard_capped_at_500(tmp_path) -> None:
    n = 700
    (tmp_path / "long.txt").write_text("\n".join(f"L{i}" for i in range(1, n + 1)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("long.txt", offset=1, limit=600)
    m = re.search(r"lines 1-(\d+) of 700", out)
    assert m
    assert int(m.group(1)) <= 500
    assert "L501" not in out.split("…")[0].split("\n")[-1]


def test_read_file_offset_precise_slice(tmp_path) -> None:
    (tmp_path / "c.css").write_text("\n".join(f"L{i}" for i in range(1, 301)), encoding="utf-8")
    read_file = _read_file_fn(tmp_path)
    out = read_file("c.css", offset=110, limit=90)
    assert "110|L110" in out
    assert "199|L199" in out
    assert "L200" not in out
    assert re.search(r"lines 110-199 of 300", out)
