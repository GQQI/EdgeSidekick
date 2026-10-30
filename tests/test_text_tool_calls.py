from __future__ import annotations

import json

from metateam.runtime.text_tool_calls import (
    XmlToolStream,
    extract_text_tool_calls,
    merge_text_tool_calls,
    snapshot_xml_tool_calls,
    split_before_tool_markup,
)
from metateam.runtime.tool_registry import bind_tool_args


def test_loose_parameter_dump_becomes_delegate_task() -> None:
    raw = (
        '<parameter=tasks> [{"goal": "搜索早期历史", "context": "综述素材"}] </parameter> '
        "</parameter=topic> AI Agent 发展历史三阶段研究 </parameter> "
        "</parameter> role> orchestrator </parameter> "
        "</parameter> max_parallel> 3 </parameter>"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert "parameter" not in visible.lower()
    assert "搜索早期历史" not in visible
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "delegate_task"
    args = json.loads(calls[0]["function"]["arguments"])
    assert isinstance(args.get("tasks"), list)
    assert args["tasks"][0]["goal"] == "搜索早期历史"
    assert args.get("role") == "orchestrator"


def test_parse_qwen_function_parameter_markup() -> None:
    raw = (
        "<tool_call>\n"
        "<function=write_file>\n"
        "<parameter=path>notes/hello.md</parameter>\n"
        "<parameter=content># Hi</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert visible == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "write_file"
    assert '"path": "notes/hello.md"' in calls[0]["function"]["arguments"]


def test_parse_minimax_invoke_markup() -> None:
    raw = (
        "Let me write it.\n"
        "<minimax:tool_call>\n"
        '<invoke name="write_file">\n'
        '<parameter name="path">a.txt</parameter>\n'
        '<parameter name="content">ok</parameter>\n'
        "</invoke>\n"
        "</minimax:tool_call>"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert "Let me write it." in visible
    assert "<invoke" not in visible
    assert calls[0]["function"]["name"] == "write_file"


def test_split_hides_tool_markup() -> None:
    visible, held = split_before_tool_markup("hello <function=write_file>")
    assert visible == "hello "
    assert held.startswith("<function=")


def test_sloppy_function_tag_missing_gt_is_parsed() -> None:
    raw = "<function=skill_evidence_based_rag </function>"
    visible, calls = extract_text_tool_calls(raw)
    assert visible == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "skill_evidence_based_rag"


def test_sloppy_function_tag_many_skills_are_echo() -> None:
    raw = (
        "<function=skill_evidence_based_rag </function>"
        "<function=skill_presentation </function>"
        "<function=skill_web_research </function>"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert calls == []
    assert "function=" not in visible
    assert "skill_evidence" not in visible


def test_empty_delegate_xml_is_not_executed() -> None:
    raw = "<function=delegate_task </function>"
    visible, calls = extract_text_tool_calls(raw)
    assert calls == []
    assert "function=" not in visible


def test_dangling_goal_parameter_is_executed() -> None:
    raw = (
        "<function=delegate_task>\n"
        "<parameter=goal>Search the web for the following topics"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "delegate_task"
    assert "Search the web" in calls[0]["function"]["arguments"]
    assert visible == ""


def test_schema_echo_is_stripped_not_executed() -> None:
    raw = (
        "<tool_call> <tool_call> function=skill_evidence_based_rag "
        "<tool_call> function=skill_presentation "
        "<tool_call> function=delegate_task "
        "<tool_call> function=read_file "
        "<tool_call> function=write_file"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert calls == []
    assert "function=" not in visible
    assert "tool_call" not in visible.lower()


def test_flat_function_eq_bare_arg_tool_is_not_executed() -> None:
    # read_file requires `path` — a bare mention with zero parsed params must
    # never be executed with {} (that's the write_file "missing 2 required
    # positional arguments" bug). It should be stripped as markup, not run.
    raw = "<tool_call> function=read_file"
    visible, calls = extract_text_tool_calls(raw)
    assert calls == []
    assert "function=" not in visible


def test_flat_function_eq_no_arg_tool_is_executed() -> None:
    raw = "<tool_call> function=git_status"
    visible, calls = extract_text_tool_calls(raw)
    assert visible == ""
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "git_status"


def test_bare_write_file_mention_never_executes_with_empty_args() -> None:
    # Regression: a truncated "<function=write_file>" with no parameters yet
    # streamed in must not fire write_file(path=None, content=None).
    for raw in (
        "<function=write_file </function>",
        "<tool_call> function=write_file",
    ):
        visible, calls = extract_text_tool_calls(raw)
        assert calls == [], raw
        assert "write_file" not in visible


def test_merge_strips_markup_when_structured_calls_exist() -> None:
    msg = {
        "role": "assistant",
        "content": "<function=write_file><parameter=path>x</parameter></function>",
        "tool_calls": [{"id": "1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}],
    }
    out = merge_text_tool_calls(msg)
    assert out["tool_calls"][0]["function"]["name"] == "list_dir"
    assert "<function" not in (out.get("content") or "")


def test_snapshot_streams_partial_xml_params() -> None:
    held = (
        "<tool_call>\n"
        "<function=write_file>\n"
        "<parameter=path>a.txt</parameter>\n"
        "<parameter=content># He"
    )
    snaps = snapshot_xml_tool_calls(held)
    assert snaps[0][0] == "write_file"
    args = snaps[0][1]
    assert "a.txt" in args
    assert "# He" in args


def test_xml_tool_stream_emits_on_growth() -> None:
    stream = XmlToolStream()
    first = stream.feed("<function=codebase_find_similar>")
    assert first[0]["name"] == "codebase_find_similar"
    assert first[0]["id"].startswith(stream.prefix)
    second = stream.feed(
        "<function=codebase_find_similar><parameter=query>auth</parameter>"
    )
    assert second
    assert "auth" in second[0]["arguments"]
    assert stream.feed(
        "<function=codebase_find_similar><parameter=query>auth</parameter>"
    ) == []


def test_xml_tool_stream_ids_unique_per_turn() -> None:
    a = XmlToolStream()
    b = XmlToolStream()
    assert a.prefix != b.prefix
    id_a = a.feed("<function=read_file>")[0]["id"]
    id_b = b.feed("<function=read_file>")[0]["id"]
    assert id_a != id_b


def test_bind_tool_args_drops_unknown_kwargs() -> None:
    def handler(query: str, limit: int = 12) -> str:
        return f"{query}:{limit}"

    bound = bind_tool_args(
        handler,
        {"query": "auth", "description": "find auth", "limit": 8, "_idx": 1},
    )
    assert bound == {"query": "auth", "limit": 8}
    assert handler(**bound) == "auth:8"


def test_parse_partial_delegate_json() -> None:
    from metateam.runtime.llm import parse_tool_args

    raw = '{"goal": "Search the web for the following topics and return structured Chinese bullet po'
    args = parse_tool_args(raw)
    assert "Search the web" in str(args.get("goal") or "")
    assert args.get("_incomplete") is True


def test_parse_complete_write_file_json_not_marked_incomplete() -> None:
    from metateam.runtime.llm import parse_tool_args

    raw = '{"path": "docs/a.md", "content": "line1\\nline2"}'
    args = parse_tool_args(raw)
    assert args.get("path") == "docs/a.md"
    assert args.get("content") == "line1\nline2"
    assert "_incomplete" not in args


def test_unclosed_limit_does_not_swallow_offset() -> None:
    raw = (
        "<function=read_file>\n"
        "<parameter=path>notes/intro.tex</parameter>\n"
        "<parameter=limit>200\n"
        "<parameter=offset>\n"
        "1</parameter>\n"
        "</function>"
    )
    visible, calls = extract_text_tool_calls(raw)
    assert calls
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["path"].endswith("intro.tex")
    assert args["limit"] in (200, "200")
    assert args["offset"] in (1, "1")
    from metateam.runtime.tool_registry import prepare_tool_args

    def read_file(path: str, offset: int = 1, limit: int = 0) -> str:
        return f"{path}:{offset}:{limit}"

    bound = prepare_tool_args("read_file", read_file, args)
    assert bound["limit"] == 200
    assert bound["offset"] == 1
    assert read_file(**bound) == "notes/intro.tex:1:200"


def test_search_text_pattern_alias_maps_to_query() -> None:
    from metateam.runtime.tool_registry import prepare_tool_args

    def search_text(query: str, path: str = ".") -> str:
        return f"{query}@{path}"

    bound = prepare_tool_args(
        "search_text",
        search_text,
        {"pattern": "Proposition|prop:", "path": "sections/method.tex"},
    )
    assert bound["query"] == "Proposition|prop:"
    assert search_text(**bound) == "Proposition|prop:@sections/method.tex"
