"""Visible-text cleanup and context dedupe (anti-repetition)."""

from __future__ import annotations

from metateam.core.textutil import split_dangling_colon, strip_dangling_colon
from metateam.runtime.context import dedupe_repeated_context


def test_split_dangling_colon_keeps_the_tail_separate() -> None:
    assert split_dangling_colon("先检查：") == ("先检查", "：")
    assert split_dangling_colon("先检查::") == ("先检查", "::")
    assert split_dangling_colon("先检查： \n") == ("先检查", "： \n")
    assert split_dangling_colon("先检查: :") == ("先检查", ": :")
    assert split_dangling_colon("先检查：页面") == ("先检查：页面", "")
    assert split_dangling_colon("") == ("", "")


def test_strip_dangling_colon_removes_lead_in_only() -> None:
    assert strip_dangling_colon("先检查页面：") == "先检查页面"
    assert strip_dangling_colon("先检查页面:  \n") == "先检查页面"
    assert strip_dangling_colon("做了三件事：") == "做了三件事"
    # A colon inside the text is meaningful and must stay.
    assert strip_dangling_colon("注意：事项") == "注意：事项"
    assert strip_dangling_colon("完成。") == "完成。"
    assert strip_dangling_colon("") == ""
    assert strip_dangling_colon("：") == ""


def test_dedupe_repeated_context_keeps_one_tool_row_per_call_id() -> None:
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "name": "read_file", "content": "same"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
        {"role": "tool", "tool_call_id": "2", "name": "read_file", "content": "same"},
    ]
    out = dedupe_repeated_context(messages)
    assert len(out) == 4
    assert out[3]["tool_call_id"] == "2"
    assert out[3]["content"].startswith("[same read_file result")
    # The persisted history is untouched; only the API payload is compacted.
    assert messages[3]["content"] == "same"


def test_dedupe_drops_repeated_tool_narration_but_keeps_calls() -> None:
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "先检查页面：",
            "tool_calls": [{"id": "1"}],
        },
        {"role": "tool", "tool_call_id": "1", "name": "read_file", "content": "a"},
        {
            "role": "assistant",
            "content": "先检查页面：",
            "tool_calls": [{"id": "2"}],
        },
        {"role": "tool", "tool_call_id": "2", "name": "read_file", "content": "b"},
    ]
    out = dedupe_repeated_context(messages)
    assert len(out) == 5
    assert out[1]["content"] == "先检查页面："
    assert out[3]["content"] == ""
    assert out[3]["tool_calls"] == [{"id": "2"}]


def test_dedupe_collapses_narration_rephrased_only_by_punctuation() -> None:
    """A retry that changes the colon is the same lead-in, not new content."""
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "先检查页面：", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "name": "read_file", "content": "a"},
        {"role": "assistant", "content": "先检查页面。", "tool_calls": [{"id": "2"}]},
    ]
    out = dedupe_repeated_context(messages)
    assert out[1]["content"] == "先检查页面："
    assert out[3]["content"] == ""


def test_dedupe_repeated_context_resets_at_each_user_turn() -> None:
    messages = [
        {"role": "user", "content": "one"},
        {"role": "tool", "name": "read_file", "content": "same"},
        {"role": "user", "content": "two"},
        {"role": "tool", "name": "read_file", "content": "same"},
    ]
    out = dedupe_repeated_context(messages)
    # The second turn's result is the fresh answer to a new request.
    assert out[3]["content"] == "same"


def test_dedupe_repeated_context_collapses_adjacent_identical_finals() -> None:
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "完成"},
        {"role": "assistant", "content": "完成"},
        {"role": "assistant", "content": "完成"},
    ]
    out = dedupe_repeated_context(messages)
    assert [m["role"] for m in out] == ["user", "assistant"]
