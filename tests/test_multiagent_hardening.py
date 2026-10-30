"""多智能体协作加固：只读工具白名单 + 子代理最小 grounding + 编排提示词."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from metateam.core.config import Settings
from metateam.runtime.tool_registry import (
    READONLY_TOOLS,
    ToolRegistry,
    drop_delegate_schemas,
    filter_readonly_schemas,
)


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {}},
    }


def test_filter_readonly_schemas_keeps_observation_drops_mutating() -> None:
    schemas = [
        _schema("read_file"),
        _schema("search_text"),
        _schema("list_dir"),
        _schema("web_search"),
        _schema("write_file"),
        _schema("str_replace"),
        _schema("delete_file"),
        _schema("run_shell"),
        _schema("git_commit"),
        _schema("delegate_task"),
        _schema("ask_user"),
        _schema("skill_save"),
        _schema("memory_append"),
        _schema("create_scheduled_task"),
        _schema("skill_demo"),  # 动态 skill 工具一律不放行
    ]
    kept = [s["function"]["name"] for s in filter_readonly_schemas(schemas)]
    assert set(kept) == {"read_file", "search_text", "list_dir", "web_search"}


def test_filter_readonly_schemas_empty_and_malformed() -> None:
    assert filter_readonly_schemas([]) == []
    assert filter_readonly_schemas(None) == []
    assert filter_readonly_schemas([{"type": "function"}]) == []


def test_readonly_whitelist_has_no_mutating_tool() -> None:
    mutating = {
        "write_file",
        "str_replace",
        "delete_file",
        "run_shell",
        "verify_run",
        "shell_job_stop",
        "git_commit",
        "memory_append",
        "memory_remove",
        "memory_write",
        "create_scheduled_task",
        "cancel_scheduled_task",
        "skill_save",
        "delegate_task",
        "delegate_dialogue",
        "ask_agent",
        "ask_user",
        "browser_click",
        "browser_input",
        "browser_type",
    }
    assert not READONLY_TOOLS & mutating


def test_registered_registry_filters_real_tools(tmp_path: Path) -> None:
    """真实注册表走一遍过滤：文件/浏览器观察工具保留，写入/shell 工具剔除。"""
    from metateam.runtime.tools.browser import register_browser_tools
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.files import register_file_tools
    from metateam.runtime.tools.shell import register_shell_tools

    settings = Settings()
    settings.workspace = tmp_path
    reg = ToolRegistry()
    ctx = ToolContext(settings=settings, skills=[])
    register_file_tools(reg, ctx)
    register_shell_tools(reg, ctx)
    register_browser_tools(reg, ctx)

    kept = {s["function"]["name"] for s in filter_readonly_schemas(reg.schemas())}
    assert {"read_file", "list_dir", "search_text", "browser_observe"} <= kept
    assert "browser_navigate" not in kept
    assert not kept & {"write_file", "str_replace", "delete_file", "run_shell"}


def test_minimal_grounding_lists_top_level(tmp_path: Path) -> None:
    from metateam.runtime.agent_grounding import minimal_grounding

    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")

    block = minimal_grounding(tmp_path)
    assert f"WORKSPACE: {tmp_path.resolve()}" in block
    assert "dir:src" in block
    assert "file:README.md" in block
    assert ".hidden" not in block


def test_minimal_grounding_caps_top_level_at_20(tmp_path: Path) -> None:
    from metateam.runtime.agent_grounding import minimal_grounding

    for i in range(25):
        (tmp_path / f"f{i:02d}.txt").write_text("x", encoding="utf-8")

    block = minimal_grounding(tmp_path)
    listed = [ln for ln in block.splitlines() if ln.startswith("Top-level now:")]
    assert len(listed) == 1
    entries = listed[0].replace("Top-level now: ", "").split(", ")
    assert len(entries) <= 20


def test_minimal_grounding_empty_workspace(tmp_path: Path) -> None:
    from metateam.runtime.agent_grounding import minimal_grounding

    block = minimal_grounding(tmp_path)
    assert "WORKSPACE:" in block
    assert "(empty)" in block


def test_run_child_injects_minimal_grounding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """task 子代理的 context 必须带上 WORKSPACE 与顶层清单，且保留 parent context。"""
    import metateam.runtime.agent as agent_mod

    s = Settings()
    s.demo_mode = True
    s.api_key = ""
    s.workspace = tmp_path
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")

    parent = agent_mod.Agent(s)
    captured: dict = {}

    class StubChild:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)
            self.agent_id = "agent_stub"
            self.settings = s
            self.messages = []

    monkeypatch.setattr(agent_mod, "Agent", StubChild)
    monkeypatch.setattr(
        parent,
        "_wait_child_run",
        lambda child, text: SimpleNamespace(text="ok", iterations=1, cancelled=False),
    )
    monkeypatch.setattr(parent, "_ingest_canvas_subtree", lambda *a, **k: None)
    monkeypatch.setattr(parent, "_remember_canvas_child", lambda *a, **k: None)
    monkeypatch.setattr(parent, "_emit_canvas_sync", lambda: None)

    parent._run_child(goal="探路", context="只查 README")

    ctx = str(captured.get("context") or "")
    assert f"WORKSPACE: {tmp_path.resolve()}" in ctx
    assert "dir:src" in ctx and "file:README.md" in ctx
    assert "只查 README" in ctx


def test_run_child_keeps_full_agent_context_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """party/full 子代理已有 grounding 刷新，context 作为 user_text 不应被拼入清单。"""
    import metateam.runtime.agent as agent_mod

    s = Settings()
    s.demo_mode = True
    s.api_key = ""
    s.workspace = tmp_path

    parent = agent_mod.Agent(s)
    captured: dict = {}

    class StubChild:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)
            self.agent_id = "agent_stub"
            self.settings = s
            self.messages = []

    monkeypatch.setattr(agent_mod, "Agent", StubChild)
    seen_user_text: list[str] = []

    def fake_wait(child, text: str) -> SimpleNamespace:
        seen_user_text.append(text)
        return SimpleNamespace(text="ok", iterations=1, cancelled=False)

    monkeypatch.setattr(parent, "_wait_child_run", fake_wait)
    monkeypatch.setattr(parent, "_ingest_canvas_subtree", lambda *a, **k: None)
    monkeypatch.setattr(parent, "_remember_canvas_child", lambda *a, **k: None)
    monkeypatch.setattr(parent, "_emit_canvas_sync", lambda: None)

    parent._run_child(goal="扮演", context="你是谈判代表", role="leaf", kind="party")

    # full_agent 子代理的 context 走 user_text 通道（grounding 由刷新机制负责）
    ctx = str(captured.get("context") or "")
    assert ctx == ""
    assert seen_user_text and "你是谈判代表" in seen_user_text[0]


def test_disable_tools_agent_registry_yields_readonly_only(tmp_path: Path) -> None:
    """disable_tools 语义：能力降级而非静默——注册表过滤后只剩观察类工具。"""
    import metateam.runtime.agent as agent_mod

    s = Settings()
    s.demo_mode = True
    s.api_key = ""
    s.workspace = tmp_path
    agent = agent_mod.Agent(s)
    agent._disable_tools = True

    kept = {sch["function"]["name"] for sch in filter_readonly_schemas(agent.registry.schemas())}
    assert {"read_file", "search_text", "list_dir"} <= kept
    assert not kept & {
        "write_file",
        "str_replace",
        "run_shell",
        "delegate_task",
        "ask_user",
        "ask_agent",
    }


def test_drop_delegate_schemas_removes_nested_agents() -> None:
    kept = {
        item["function"]["name"]
        for item in drop_delegate_schemas(
            [
                _schema("read_file"),
                _schema("delegate_task"),
                _schema("delegate_dialogue"),
                _schema("ask_agent"),
                _schema("write_file"),
            ]
        )
    }
    assert kept == {"read_file", "write_file"}


def test_block_delegate_rejects_nested_spawn(tmp_path: Path) -> None:
    import metateam.runtime.agent as agent_mod

    settings = Settings()
    settings.demo_mode = True
    settings.api_key = ""
    settings.workspace = tmp_path
    agent = agent_mod.Agent(settings)
    agent._block_delegate = True
    result = agent._execute_one(
        {"id": "call_1", "function": {"name": "delegate_task", "arguments": "{}"}}
    )
    assert "group collaboration harness" in result["content"]


def test_social_prompt_requires_plan_first(tmp_path: Path) -> None:
    from metateam.runtime.prompts import build_social_system_prompt

    prompt = build_social_system_prompt(workspace=tmp_path)
    assert "numbered plan" in prompt
    assert "Never wait for plan confirmation" in prompt
    assert "create_scheduled_task" in prompt
    assert "Never say this feature is missing" in prompt
    assert f"WORKSPACE: {tmp_path.resolve()}" in prompt


def _build_sub_prompt(role: str, tmp_path: Path) -> str:
    from metateam.runtime.prompts import build_system_prompt

    return build_system_prompt(
        workspace=tmp_path,
        skills=[],
        memory_file=tmp_path / "MEMORY.md",
        skills_dir=None,
        is_subagent=True,
        role=role,
        goal="多流汇总",
        context="",
        depth=1,
        max_depth=2,
    )


def test_orchestrator_role_gets_orchestration_extra(tmp_path: Path) -> None:
    prompt = _build_sub_prompt("orchestrator", tmp_path)
    assert "Orchestrator worker" in prompt
    assert "Nested delegate_task is unavailable" in prompt
    assert "synthesize" in prompt.lower()


def test_leaf_role_does_not_get_orchestration_extra(tmp_path: Path) -> None:
    prompt = _build_sub_prompt("leaf", tmp_path)
    assert "Orchestrator worker" not in prompt
    assert "You are a leaf worker" in prompt


def test_core_prompt_carries_routing_and_brief_elements(tmp_path: Path) -> None:
    from metateam.runtime.prompts import build_system_prompt

    prompt = build_system_prompt(
        workspace=tmp_path,
        skills=[],
        memory_file=tmp_path / "MEMORY.md",
        skills_dir=None,
    )
    assert "Route BEFORE spawning" in prompt
    assert "0 workers" in prompt
    assert "ON DEMAND" in prompt or "on demand" in prompt.lower() or "not every task" in prompt.lower()
    assert "objective" in prompt and "boundary" in prompt


def test_delegate_task_description_carries_brief_contract() -> None:
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(settings=Settings(), skills=[], run_child=lambda **kw: "ok"),
    )
    desc = reg.get("delegate_task").description
    assert "tasks" in desc
    assert "ON DEMAND" in desc or "only when" in desc.lower()
    assert "top-level" in desc.lower() or "N workers" in desc or "short" in desc.lower()
