# -*- coding: utf-8 -*-
"""ReAct goal completion owns stop/continue; tool-intent is auxiliary only."""

from __future__ import annotations

from typing import Any, Iterator, Optional


class _GoalAwareCompress:
    """Return continue until a tool result exists in the prompt, else done."""

    def __init__(self, *, default_done: bool = False) -> None:
        self.default_done = default_done
        self.calls: list[str] = []

    def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls.append(user)
        low = (user or "").lower()
        # Evidence section non-empty beyond "(none)" => goal can settle.
        if "tool evidence:" in low:
            after = low.split("tool evidence:", 1)[1]
            evidence_part = after.split("candidate final reply:", 1)[0]
            if evidence_part.strip() and "(none)" not in evidence_part:
                return '{"state": "done", "reason": "evidence present"}'
        if self.default_done:
            return '{"state": "done", "reason": "conversational complete"}'
        return '{"state": "continue", "reason": "action still missing"}'


class _FakeLLM:
    """First reply narrates a tool; second emits a native tool_call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        cancel_check: Optional[Any] = None,
        tool_choice: Optional[Any] = None,
    ) -> Iterator[tuple[str, Any]]:
        self.calls.append({"tools": tools, "tool_choice": tool_choice})
        self._n += 1
        if self._n == 1:
            text = "用户让我查询数据。我需要使用plugin_demo_search_messages来搜索最新记录。"
            yield ("delta", text)
            yield (
                "done",
                {"role": "assistant", "content": text, "tool_calls": []},
            )
            return
        tc = {
            "id": "call_demo_1",
            "type": "function",
            "function": {
                "name": "plugin_demo_search_messages",
                "arguments": '{"q":"latest"}',
            },
        }
        yield ("done", {"role": "assistant", "content": "", "tool_calls": [tc]})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": "done", "tool_calls": []}


class _ChatOnlyLLM:
    """Normal conversational reply; never emits tool_calls."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def stream_chat(
        self,
        messages,
        tools=None,
        temperature=None,
        cancel_check=None,
        tool_choice=None,
    ):
        self.calls.append({"tools": tools, "tool_choice": tool_choice})
        yield ("delta", self.reply)
        yield ("done", {"role": "assistant", "content": self.reply, "tool_calls": []})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": self.reply, "tool_calls": []}


def _schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "demo",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _patch_budget(monkeypatch, agent_mod) -> None:
    monkeypatch.setattr(agent_mod, "debug_dump_budget", lambda messages: "{}")
    monkeypatch.setattr(agent_mod, "context_budget_tokens", lambda *a, **k: 0)
    monkeypatch.setattr(agent_mod, "messages_tokens", lambda *a, **k: 0)
    monkeypatch.setattr(agent_mod, "schemas_tokens", lambda *a, **k: 0)


def _make_registry():
    from metateam.runtime.tool_registry import ToolRegistry, Tool

    registry = ToolRegistry()

    def _demo_search(q: str = "") -> str:
        return f"FOUND:{q or 'ok'}"

    registry.register(
        Tool(
            "plugin_demo_search_messages",
            "Search demo records",
            {"type": "object", "properties": {"q": {"type": "string"}}},
            _demo_search,
        )
    )
    return registry


def _bind_execute(stub):
    def _fake_execute(tool_calls):
        out = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            call_id = str(tc.get("id") or "call")
            stub._emit(
                "tool_end",
                {"name": name, "call_id": call_id, "ok": True, "result": "FOUND:latest"},
            )
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": "FOUND:latest",
                }
            )
        return out

    stub._execute_tools = _fake_execute  # type: ignore[method-assign]


def test_conversational_reply_stops_once_even_with_tools(monkeypatch) -> None:
    """Generic chat (greeting/thanks) must not auto-continue when tools exist."""
    from metateam.runtime import agent as agent_mod
    from metateam.runtime.agent import Agent

    _patch_budget(monkeypatch, agent_mod)
    registry = _make_registry()
    events: list[tuple[str, dict]] = []

    class Stub(Agent):
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "hello there"}]
            self.llm = _ChatOnlyLLM("Hello! How can I help you today?")
            self.compress_llm = _GoalAwareCompress(default_done=True)
            self.registry = registry
            self.settings = type(
                "S",
                (),
                {"context_limit": 128000, "temperature": 0.2, "plugin_bound_ids": None},
            )()
            self.is_subagent = False
            self.full_agent = True
            self._disable_tools = False
            self._block_delegate = False
            self._requires_initial_delegation = False
            self._delegation_started = False
            self._classify = False
            self._response_language_src = "hello there"
            self._goal_satisfied = None
            self._last_compressed = False

        def cancelled(self) -> bool:
            return False

        def _emit(self, name: str, data: dict) -> None:
            events.append((name, data))

        def _emit_paced_text(self, text: str, reset: bool = True) -> None:
            self._emit("assistant_delta", {"chunk": text})

        def _maybe_compress(self) -> None:
            return None

        def _model_messages(self) -> list:
            return list(self.messages)

        def _stream_text_to_ui(self, *a, **k) -> None:
            return None

    stub = Stub()
    _bind_execute(stub)
    schemas = [_schema("plugin_demo_search_messages")]
    final, turned, cancelled, _ = stub._run_agent_loop(
        max_iters=4,
        tools=schemas,
        emit_assistant_text=True,
        hold_for_goal=True,
    )
    assert cancelled is False
    assert len(stub.llm.calls) == 1
    assert stub.llm.calls[0].get("tool_choice") in (None, "auto")
    assert stub._goal_satisfied is True
    status_texts = [e[1].get("text", "") for e in events if e[0] == "assistant_status"]
    assert not any("继续执行" in t for t in status_texts)
    assert final


def test_agent_loop_continues_incomplete_action_via_goal(monkeypatch) -> None:
    """Unfinished action request continues; auxiliary force_tool_choice may apply."""
    from metateam.runtime import agent as agent_mod
    from metateam.runtime.agent import Agent

    _patch_budget(monkeypatch, agent_mod)
    registry = _make_registry()
    events: list[tuple[str, dict]] = []

    class Stub(Agent):
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "帮我查一下最新记录"}]
            self.llm = _FakeLLM()
            self.compress_llm = _GoalAwareCompress(default_done=False)
            self.registry = registry
            self.settings = type(
                "S",
                (),
                {"context_limit": 128000, "temperature": 0.2, "plugin_bound_ids": None},
            )()
            self.is_subagent = False
            self.full_agent = True
            self._disable_tools = False
            self._block_delegate = False
            self._requires_initial_delegation = False
            self._delegation_started = False
            self._classify = False
            self._response_language_src = "帮我查一下最新记录"
            self._goal_satisfied = None
            self._last_compressed = False

        def cancelled(self) -> bool:
            return False

        def _emit(self, name: str, data: dict) -> None:
            events.append((name, data))

        def _emit_paced_text(self, text: str, reset: bool = True) -> None:
            self._emit("assistant_delta", {"chunk": text})

        def _maybe_compress(self) -> None:
            return None

        def _model_messages(self) -> list:
            return list(self.messages)

        def _stream_text_to_ui(self, *a, **k) -> None:
            return None

    stub = Stub()
    _bind_execute(stub)
    schemas = [_schema("plugin_demo_search_messages")]
    stub._run_agent_loop(
        max_iters=4,
        tools=schemas,
        emit_assistant_text=True,
        hold_for_goal=True,
    )

    assert len(stub.llm.calls) >= 2
    assert stub.llm.calls[0].get("tool_choice") in (None, "auto")
    # Goal continue + narrated intent => auxiliary required on next Think.
    assert stub.llm.calls[1].get("tool_choice") == "required"
    tool_roles = [m for m in stub.messages if m.get("role") == "tool"]
    tool_events = [e for e in events if e[0] in {"tool_end", "tool_start", "tool_call_delta"}]
    assert tool_roles or tool_events or any(
        (m.get("tool_calls") for m in stub.messages if m.get("role") == "assistant")
    )
    status_texts = [e[1].get("text", "") for e in events if e[0] == "assistant_status"]
    assert any("未完成" in t for t in status_texts)
    # Goal-continue is an internal system instruction (not a fake user ask).
    cont = [
        m
        for m in stub.messages
        if (m.get("sidekick") or {}).get("kind") == "goal_continue"
    ]
    assert cont, "expected goal_continue control message"
    assert cont[0].get("role") == "system"
    assert "ReAct" not in str(cont[0].get("content") or "")


def test_text_only_intent_does_not_finalize_without_tools_or_error() -> None:
    from metateam.runtime.goal_completion import (
        looks_like_tool_intent_only,
        should_continue_for_tool_intent,
        tool_intent_unavailable_message,
    )

    prose = "用户让我查询数据。我需要使用plugin_demo_search_messages来搜索最新记录。"
    assert looks_like_tool_intent_only(prose) is True
    assert should_continue_for_tool_intent(
        prose,
        tools_available=True,
        had_tool_results=False,
        nudges=0,
        known_tool_names=["plugin_demo_search_messages"],
    )
    err = tool_intent_unavailable_message(prose, known_tool_names=[])
    assert err and "plugin_demo_search_messages" in err


def test_goal_judge_continues_narrated_intent_without_evidence() -> None:
    """Root cause fix: empty evidence + intent prose → LLM verdict continue.

    No hard-coded override: a judge that reads Evidence present / the hard
    rule in the system prompt must return continue so the loop re-enters Think.
    """
    from metateam.runtime.goal_completion import (
        _GOAL_STATE_SYSTEM,
        assess_goal_state,
    )

    class _EvidenceAwareJudge:
        def __init__(self) -> None:
            self.system = ""
            self.user = ""

        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            self.system = system
            self.user = user
            low = (user or "").lower()
            # Mirror the hard rule the real model is instructed to follow.
            if "evidence present: no" in low and (
                "调用" in (user or "")
                or "工具" in (user or "")
                or "use" in low
                or "call" in low
            ):
                return '{"state": "continue", "reason": "narrated intent, no evidence"}'
            return '{"state": "done", "reason": "ok"}'

    judge = _EvidenceAwareJudge()
    state = assess_goal_state(
        judge,
        goal="生成一个 Dockerfile",
        evidence="",
        candidate="用户想要生成Dockerfile。让我调用generate_dockerfile工具。",
    )
    assert state == "continue"
    assert "Evidence present: no" in judge.user
    assert "zero tool results" in judge.user
    assert "narrated intent is never completion" in judge.system.lower()
    assert "MUST be" in _GOAL_STATE_SYSTEM or "MUST be continue" in _GOAL_STATE_SYSTEM


# Generic samples (not product branch conditions in production code).
SAMPLE_PLUGIN_INTENT = (
    "用户让我继续，我需要使用演示插件来查询最新数据。让我先搜索相关记录。"
)
SAMPLE_PREFIXED_INTENT = (
    "用户让我查询数据。我需要使用plugin_demo_search_messages来搜索最新记录。"
)


def test_generic_intent_samples_still_match_auxiliary_heuristic() -> None:
    from metateam.runtime.goal_completion import (
        looks_like_tool_intent_only,
        should_continue_for_tool_intent,
        intent_probe_text,
    )

    assert looks_like_tool_intent_only(SAMPLE_PLUGIN_INTENT) is True
    assert looks_like_tool_intent_only(SAMPLE_PREFIXED_INTENT) is True
    assert should_continue_for_tool_intent(
        SAMPLE_PREFIXED_INTENT,
        tools_available=True,
        had_tool_results=False,
        nudges=0,
        known_tool_names=["plugin_demo_search_messages", "list_dir"],
    )
    assert looks_like_tool_intent_only("让我先搜索相关记录。") is True
    assert looks_like_tool_intent_only("我需要使用演示插件来查询数据") is True
    probe = intent_probe_text("", SAMPLE_PREFIXED_INTENT)
    assert looks_like_tool_intent_only(probe) is True
    # Conversational completions must not look like tool intent.
    for chat in (
        "你好！有什么可以帮你的吗？",
        "Hello! How can I help?",
        "Thanks, that helps.",
        "谢谢，明白了。",
        "好的。",
    ):
        assert looks_like_tool_intent_only(chat) is False
        assert (
            should_continue_for_tool_intent(
                chat,
                tools_available=True,
                had_tool_results=False,
                nudges=0,
                known_tool_names=["plugin_demo_search_messages"],
            )
            is False
        )


def test_display_name_maps_via_plugin_registry() -> None:
    from metateam.runtime.goal_completion import (
        resolve_display_named_plugin_tools,
        should_continue_for_tool_intent,
    )

    summaries = [
        {
            "id": "demo",
            "name": "演示",
            "tools": [{"name": "plugin_demo_search_messages"}],
        }
    ]
    mapped = resolve_display_named_plugin_tools("我需要使用演示插件来查询", summaries)
    assert "plugin_demo_search_messages" in mapped
    assert should_continue_for_tool_intent(
        "我需要使用演示插件来查询最新数据",
        tools_available=True,
        had_tool_results=False,
        nudges=0,
        known_tool_names=["plugin_demo_search_messages"],
        plugin_summaries=summaries,
    )


class _StickyIntentLLM:
    """Keeps narrating intent; soft fallback round must still attach tools."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._n = 0

    def stream_chat(
        self,
        messages,
        tools=None,
        temperature=None,
        cancel_check=None,
        tool_choice=None,
    ):
        self.calls.append({"tools": tools, "tool_choice": tool_choice})
        self._n += 1
        if self._n <= 2:
            text = SAMPLE_PREFIXED_INTENT
            yield ("delta", text)
            yield ("done", {"role": "assistant", "content": text, "tool_calls": []})
            return
        tc = {
            "id": "call_demo_2",
            "type": "function",
            "function": {
                "name": "plugin_demo_search_messages",
                "arguments": '{"q":"latest"}',
            },
        }
        yield ("done", {"role": "assistant", "content": "", "tool_calls": [tc]})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": "done", "tool_calls": []}


def test_agent_loop_soft_fallback_after_required(monkeypatch) -> None:
    from metateam.runtime import agent as agent_mod
    from metateam.runtime.agent import Agent

    _patch_budget(monkeypatch, agent_mod)
    registry = _make_registry()
    events: list[tuple[str, dict]] = []

    class Stub(Agent):
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "继续查最新记录"}]
            self.llm = _StickyIntentLLM()
            self.compress_llm = _GoalAwareCompress(default_done=False)
            self.registry = registry
            self.settings = type(
                "S",
                (),
                {"context_limit": 128000, "temperature": 0.2, "plugin_bound_ids": None},
            )()
            self.is_subagent = False
            self.full_agent = True
            self._disable_tools = False
            self._block_delegate = False
            self._requires_initial_delegation = False
            self._delegation_started = False
            self._classify = False
            self._response_language_src = "继续查最新记录"
            self._goal_satisfied = None
            self._last_compressed = False

        def cancelled(self) -> bool:
            return False

        def _emit(self, name: str, data: dict) -> None:
            events.append((name, data))

        def _emit_paced_text(self, text: str, reset: bool = True) -> None:
            self._emit("assistant_delta", {"chunk": text})

        def _maybe_compress(self) -> None:
            return None

        def _model_messages(self) -> list:
            return list(self.messages)

        def _stream_text_to_ui(self, *a, **k) -> None:
            return None

    stub = Stub()
    _bind_execute(stub)
    schemas = [_schema("plugin_demo_search_messages")]
    stub._run_agent_loop(
        max_iters=6,
        tools=schemas,
        emit_assistant_text=True,
        hold_for_goal=True,
    )

    assert len(stub.llm.calls) >= 3
    assert stub.llm.calls[0].get("tool_choice") in (None, "auto")
    assert stub.llm.calls[1].get("tool_choice") == "required"
    assert stub.llm.calls[2].get("tools")
    assert stub.llm.calls[2].get("tool_choice") in (None, "auto")


def _make_colon_stub(monkeypatch, *, llm, compress_llm, user_text="改一下首页"):
    """Agent stub wired for the real streaming loop, with event capture."""
    from metateam.runtime import agent as agent_mod
    from metateam.runtime.agent import Agent

    _patch_budget(monkeypatch, agent_mod)
    events: list[tuple[str, dict]] = []

    class Stub(Agent):
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": user_text}]
            self.llm = llm
            self.compress_llm = compress_llm
            self.registry = _make_registry()
            self.settings = type(
                "S",
                (),
                {"context_limit": 128000, "temperature": 0.2, "plugin_bound_ids": None},
            )()
            self.is_subagent = False
            self.full_agent = True
            self._disable_tools = False
            self._block_delegate = False
            self._requires_initial_delegation = False
            self._delegation_started = False
            self._classify = False
            self._response_language_src = user_text
            self._goal_satisfied = None
            self._last_compressed = False

        def cancelled(self) -> bool:
            return False

        def _emit(self, name: str, data: dict) -> None:
            events.append((name, data))

        def _emit_paced_text(self, text: str, reset: bool = True) -> None:
            self._emit("assistant_delta", {"chunk": text})

        def _maybe_compress(self) -> None:
            return None

        def _model_messages(self) -> list:
            return list(self.messages)

        def _stream_text_to_ui(self, *a, **k) -> None:
            return None

    stub = Stub()
    _bind_execute(stub)
    return stub, events


def test_trailing_colon_final_reply_is_not_persisted(monkeypatch) -> None:
    stub, events = _make_colon_stub(
        monkeypatch,
        llm=_ChatOnlyLLM("先检查页面中“北大人物”区块的位置："),
        compress_llm=_GoalAwareCompress(default_done=True),
    )
    final, _, _, _ = stub._run_agent_loop(
        max_iters=3,
        tools=[_schema("plugin_demo_search_messages")],
        emit_assistant_text=True,
        hold_for_goal=True,
    )
    assert final == "先检查页面中“北大人物”区块的位置"
    stored = [
        m
        for m in stub.messages
        if m.get("role") == "assistant" and not m.get("tool_calls")
    ]
    assert stored[-1]["content"] == "先检查页面中“北大人物”区块的位置"
    seen = "".join(
        str(d.get("chunk") or "") for name, d in events if name == "assistant_delta"
    )
    assert not seen.rstrip().endswith(("：", ":"))


class _ColonToolLLM:
    """Round 1: colon-ended preamble with a tool call. Round 2: final answer."""

    def __init__(self) -> None:
        self._n = 0
        self.calls: list[dict[str, Any]] = []

    def stream_chat(
        self,
        messages,
        tools=None,
        temperature=None,
        cancel_check=None,
        tool_choice=None,
    ):
        self.calls.append({"tools": tools, "tool_choice": tool_choice})
        self._n += 1
        if self._n == 1:
            text = "先检查本地页面中“北大人物”区块的位置："
            tc = {
                "id": "call_colon_1",
                "type": "function",
                "function": {
                    "name": "plugin_demo_search_messages",
                    "arguments": '{"q":"北大人物"}',
                },
            }
            yield ("delta", text)
            yield ("done", {"role": "assistant", "content": text, "tool_calls": [tc]})
            return
        text = "区块位置已确认。"
        yield ("delta", text)
        yield ("done", {"role": "assistant", "content": text, "tool_calls": []})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": "已处理。", "tool_calls": []}


def test_trailing_colon_tool_preamble_is_not_persisted(monkeypatch) -> None:
    stub, _ = _make_colon_stub(
        monkeypatch,
        llm=_ColonToolLLM(),
        compress_llm=_GoalAwareCompress(default_done=True),
    )
    stub._run_agent_loop(
        max_iters=4,
        tools=[_schema("plugin_demo_search_messages")],
        emit_assistant_text=True,
        hold_for_goal=True,
    )
    for msg in stub.messages:
        if msg.get("role") == "assistant":
            assert not str(msg.get("content") or "").rstrip().endswith(("：", ":"))


def test_stream_final_reply_strips_trailing_colon(monkeypatch) -> None:
    stub, _ = _make_colon_stub(
        monkeypatch,
        llm=_ChatOnlyLLM("区块已确认："),
        compress_llm=_GoalAwareCompress(default_done=True),
    )
    text = stub._stream_final_reply()
    assert text == "区块已确认"
    assert stub.messages[-1]["content"] == "区块已确认"
