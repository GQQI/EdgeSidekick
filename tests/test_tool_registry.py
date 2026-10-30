from __future__ import annotations

from metateam.runtime.tool_registry import (
    Tool,
    ToolRegistry,
    missing_required_args,
    plan_parallel_batches,
    prepare_tool_args,
    skill_tool_name,
)


def _tc(name: str) -> dict:
    return {"function": {"name": name}, "id": name}


def test_skill_tool_name_normalizes() -> None:
    assert skill_tool_name("My Skill!") == "skill_my_skill"
    assert skill_tool_name("foo--bar") == "skill_foo_bar"
    assert skill_tool_name("___") == "skill_"


def test_plan_parallel_batches_groups_safe() -> None:
    reg = ToolRegistry()
    reg.register(Tool("read_file", "d", {}, lambda: "ok", parallel_safe=True))
    reg.register(Tool("list_dir", "d", {}, lambda: "ok", parallel_safe=True))
    reg.register(Tool("write_file", "d", {}, lambda: "ok", parallel_safe=False))
    batches = plan_parallel_batches(
        [_tc("read_file"), _tc("list_dir"), _tc("write_file"), _tc("read_file")],
        reg,
    )
    assert len(batches) == 3
    assert [c["id"] for c in batches[0]] == ["read_file", "list_dir"]
    assert [c["id"] for c in batches[1]] == ["write_file"]
    assert [c["id"] for c in batches[2]] == ["read_file"]


def test_write_file_aliases_and_nested_payload() -> None:
    def write_file(path: str, content: str, force_create: bool = False) -> str:
        return f"{path}:{content}:{force_create}"

    nested = prepare_tool_args(
        "write_file",
        write_file,
        {"file": {"file_path": "docs/a.md", "contents": "hello"}},
    )
    assert nested["path"] == "docs/a.md"
    assert nested["content"] == "hello"
    assert write_file(**nested) == "docs/a.md:hello:False"
    deep = prepare_tool_args(
        "write_file",
        write_file,
        {"params": {"file": {"file_path": "docs/b.md", "contents": "world"}}},
    )
    assert deep["path"] == "docs/b.md"
    assert deep["content"] == "world"
    assert missing_required_args(write_file, {"path": "a.md"}) == ["content"]
    assert missing_required_args(write_file, {"path": "a.md", "content": ""}) == []


def test_encoding_alias_from_charset() -> None:
    def read_file(path: str, encoding: str = "") -> str:
        return f"{path}:{encoding}"

    bound = prepare_tool_args(
        "read_file",
        read_file,
        {"path": "a.md", "charset": "gb18030"},
    )
    assert bound["path"] == "a.md"
    assert bound["encoding"] == "gb18030"


def test_write_file_arg_error_rejects_incomplete_and_missing() -> None:
    from metateam.runtime.agent_execute import _tool_arg_error

    def write_file(path: str, content: str, force_create: bool = False) -> str:
        return "wrote"

    tool = Tool("write_file", "d", {}, write_file)
    cut_off = _tool_arg_error(
        "write_file",
        tool,
        {"path": "a.md", "content": "partial", "_incomplete": True},
    )
    assert cut_off is not None and cut_off.startswith("ERROR")
    assert "cut off" in cut_off
    missing = _tool_arg_error("write_file", tool, {"path": "a.md"})
    assert missing is not None and "content" in missing
    ok = _tool_arg_error("write_file", tool, {"path": "a.md", "content": "full"})
    assert ok is None


def test_file_blocker_annotation_and_detection() -> None:
    from metateam.runtime.agent_execute import (
        _annotate_file_blocker,
        file_tool_results_blocked,
    )

    noted = _annotate_file_blocker("read_file", "ERROR: not found: a.md")
    assert "Do not continue as if this succeeded" in noted
    assert "complete payload" not in noted
    deleted = _annotate_file_blocker("delete_file", "ERROR: not found: nested/item.json")
    assert "complete payload" not in deleted
    assert "already gone" in deleted
    cut_off = _annotate_file_blocker(
        "write_file",
        "ERROR: write_file arguments were cut off before the payload was complete.",
    )
    assert "complete payload" in cut_off
    assert file_tool_results_blocked(
        [{"name": "read_file", "content": "WARNING: decoded as gb18030\nbody"}]
    )
    assert not file_tool_results_blocked(
        [{"name": "web_search", "content": "ERROR: network"}]
    )


def test_schemas_hide_mutating_when_disallowed() -> None:
    reg = ToolRegistry()
    reg.register(Tool("read_file", "d", {"type": "object"}, lambda: "ok"))
    reg.register(
        Tool("write_file", "d", {"type": "object"}, lambda: "ok", requires_approval=True)
    )
    names = {s["function"]["name"] for s in reg.schemas(allow_mutating=False)}
    assert names == {"read_file"}


def test_ask_user_fills_missing_options_from_aliases_or_prose() -> None:
    def ask_user(question: str, options: list[str] | None = None) -> str:
        return f"{question}:{len(options or [])}"

    from metateam.runtime.agent_execute import _tool_arg_error

    tool = Tool("ask_user", "d", {}, ask_user)
    # Exact reproduction of the failing call shape: packet fields, no options.
    args = {
        "allow_custom": True,
        "custom_label": "其他",
        "how": "options",
        "question": "要继续吗？",
        "stop": "选一个",
        "what": "decision",
        "why": "需要确认",
    }
    assert _tool_arg_error("ask_user", tool, args) is None
    bound = prepare_tool_args("ask_user", ask_user, args)
    assert isinstance(bound.get("options"), list)
    assert len(bound["options"]) >= 2

    from_choices = prepare_tool_args(
        "ask_user",
        ask_user,
        {"question": "选一个", "choices": ["A 方案", "B 方案"]},
    )
    assert from_choices["options"] == ["A 方案", "B 方案"]

    from_prose = prepare_tool_args(
        "ask_user",
        ask_user,
        {
            "question": "请选择下一步：\n1. 继续实现\n2. 先写测试",
            "how": "options",
        },
    )
    assert from_prose["options"] == ["继续实现", "先写测试"]
