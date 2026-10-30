from __future__ import annotations

import json

from metateam.runtime.llm import (
    _strip_for_api,
    canonical_tool_arguments,
    decode_tool_args_json,
    merge_stream_fragment,
    parse_tool_args,
)


def test_merge_appends_true_deltas() -> None:
    acc = merge_stream_fragment("", '{"path": "a.py", "content": "')
    acc = merge_stream_fragment(acc, "hello")
    acc = merge_stream_fragment(acc, '"}')
    assert acc == '{"path": "a.py", "content": "hello"}'
    obj, reason = decode_tool_args_json(acc)
    assert reason == "ok"
    assert obj == {"path": "a.py", "content": "hello"}


def test_merge_replaces_cumulative_snapshots() -> None:
    """MiniMax/vLLM often resend the whole arguments string each chunk."""
    acc = '{"path": "ui/src/components/ChatBubble.tsx", "content": "'
    nxt = '{"path": "ui/src/components/ChatBubble.tsx", "content": "import React from \'react\'\\n"}'
    merged = merge_stream_fragment(acc, nxt)
    assert merged == nxt
    assert '{"path"' not in merged[1:] or merged.count('{"path"') == 1


def test_merge_does_not_duplicate_function_name() -> None:
    assert merge_stream_fragment("write_file", "write_file") == "write_file"
    assert merge_stream_fragment("wri", "write_file") == "write_file"
    assert merge_stream_fragment("write_file", "write_file") != "write_filewrite_file"


def test_merge_keeps_single_brace_delta_inside_arguments() -> None:
    acc = '{"path": "login.html", "content": "*'
    acc = merge_stream_fragment(acc, "{")
    acc = merge_stream_fragment(acc, "margin:0;padding:0}")
    assert acc == '{"path": "login.html", "content": "*{margin:0;padding:0}'


def test_merge_keeps_longer_complete_snapshot() -> None:
    prev = json.dumps({"path": "a.py", "content": "hel"})
    nxt = json.dumps({"path": "a.py", "content": "hello world"})
    assert merge_stream_fragment(prev, nxt) == nxt
    assert merge_stream_fragment(nxt, prev) == nxt


def _merge_json_chunks(chunks: list[str]) -> str:
    acc = ""
    for piece in chunks:
        acc = merge_stream_fragment(acc, piece, json_payload=True)
    return acc


def test_json_nested_object_open_is_not_dropped_as_stale_snapshot() -> None:
    """The first tasks[] item opens with '{"' — a prefix of the root object.

    Treating that delta as a regressive snapshot deleted the brace before
    ``goal``, which made the whole delegate_task payload invalid and
    collapsed N workers into the first one.
    """
    raw = _merge_json_chunks(
        ['{"', "tasks", '"', ": ", "[", '{"', "goal", '": "A"', "}", ", ", '{"goal": "B"}', ']}']
    )
    assert raw == '{"tasks": [{"goal": "A"}, {"goal": "B"}]}'
    assert json.loads(raw)["tasks"] == [{"goal": "A"}, {"goal": "B"}]


def test_streamed_delegate_tasks_keep_all_workers() -> None:
    """MiniMax/DeepSeek-style deltas: every tasks[] item must survive."""
    from metateam.runtime.tools.delegate import coerce_delegate_task_args

    raw = _merge_json_chunks(
        [
            '{"',
            "tasks",
            '"',
            ": ",
            "[",
            '{"',
            "goal",
            '": "A"',
            "}",
            ", ",
            '{"goal": "B"}',
            ", ",
            '{"goal": "C"}',
            "]}",
        ]
    )
    import metateam.runtime.llm as llm_mod

    args = llm_mod.parse_tool_args(raw)
    coerced = coerce_delegate_task_args(args)
    assert [row["goal"] for row in coerced["tasks"]] == ["A", "B", "C"]


def test_parse_raw_newlines_in_write_file_not_incomplete() -> None:
    """Models often emit file bodies with real newlines instead of \\n."""
    raw = '{"path": "docs/a.md", "content": "line1\nline2"}'
    obj, reason = decode_tool_args_json(raw)
    assert reason == "control_chars"
    assert obj == {"path": "docs/a.md", "content": "line1\nline2"}
    args = parse_tool_args(raw)
    assert args.get("content") == "line1\nline2"
    assert "_incomplete" not in args
    wire = canonical_tool_arguments(raw)
    json.loads(wire)  # strict — gateway-safe
    assert json.loads(wire)["content"] == "line1\nline2"


def test_truncated_write_file_marked_incomplete_without_fake_closer() -> None:
    raw = '{"path": "ui/src/components/ChatBubble.tsx", "content": "import React from \'react\';'
    args = parse_tool_args(raw)
    assert args.get("_incomplete") is True
    assert args.get("path") == "ui/src/components/ChatBubble.tsx"
    wire = canonical_tool_arguments(raw)
    data = json.loads(wire)
    assert data.get("_incomplete") is True
    assert data.get("path") == "ui/src/components/ChatBubble.tsx"
    assert "content" not in data


def test_canonical_keeps_salvaged_delegate_tasks_on_truncate() -> None:
    """DeepSeek often closes goal first; do not strip finished tasks[] workers."""
    raw = (
        '{"goal": "审查前端", "tasks": ['
        '{"goal": "审查前端 JS"}, {"goal": "审查后端 API"}, {"goal": "审查部署'
    )
    wire = canonical_tool_arguments(raw)
    data = json.loads(wire)
    assert data.get("_incomplete") is True
    assert data.get("goal") == "审查前端"
    assert [row["goal"] for row in data["tasks"]] == ["审查前端 JS", "审查后端 API"]
    # Execution path must see the same salvaged workers (not goal-only).
    args = parse_tool_args(wire)
    assert [row["goal"] for row in args["tasks"]] == ["审查前端 JS", "审查后端 API"]


def test_strip_for_api_drops_tool_results_that_do_not_match_the_call() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_new", "name": "read_file", "content": "1|ok"},
        {"role": "tool", "tool_call_id": "call_old", "name": "run_shell", "content": "stale"},
        {
            "role": "assistant",
            "content": "again",
            "tool_calls": [
                {
                    "id": "call_new",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_new", "name": "read_file", "content": "1|ok"},
    ]
    stripped = _strip_for_api(messages)
    roles = [(m.get("role"), m.get("tool_call_id")) for m in stripped]
    assert roles == [
        ("assistant", None),
        ("tool", "call_new"),
    ]
    assert messages[2]["tool_call_id"] == "call_old"


def test_strip_for_api_omits_empty_tool_calls() -> None:
    messages = [
        {"role": "assistant", "content": "先停一下", "tool_calls": []},
        {"role": "user", "content": "继续"},
    ]
    stripped = _strip_for_api(messages)
    assert "tool_calls" not in stripped[0]
    assert stripped[0]["content"] == "先停一下"
    assert messages[0]["tool_calls"] == []


def test_strip_for_api_does_not_resend_unterminated_arguments() -> None:
    raw = '{"path": "ui/src/components/ChatBubble.tsx", "content": "import React from \'react\';'
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": raw},
                }
            ],
        }
    ]
    stripped = _strip_for_api(messages)
    sent = stripped[0]["tool_calls"][0]["function"]["arguments"]
    json.loads(sent)
    assert json.loads(sent)["_incomplete"] is True
    # Original history must stay untouched — we copy, not mutate.
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == raw


def test_concatenated_complete_snapshots_use_last() -> None:
    first = json.dumps({"path": "a.py", "content": "hel"})
    second = json.dumps({"path": "a.py", "content": "hello"})
    obj, reason = decode_tool_args_json(first + second)
    assert reason == "concatenated"
    assert obj == {"path": "a.py", "content": "hello"}
    assert "_incomplete" not in parse_tool_args(first + second)


def test_complete_then_truncated_snapshot_is_incomplete() -> None:
    first = json.dumps({"path": "a.py", "content": "hel"})
    raw = first + '{"path": "a.py", "content": "hello wor'
    args = parse_tool_args(raw)
    assert args.get("_incomplete") is True
