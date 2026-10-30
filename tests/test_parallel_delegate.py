"""Regression: multiple separate delegate_task tool_calls must run concurrently.

Some models emit N separate delegate_task tool_calls in one assistant turn
(one per worker) instead of a single call with tasks=[...]. Before this fix,
delegate_task was registered with parallel_safe=False, so plan_parallel_batches
isolated each call into its own batch and _execute_tools ran them strictly
one-at-a-time — the opposite of what the user asked for when requesting
"run 3 agents in parallel".
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from metateam.core.config import Settings
from metateam.core.guardrails import Guardrails
from metateam.runtime.agent_execute import AgentExecuteMixin
from metateam.runtime.approval import ApprovalGate
from metateam.runtime.tool_registry import Tool, ToolRegistry, plan_parallel_batches


class _FakeAgent(AgentExecuteMixin):
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.settings = Settings()
        self.settings.tool_result_cap = 100_000
        self.approval = ApprovalGate()
        self.guard = Guardrails()
        self.is_subagent = False
        self.goal = ""
        self.role = "leaf"
        self._allow_mutating_tools = True
        self._turn_mutated = False
        self._turn_verified = False
        self._delegate_slots_remaining = self.settings.max_concurrent_children
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _emit(self, type_: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((type_, data or {}))

    def cancelled(self) -> bool:
        return False

    def _ingest_workspace_fact(self, name: str, args: dict[str, Any], content: str) -> None:
        return None


def _tc(name: str, call_id: str) -> dict[str, Any]:
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


def test_delegate_task_tool_is_registered_parallel_safe() -> None:
    """The real registration must opt delegate_task into parallel batching."""
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    settings = Settings()
    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(settings=settings, skills=[], run_child=lambda **kw: "ok"),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    assert tool.parallel_safe is True


def test_browser_compatibility_tools_are_registered() -> None:
    from metateam.runtime.tools.browser import register_browser_tools
    from metateam.runtime.tools.context import ToolContext

    reg = ToolRegistry()
    register_browser_tools(reg, ToolContext(settings=Settings(), skills=[]))
    assert reg.get("browser_snapshot") is not None
    assert reg.get("browser_get_page_content") is not None


def test_three_separate_delegate_task_calls_batch_together() -> None:
    reg = ToolRegistry()
    reg.register(Tool("delegate_task", "d", {}, lambda: "ok", parallel_safe=True))
    reg.register(Tool("write_file", "d", {}, lambda: "ok", parallel_safe=False))

    calls = [
        _tc("delegate_task", "d1"),
        _tc("delegate_task", "d2"),
        _tc("delegate_task", "d3"),
    ]
    batches = plan_parallel_batches(calls, reg)
    assert len(batches) == 1
    assert [c["id"] for c in batches[0]] == ["d1", "d2", "d3"]

    # A mutating call in between still isolates the groups around it.
    mixed = [
        _tc("delegate_task", "d1"),
        _tc("write_file", "w1"),
        _tc("delegate_task", "d2"),
        _tc("delegate_task", "d3"),
    ]
    batches2 = plan_parallel_batches(mixed, reg)
    assert [[c["id"] for c in b] for b in batches2] == [["d1"], ["w1"], ["d2", "d3"]]


def test_delegate_task_allows_more_than_max_children() -> None:
    import json

    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    seen: list[str] = []

    def run_child(*, goal: str, context: str = "", role: str = "leaf", **_kw: Any) -> str:
        seen.append(goal)
        return f"ok:{goal}"

    settings = Settings()
    settings.max_concurrent_children = 3
    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(settings=settings, skills=[], run_child=run_child),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    out = tool.handler(tasks=[{"goal": f"t{i}"} for i in range(4)])
    assert not str(out).startswith("ERROR")
    payload = json.loads(out)
    assert len(payload) == 4
    assert sorted(seen) == ["t0", "t1", "t2", "t3"]


def test_multiple_delegate_task_calls_actually_overlap_in_time() -> None:
    starts: list[float] = []
    ends: list[float] = []
    gap = 0.15
    lock = threading.Lock()

    def slow_delegate() -> str:
        with lock:
            starts.append(time.monotonic())
        time.sleep(gap)
        with lock:
            ends.append(time.monotonic())
        return "ok"

    reg = ToolRegistry()
    reg.register(
        Tool("delegate_task", "d", {"type": "object", "properties": {}}, slow_delegate, parallel_safe=True)
    )

    agent = _FakeAgent(reg)
    tool_calls = [_tc("delegate_task", f"c{i}") for i in range(3)]

    t0 = time.monotonic()
    results = agent._execute_tools(tool_calls)
    elapsed = time.monotonic() - t0

    assert len(results) == 3
    assert all(r["content"] == "ok" for r in results)
    # Serialized execution would take ~3x gap; true concurrency keeps it near 1x.
    assert elapsed < gap * 2.2, f"expected overlap, took {elapsed:.3f}s for 3x{gap}s calls"
    assert len(starts) == 3
    # The third call must have started before the first one finished.
    assert max(starts) < min(ends) + 0.05


def test_salvage_tasks_array_from_truncated_delegate_payload() -> None:
    from metateam.runtime.agent_execute import _tool_arg_error
    from metateam.runtime.llm import parse_tool_args, salvage_tasks_array
    from metateam.runtime.tools.delegate import coerce_delegate_task_args

    raw = (
        '{"goal": "审查前端", "why": "三路并行", "tasks": ['
        '{"goal": "审查前端 JS"}, {"goal": "审查后端 API"}, {"goal": "审查部署'
    )
    salvaged = salvage_tasks_array(raw)
    assert salvaged is not None
    assert [row["goal"] for row in salvaged] == ["审查前端 JS", "审查后端 API"]

    args = parse_tool_args(raw)
    assert args.get("_incomplete") is True
    assert [row["goal"] for row in args["tasks"]] == ["审查前端 JS", "审查后端 API"]
    # No hard ERROR — coerce and run the workers that arrived.
    class _Tool:
        @staticmethod
        def handler(tasks=None, share=None, redact=None):
            return "ok"

    assert _tool_arg_error("delegate_task", _Tool(), args) is None
    coerced = coerce_delegate_task_args(args)
    assert "_incomplete" not in coerced
    assert len(coerced["tasks"]) == 2


def test_coerce_delegate_task_args_passes_tasks_through() -> None:
    from metateam.runtime.tools.delegate import coerce_delegate_task_args

    # Truncated top-level goal → one runnable task; clear incomplete.
    fixed = coerce_delegate_task_args(
        {
            "_incomplete": True,
            "goal": "审查后端 server.py 与部署配置的 bug",
            "context": "找真实缺陷",
            "role": "leaf",
        }
    )
    assert "_incomplete" not in fixed
    assert "goal" not in fixed
    assert len(fixed["tasks"]) == 1
    assert fixed["tasks"][0] == {
        "goal": "审查后端 server.py 与部署配置的 bug",
        "context": "找真实缺陷",
    }

    # Model's tasks[] is authoritative — do not rewrite or split.
    crossed = coerce_delegate_task_args(
        {
            "tasks": [
                {
                    "goal": "审计后端 bug：tsinghua_site/api/server.py",
                    "context": "读 tsinghua_site/index.html 与 static/",
                    "role": "leaf",
                }
            ]
        }
    )
    assert len(crossed["tasks"]) == 1
    assert crossed["tasks"][0]["goal"].startswith("审计后端")

    clean = coerce_delegate_task_args(
        {
            "tasks": [
                {"goal": "审计前端", "context": "读 html/js"},
                {"goal": "审计后端", "context": "读 server.py"},
                {"goal": "审计部署", "context": "读 Dockerfile"},
            ]
        }
    )
    assert list(clean.keys()) == ["tasks"]
    assert [t["goal"] for t in clean["tasks"]] == ["审计前端", "审计后端", "审计部署"]


def test_merge_core_into_system_refreshes_delegation_rules() -> None:
    from metateam.runtime.prompts import CORE, merge_core_into_system

    stale = (
        "# Reply language\nThis turn is Simplified Chinese.\n\n"
        "You are EdgeSidekick — OLD DELEGATION RULES WITHOUT N-AGENT FIX.\n\n"
        "## Host environment (CRITICAL)\nOS: Windows\n\n"
        "WORKSPACE: C:/tmp\n\n"
        "## Memory\nkeep me\n"
    )
    merged = merge_core_into_system(stale)
    assert "OLD DELEGATION RULES" not in merged
    assert "Wire format" in merged or "tasks" in merged
    assert "catch-all" in merged.lower() or "DISTINCT" in merged
    assert "## Host environment (CRITICAL)" in merged
    assert "## Memory\nkeep me" in merged
    assert merged.startswith("# Reply language\n")
    assert CORE.splitlines()[0] in merged


def test_normalize_delegate_tasks_parses_json_string() -> None:
    """DeepSeek emits tasks as a JSON string; must expand to N goals."""
    from metateam.runtime.tools.delegate import normalize_delegate_tasks

    raw = '[{"goal":"调查 A"},{"goal":"调查 B"},{"goal":"调查 C"}]'
    items = normalize_delegate_tasks(raw)
    assert items is not None
    assert len(items) == 3
    assert [row["goal"] for row in items] == ["调查 A", "调查 B", "调查 C"]

    assert normalize_delegate_tasks({"goal": "单任务"}) == [{"goal": "单任务"}]
    assert normalize_delegate_tasks("plain goal text") == [{"goal": "plain goal text"}]
    nested = normalize_delegate_tasks(['{"goal":"嵌套"}', {"goal": "对象"}])
    assert nested is not None
    assert [row["goal"] for row in nested] == ["嵌套", "对象"]


def test_delegate_task_promotes_top_level_goal_to_one_worker() -> None:
    """Truncated DeepSeek payloads with only goal must still spawn, not ERROR-loop."""
    from metateam.runtime.tool_registry import prepare_tool_args
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    started: list[str] = []

    def run_child(*, goal: str, **_kw: Any) -> str:
        started.append(goal)
        return f"ok:{goal}"

    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(settings=Settings(), skills=[], run_child=run_child),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    prepared = prepare_tool_args(
        "delegate_task", tool.handler, {"goal": "审查前端代码"}
    )
    out = tool.handler(**prepared)
    assert not str(out).startswith("ERROR:")
    assert started == ["审查前端代码"]


def test_delegate_task_json_string_tasks_spawns_all_children() -> None:
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    goals: list[str] = []

    def run_child(*, goal: str, context: str = "", role: str = "leaf", **_kw: Any) -> str:
        goals.append(goal)
        return f"ok:{goal}"

    noted: list[list[dict[str, Any]]] = []

    settings = Settings()
    settings.max_concurrent_children = 4
    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(
            settings=settings,
            skills=[],
            run_child=run_child,
            note_canvas_tasks=lambda items: noted.append(list(items)),
        ),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    payload = json.dumps(
        [{"goal": "alpha"}, {"goal": "beta"}, {"goal": "gamma"}],
        ensure_ascii=False,
    )
    tool.handler(tasks=payload)
    assert goals == ["alpha", "beta", "gamma"]
    assert len(noted) == 1
    assert [row["goal"] for row in noted[0]] == ["alpha", "beta", "gamma"]


def test_delegate_task_passes_the_completion_contract_to_each_worker() -> None:
    """The worker's goal judge must see what it was asked to deliver + stop on."""
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    captured: list[dict[str, Any]] = []

    def run_child(*, goal: str, context: str = "", role: str = "leaf", **kw: Any) -> str:
        captured.append({"goal": goal, **kw})
        return f"ok:{goal}"

    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(settings=Settings(), skills=[], run_child=run_child),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    tool.handler(tasks=[{"goal": "审计 server.py"}])

    assert len(captured) == 1
    row = captured[0]
    assert row["goal"] == "审计 server.py"
    assert "goal satisfied" in str(row.get("stop_when") or "")
    assert row.get("deliverable") == ["findings", "recommendation", "open_questions"]


def test_delegate_task_notes_all_canvas_slots_before_running() -> None:
    from metateam.runtime.tools.context import ToolContext
    from metateam.runtime.tools.delegate import register_ask_and_delegate

    noted: list[list[dict[str, Any]]] = []

    def run_child(*, goal: str, context: str = "", role: str = "leaf", **_kw: Any) -> str:
        return f"ok:{goal}"

    def note_canvas_tasks(items: list[dict[str, Any]]) -> None:
        noted.append(list(items))

    settings = Settings()
    settings.max_concurrent_children = 3
    reg = ToolRegistry()
    register_ask_and_delegate(
        reg,
        ToolContext(
            settings=settings,
            skills=[],
            run_child=run_child,
            note_canvas_tasks=note_canvas_tasks,
        ),
    )
    tool = reg.get("delegate_task")
    assert tool is not None
    tool.handler(tasks=[{"goal": f"t{i}"} for i in range(5)])
    assert len(noted) == 1
    assert [row["goal"] for row in noted[0]] == [f"t{i}" for i in range(5)]
    assert len({row["child_id"] for row in noted[0]}) == 5

