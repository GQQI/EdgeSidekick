"""Goal-completion verdicts: unmet work must not look like a finished turn."""

from __future__ import annotations

from metateam.runtime.prompts import apply_response_language, user_language
from metateam.runtime.goal_completion import (
    GOAL_CONTINUE_NUDGE,
    active_obligation,
    assess_goal_state,
    assess_goal_verdict,
    coalesce_internal_nudge,
    extract_named_tool_ids,
    goal_continue_nudge,
    is_control_user_message,
    looks_like_tool_intent_only,
    parse_goal_state,
    parse_goal_verdict,
    resolve_mentioned_tools,
    should_continue_for_goal,
    should_continue_for_tool_intent,
    should_run_next_plan_step,
    status_for_goal_verdict,
    tool_evidence,
    tool_intent_unavailable_message,
)


def test_parse_goal_state_json() -> None:
    assert parse_goal_state('{"state": "done", "reason": "file written"}') == "done"
    assert parse_goal_state('{"state": "continue", "reason": "form still missing"}') == "continue"
    assert parse_goal_state('{"state": "blocked", "reason": "need a choice"}') == "blocked"
    assert parse_goal_state('{"satisfied": false}') == "continue"
    assert parse_goal_state('{"satisfied": true}') == "done"
    assert parse_goal_state("not json") is None
    assert parse_goal_state("") is None


def test_parse_goal_verdict_keeps_reason() -> None:
    v = parse_goal_verdict('{"state": "continue", "reason": "narrated intent only"}')
    assert v["state"] == "continue"
    assert "narrated" in v["reason"]
    assert parse_goal_verdict("").get("state") is None


def test_status_and_nudge_surface_judge_reason() -> None:
    assert "未完成" in status_for_goal_verdict("continue", reason="还缺 Dockerfile")
    assert "Dockerfile" in status_for_goal_verdict("continue", reason="还缺 Dockerfile")
    assert "已完成" in status_for_goal_verdict("done", reason="已写入")
    assert "等待" in status_for_goal_verdict("blocked", reason="选端口")
    assert "没有带来新结果" in status_for_goal_verdict(
        "continue", reason="x", stalled_no_progress=True
    )
    nudge = goal_continue_nudge("还缺工具结果")
    assert "Judge reason:" in nudge
    assert "还缺工具结果" in nudge
    assert "ReAct" not in nudge


def test_parse_goal_state_fenced() -> None:
    raw = '```json\n{"state": "continue", "reason": "partial"}\n```'
    assert parse_goal_state(raw) == "continue"


def test_active_obligation_skips_nudges_and_keeps_plan_step() -> None:
    messages = [
        {"role": "user", "content": "把登录页改成可注册"},
        {
            "role": "user",
            "content": "[Plan step 1/2] 改标题",
            "sidekick_internal": True,
            "sidekick": {"internal": True, "kind": "plan_step"},
        },
        {
            "role": "user",
            "content": "keep going",
            "sidekick_internal": True,
            "sidekick": {"internal": True, "kind": "goal_continue"},
        },
    ]
    assert active_obligation(messages) == "[Plan step 1/2] 改标题"
    assert is_control_user_message(messages[-1]) is True


def test_tool_evidence_keeps_latest_results_and_digests_the_turn() -> None:
    messages = [
        {"role": "user", "content": "goal"},
        {"role": "tool", "name": "read_file", "content": "old"},
        {"role": "tool", "name": "write_file", "content": "wrote login.tsx"},
    ]
    text = tool_evidence(messages, start=1, limit_items=1, item_chars=80)
    # The latest row stays in full; the earlier action is still visible in the
    # per-tool digest so the judge does not think the turn did nothing.
    assert "write_file: wrote login.tsx" in text
    assert "read_file x1" in text
    assert "Recent results:" in text


def test_tool_evidence_surfaces_actions_older_than_the_recent_window() -> None:
    messages = [{"role": "user", "content": "goal"}]
    messages.append({"role": "tool", "name": "run_shell", "content": "exit 0"})
    for i in range(12):
        messages.append({"role": "tool", "name": "read_file", "content": f"line {i}"})
    text = tool_evidence(messages, start=1, limit_items=2)
    assert "run_shell x1 (last ok)" in text
    # Only the recent window gets full rows.
    assert "line 0" not in text
    assert "line 11" in text


def test_only_an_explicit_continue_verdict_reopens_the_turn() -> None:
    assert should_continue_for_goal("continue") is True
    assert should_continue_for_goal("done") is False
    assert should_continue_for_goal("blocked") is False
    # An unreadable verdict stops instead of spending another model round.
    assert should_continue_for_goal(None) is False


def test_tool_evidence_prefers_execution_time_rows() -> None:
    """Squeezing the transcript must not erase what the tools proved."""
    messages = [
        {"role": "user", "content": "verify the page"},
        {
            "role": "tool",
            "name": "browser_snapshot",
            "content": "[cleared tool result: browser_snapshot. The payload was dropped to free context.]",
        },
    ]
    text = tool_evidence(
        messages,
        start=1,
        rows=[
            (
                "browser_snapshot",
                "tabs: 0*:北京大学; elements: [1]<a href=...> 北大 · 要闻 正常显示",
            )
        ],
        limit_items=2,
    )
    assert "北大 · 要闻 正常显示" in text
    assert "[cleared tool result:" not in text


class _FakeLLM:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
        assert "Active goal" in user
        return self.raw


def test_assess_goal_state_reads_model_json() -> None:
    class _CheckPayload(_FakeLLM):
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            assert "Active goal" in user
            assert "Evidence present:" in user
            return self.raw

    state = assess_goal_state(
        _CheckPayload('{"state": "continue", "reason": "button not added"}'),
        goal="给首页加一个注册按钮",
        evidence="read_file: <h1>首页</h1>",
        candidate="我先看完了首页，下一步再改。",
    )
    assert state == "continue"


def test_response_language_follows_the_user_without_rewriting_tools() -> None:
    assert user_language("把登录页改成可注册") == "zh"
    assert user_language("Add a signup button to the login page") == "en"
    base = "You are EdgeSidekick.\n\n# Tools\nCall read_file with JSON arguments."
    zh = apply_response_language(base, "把登录页改成可注册")
    en = apply_response_language(zh, "Add a signup button")
    assert zh.startswith("# Reply language\n")
    assert "简体中文" in zh
    assert "# Tools\nCall read_file" in zh
    assert "Call read_file" in en
    assert "must both be English" in en
    # One directive per call: the tail copy was pure duplication.
    assert zh.count("简体中文") == 1
    assert "# Response language" not in zh
    assert zh.split("\n\n", 1)[1] == en.split("\n\n", 1)[1]


def test_plan_step_gate_stops_only_on_a_positive_settled_verdict() -> None:
    assert should_run_next_plan_step("continue") is True
    assert should_run_next_plan_step("done") is False
    assert should_run_next_plan_step("blocked") is False
    # A failed judge must not skip work the user already approved.
    assert should_run_next_plan_step(None) is True


def test_assess_goal_state_failure_is_unknown() -> None:
    class _Boom:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            raise RuntimeError("offline")

    assert assess_goal_state(_Boom(), goal="改首页", evidence="", candidate="好") is None


def _agent_stub(raw_verdict: str):
    from metateam.runtime.agent import Agent

    class Stub:
        def __init__(self) -> None:
            self.messages = [
                {"role": "user", "content": "把首页标题改成欢迎"},
                {"role": "tool", "name": "str_replace", "content": "updated index.html"},
            ]
            self.compress_llm = _FakeLLM(raw_verdict)
            self._disable_tools = False
            self._goal_satisfied = None
            self._response_language_src = "把首页标题改成欢迎"
            self.events: list[tuple[str, dict]] = []

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

    stub = Stub()
    return Agent, stub


def test_agent_stops_on_done_verdict() -> None:
    Agent, stub = _agent_stub('{"state": "done", "reason": "title updated"}')
    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "标题已改好。",
        loop_start=1,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep_going is False
    assert nudges == 0
    assert stub._goal_satisfied is True
    settled = [d for name, d in stub.events if name == "goal_settled"]
    assert settled and settled[-1].get("satisfied") is True
    assert settled[-1].get("blocked") is False
    assert settled[-1].get("reason") == "title updated"
    judges = [d for name, d in stub.events if name == "goal_judge"]
    assert judges and judges[-1].get("state") == "done"
    assert judges[-1].get("reason") == "title updated"
    statuses = [d for name, d in stub.events if name == "assistant_status"]
    assert statuses and "已完成" in str(statuses[-1].get("text") or "")


def _plan_stub(verdict: str):
    from types import SimpleNamespace

    class Stub:
        def __init__(self) -> None:
            self.messages = [
                {"role": "user", "content": "把首页标题改成欢迎"},
                {"role": "tool", "name": "str_replace", "content": "updated index.html"},
            ]
            self.compress_llm = _FakeLLM(verdict)
            self.settings = SimpleNamespace(max_iterations=30)
            self._goal_satisfied = True
            self._last_compressed = False
            self.guard = SimpleNamespace(begin_plan_step=lambda: None)
            self.events: list[tuple[str, dict]] = []
            self.loops = 0

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

        def cancelled(self) -> bool:
            return False

        def _run_agent_loop(self, cap: int):
            self.loops += 1
            return f"第 {self.loops} 步完成", 1, False, False

    return Stub()


def _plan_tasks() -> list[dict]:
    return [
        {"id": "t1", "title": "改标题", "status": "pending"},
        {"id": "t2", "title": "再补一个页脚", "status": "pending"},
    ]


def test_plan_stops_when_a_step_already_met_the_user_goal() -> None:
    from metateam.runtime.agent import Agent

    stub = _plan_stub('{"state": "done", "reason": "title updated"}')
    tasks = _plan_tasks()
    final, turned, cancelled, _ = Agent._execute_plan_steps(
        stub, "plan_1", tasks, {}, "计划", "把首页标题改成欢迎"
    )
    assert stub.loops == 1
    assert tasks[0]["status"] == "done"
    assert tasks[1]["status"] == "skipped"
    assert "无需执行" in final
    assert cancelled is False
    # The UI receives the skipped snapshot, not a forever-pending task.
    snapshots = [d for name, d in stub.events if name == "plan_step"]
    assert snapshots[-1]["tasks"][1]["status"] == "skipped"


def test_plan_keeps_running_while_the_judge_says_continue() -> None:
    from metateam.runtime.agent import Agent

    stub = _plan_stub('{"state": "continue", "reason": "footer missing"}')
    tasks = _plan_tasks()
    final, _, _, _ = Agent._execute_plan_steps(
        stub, "plan_1", tasks, {}, "计划", "把首页标题改成欢迎"
    )
    assert stub.loops == 2
    assert [t["status"] for t in tasks] == ["done", "done"]
    assert "无需执行" not in final


def test_agent_nudges_once_then_stops_at_the_cap() -> None:
    Agent, stub = _agent_stub('{"state": "continue", "reason": "button still missing"}')
    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "我先看完了页面。",
        loop_start=1,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep_going is True
    assert nudges == 1
    assert stub.messages[-1]["sidekick"]["kind"] == "goal_continue"
    assert stub.messages[-1]["role"] == "system"
    assert "ReAct" not in stub.messages[-1]["content"]
    assert "react" not in stub.messages[-1]["content"].lower()

    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "页面看完了。",
        loop_start=1,
        hold_for_goal=True,
        goal_nudges=2,
        emit_assistant_text=False,
    )
    assert keep_going is False
    assert stub._goal_satisfied is False


def test_looks_like_tool_intent_only_generic() -> None:
    # zh / en intent prose — must not hardcode a product/plugin name
    assert looks_like_tool_intent_only("我需要使用插件工具来搜索和读取邮件。") is True
    assert looks_like_tool_intent_only("我将调用 MCP 工具来查询最新数据。") is True
    assert (
        looks_like_tool_intent_only(
            "I need to use the plugin tool to search and read emails."
        )
        is True
    )
    assert looks_like_tool_intent_only("Let me call the tool to list files.") is True
    # plain chat / completed work must not reopen (samples, not production branches)
    assert looks_like_tool_intent_only("今天天气不错。") is False
    assert looks_like_tool_intent_only("你好！有什么可以帮你的吗？") is False
    assert looks_like_tool_intent_only("Hello! How can I help you?") is False
    assert looks_like_tool_intent_only("Thanks!") is False
    assert looks_like_tool_intent_only("谢谢") is False
    assert looks_like_tool_intent_only("最新三封邮件是：A、B、C。") is False
    assert looks_like_tool_intent_only("我已经使用工具查到了三封邮件。") is False
    assert looks_like_tool_intent_only("") is False


def test_should_continue_for_tool_intent_is_conservative() -> None:
    intent = "我需要使用插件工具来搜索邮件。"
    assert (
        should_continue_for_tool_intent(
            intent,
            tools_available=True,
            had_tool_results=False,
            nudges=0,
        )
        is True
    )
    # After a tool already ran, goal judge owns the decision.
    assert (
        should_continue_for_tool_intent(
            intent,
            tools_available=True,
            had_tool_results=True,
            nudges=0,
        )
        is False
    )
    # Empty tool schema → do not nudge.
    assert (
        should_continue_for_tool_intent(
            intent,
            tools_available=False,
            had_tool_results=False,
            nudges=0,
        )
        is False
    )
    # Cap at TOOL_INTENT_CONTINUE_LIMIT continuations.
    assert (
        should_continue_for_tool_intent(
            intent,
            tools_available=True,
            had_tool_results=False,
            nudges=1,
        )
        is True
    )
    assert (
        should_continue_for_tool_intent(
            intent,
            tools_available=True,
            had_tool_results=False,
            nudges=2,
        )
        is False
    )


def test_tool_intent_nudge_is_control_message() -> None:
    from metateam.runtime.goal_completion import GOAL_CONTINUE_NUDGE, TOOL_INTENT_NUDGE

    msg = {
        "role": "user",
        "content": TOOL_INTENT_NUDGE,
        "sidekick_internal": True,
        "sidekick": {"internal": True, "kind": "tool_intent"},
    }
    assert is_control_user_message(msg) is True
    # Continuations must not look like a user ReAct request.
    sys_msg = {
        "role": "system",
        "content": GOAL_CONTINUE_NUDGE,
        "sidekick_internal": True,
        "sidekick": {"internal": True, "kind": "goal_continue"},
    }
    assert is_control_user_message(sys_msg) is True
    assert "ReAct" not in GOAL_CONTINUE_NUDGE
    assert "ReAct" not in TOOL_INTENT_NUDGE
    assert "继续循环" not in GOAL_CONTINUE_NUDGE
    assert not TOOL_INTENT_NUDGE.startswith("继续执行")


def test_looks_like_tool_intent_names_concrete_prefixed_id() -> None:
    """Chinese/English prose that names plugin_*/mcp_* without saying 工具/插件."""
    zh = "用户让我查询数据。我需要使用plugin_demo_search_messages来搜索最新记录。"
    assert looks_like_tool_intent_only(zh) is True
    assert extract_named_tool_ids(zh) == ["plugin_demo_search_messages"]
    assert (
        should_continue_for_tool_intent(
            zh,
            tools_available=True,
            had_tool_results=False,
            nudges=0,
            known_tool_names=["plugin_demo_search_messages", "read_file"],
        )
        is True
    )
    mcp = "接下来调用mcp_files_list来列出目录。"
    assert looks_like_tool_intent_only(mcp) is True
    assert "mcp_files_list" in extract_named_tool_ids(mcp)


def test_resolve_mentioned_tools_only_returns_registry_names() -> None:
    prose = "我将使用plugin_demo_search和unknown_tool_xyz来查询。"
    assert resolve_mentioned_tools(
        prose, known_tool_names=["plugin_demo_search", "read_file"]
    ) == ["plugin_demo_search"]
    # Without a registry filter, prefixed ids are still extracted.
    assert "plugin_demo_search" in extract_named_tool_ids(prose)


def test_tool_intent_unavailable_when_named_tools_missing() -> None:
    prose = "我需要使用plugin_demo_search来搜索。"
    msg = tool_intent_unavailable_message(prose, known_tool_names=["read_file"])
    assert msg is not None
    assert "plugin_demo_search" in msg
    # Present in schema → no unavailable message
    assert (
        tool_intent_unavailable_message(
            prose, known_tool_names=["plugin_demo_search"]
        )
        is None
    )


def test_should_continue_for_known_nonprefixed_registry_name() -> None:
    """Builtin / non-prefixed registry names named in intent prose also continue."""
    prose = "我需要使用read_file来读取配置。"
    assert (
        should_continue_for_tool_intent(
            prose,
            tools_available=True,
            had_tool_results=False,
            nudges=0,
            known_tool_names=["read_file", "write_file"],
        )
        is True
    )


def test_resolve_leaf_alias_maps_short_tool_name() -> None:
    """Prose naming a manifest leaf (generate_dockerfile) resolves to plugin_*."""
    from metateam.runtime.goal_completion import (
        resolve_mentioned_tools,
        tool_leaf_aliases,
        looks_like_tool_intent_only,
        should_continue_for_tool_intent,
    )

    full = "plugin_offline_docker_generate_dockerfile"
    assert "generate_dockerfile" in tool_leaf_aliases(
        full, known_plugin_ids=["offline-docker"]
    )
    prose = "用户想要生成Dockerfile。让我调用generate_dockerfile工具。"
    assert looks_like_tool_intent_only(prose) is True
    assert resolve_mentioned_tools(
        prose,
        [full, "read_file"],
        known_plugin_ids=["offline-docker"],
    ) == [full]
    assert (
        should_continue_for_tool_intent(
            prose,
            tools_available=True,
            had_tool_results=False,
            nudges=0,
            known_tool_names=[full, "read_file"],
            plugin_summaries=[
                {
                    "id": "offline-docker",
                    "name": "离线 Docker 交付",
                    "tools": [{"name": full}],
                }
            ],
        )
        is True
    )


def test_prior_assistant_substance_skips_latest_candidate() -> None:
    from metateam.runtime.goal_completion import prior_assistant_substance

    messages = [
        {"role": "user", "content": "介绍目录"},
        {"role": "assistant", "content": "## 完整介绍\n文件A、文件B……"},
        {"role": "assistant", "content": "任务已完成，无需继续。"},
    ]
    prior = prior_assistant_substance(messages, start=0)
    assert "完整介绍" in prior
    assert "无需继续" not in prior


def test_assess_goal_state_receives_prior_replies() -> None:
    seen: list[str] = []

    class _Capture:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            seen.append(user)
            return '{"state": "done", "reason": "prior answer complete"}'

    state = assess_goal_state(
        _Capture(),
        goal="介绍目录",
        evidence="read_file: ok",
        candidate="任务已完成。",
        prior_replies="## 完整介绍\n六个文件的摘要……",
    )
    assert state == "done"
    assert "Earlier assistant replies" in seen[0]
    assert "完整介绍" in seen[0]
    assert "Evidence present: yes" in seen[0]


def test_assess_goal_state_marks_empty_evidence_explicitly() -> None:
    seen: list[tuple[str, str]] = []

    class _Capture:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            seen.append((system, user))
            return '{"state": "continue", "reason": "no evidence yet"}'

    state = assess_goal_state(
        _Capture(),
        goal="生成 Dockerfile",
        evidence="",
        candidate="让我调用generate_dockerfile工具。",
    )
    assert state == "continue"
    system, user = seen[0]
    assert "Evidence present: no" in user
    assert "zero tool results" in user
    assert "narrated intent is never completion" in system.lower()



def test_reject_early_stop_conversational_done_without_tools() -> None:
    """No-action user turn + reply => done; tools in schema are irrelevant."""
    from metateam.runtime.agent import Agent

    class Stub:
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "hello there"}]
            self.compress_llm = _FakeLLM(
                '{"state": "done", "reason": "greeting already answered"}'
            )
            self._disable_tools = False
            self._goal_satisfied = None
            self._response_language_src = "hello there"
            self.events: list[tuple[str, dict]] = []

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

    stub = Stub()
    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "Hello! How can I help you today?",
        loop_start=0,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep_going is False
    assert nudges == 0
    assert stub._goal_satisfied is True
    settled = [d for name, d in stub.events if name == "goal_settled"]
    assert settled and settled[-1].get("satisfied") is True
    assert settled[-1].get("blocked") is False


def test_reject_early_stop_action_without_evidence_continues() -> None:
    """Action request with only a narrated plan (no tool evidence) => continue."""
    from metateam.runtime.agent import Agent

    class Stub:
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "查一下最新记录并总结"}]
            self.compress_llm = _FakeLLM(
                '{"state": "continue", "reason": "lookup not done yet"}'
            )
            self._disable_tools = False
            self._goal_satisfied = None
            self._response_language_src = "查一下最新记录并总结"
            self.events: list[tuple[str, dict]] = []

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

    stub = Stub()
    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "我需要使用plugin_demo_search_messages来搜索最新记录。",
        loop_start=0,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep_going is True
    assert nudges == 1
    assert stub.messages[-1]["sidekick"]["kind"] == "goal_continue"
    assert stub.messages[-1]["role"] == "system"
    assert "ReAct" not in stub.messages[-1]["content"]


def test_tool_evidence_force_includes_early_mutations() -> None:
    messages = [{"role": "user", "content": "goal"}]
    messages.append({"role": "tool", "name": "delete_file", "content": "deleted early.txt"})
    for i in range(12):
        messages.append({"role": "tool", "name": "read_file", "content": f"chunk-{i}"})
    text = tool_evidence(messages, start=1, limit_items=8, item_chars=80)
    assert "delete_file: deleted early.txt" in text
    # recent reads still present
    assert "chunk-11" in text


def test_coalesce_internal_nudge_dedupes_same_kind() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {
            "role": "system",
            "content": "old continue essay " * 20,
            "sidekick_internal": True,
            "sidekick": {"internal": True, "kind": "goal_continue"},
        },
    ]
    coalesce_internal_nudge(
        messages,
        kind="goal_continue",
        content=GOAL_CONTINUE_NUDGE,
        role="system",
    )
    kinds = [
        (m.get("sidekick") or {}).get("kind")
        for m in messages
        if isinstance(m.get("sidekick"), dict)
    ]
    assert kinds.count("goal_continue") == 1
    assert messages[-1]["content"] == GOAL_CONTINUE_NUDGE
    assert "old continue essay" not in messages[-1]["content"]
    # Second coalesce still a single copy
    coalesce_internal_nudge(
        messages, kind="goal_continue", content="short delta", role="system"
    )
    assert sum(
        1
        for m in messages
        if isinstance(m.get("sidekick"), dict)
        and m["sidekick"].get("kind") == "goal_continue"
    ) == 1
    assert messages[-1]["content"] == "short delta"


def test_active_obligation_uses_compaction_pins_not_raw_mark() -> None:
    from metateam.runtime.context import _build_compaction_message

    compact = _build_compaction_message(
        "## Pinned (do not drop)\n### User requests\n- 修复北大概况图片\n",
        "## Goal\nfix images\n## Next\nfetch real API",
    )
    messages = [
        {"role": "system", "content": "sys"},
        compact,
        # No later real user — obligation must come from pins.
    ]
    goal = active_obligation(messages)
    assert "修复北大概况图片" in goal
    assert "CONTEXT COMPACTION" not in goal
    assert is_control_user_message(compact) is True


def test_active_obligation_prefers_a_workers_assigned_goal() -> None:
    """A leaf's user turn is a bootstrap line; its task is the delegated goal."""
    messages = [
        {"role": "system", "content": "YOUR TASK: 审计 server.py"},
        {"role": "user", "content": "Begin now. Use tools, then summarize."},
    ]
    goal = active_obligation(
        messages,
        assigned_goal="审计 server.py，输出文件:行号 + 问题 + 修复建议",
        assigned_context="重点：未定义变量、异步竞态、错误处理",
    )
    assert "审计 server.py" in goal
    assert "未定义变量" in goal
    assert "Begin now" not in goal


def test_assess_goal_verdict_includes_the_worker_contract() -> None:
    seen: list[str] = []

    class _Capture:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            seen.append(user)
            return '{"state": "done", "reason": "report delivered"}'

    state = assess_goal_state(
        _Capture(),
        goal="审计 server.py，输出问题清单",
        evidence="read_file: server.py",
        candidate="findings: 3 bugs\nrecommendation: fix them\nopen_questions: none",
        contract={
            "stop_when": "goal satisfied or blocked with open_questions",
            "deliverable": ["findings", "recommendation", "open_questions"],
        },
    )
    assert state == "done"
    payload = seen[0]
    assert "Active goal" in payload
    assert "# Delegated worker contract" in payload
    assert "findings, recommendation, open_questions" in payload
    assert "goal satisfied or blocked with open_questions" in payload
    # The worker's report hands open items to the lead; that is completion.
    assert "state=done once the report" in payload


def test_reject_early_stop_judges_a_leaf_against_its_delegated_task() -> None:
    from metateam.runtime.agent import Agent

    seen: list[str] = []

    class _Capture:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            seen.append(user)
            return '{"state": "done", "reason": "deliverable reported"}'

    class _Stub:
        def __init__(self) -> None:
            self.is_subagent = True
            self.full_agent = False
            self.goal = "审计 server.py，输出文件:行号 + 问题 + 修复建议"
            self.context = "[AskPacket]\nstop_when: goal satisfied"
            self.task_contract = {
                "stop_when": "goal satisfied or blocked with open_questions",
                "deliverable": ["findings", "recommendation", "open_questions"],
            }
            self.messages = [
                {"role": "system", "content": "YOUR TASK: ..."},
                {"role": "user", "content": "Begin now. Use tools, then summarize."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "read_file",
                    "content": "1|def handler(): ...",
                },
                {
                    "role": "assistant",
                    "content": "findings: 3 bugs\nopen_questions: none",
                },
            ]
            self.compress_llm = _Capture()
            self._disable_tools = False
            self._goal_satisfied = None
            self._response_language_src = "审计 server.py"
            self.events: list[tuple[str, dict]] = []

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

    stub = _Stub()
    keep_going, nudges = Agent._reject_early_stop(
        stub,
        "findings: 3 bugs\nopen_questions: none",
        loop_start=2,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep_going is False
    assert nudges == 0
    assert stub._goal_satisfied is True
    payload = seen[0]
    assert "审计 server.py" in payload
    assert "Begin now" not in payload.split("Tool evidence")[0]
    assert "# Delegated worker contract" in payload
    assert "findings, recommendation, open_questions" in payload


def test_leaf_iteration_budget_is_finite_when_config_is_zero() -> None:
    from metateam.core.config import Settings
    from metateam.runtime.agent import Agent, SUBAGENT_ITERATION_BUDGET

    agent = Agent.__new__(Agent)
    agent.is_subagent = True
    agent.full_agent = False
    agent.settings = Settings()
    agent.settings.subagent_max_iterations = 0
    assert Agent._iteration_budget(agent) == SUBAGENT_ITERATION_BUDGET

    agent.settings.subagent_max_iterations = 5
    assert Agent._iteration_budget(agent) == 5

    # The lead keeps its configured behaviour; only workers get the floor.
    agent.is_subagent = False
    agent.settings.max_iterations = 0
    assert Agent._iteration_budget(agent) == 0

