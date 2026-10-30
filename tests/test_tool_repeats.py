"""A repeated call is a loop only when it keeps returning the same result."""

from __future__ import annotations

from metateam.runtime.tool_repeats import (
    PROCEED,
    STOP,
    ToolRepeatTracker,
    call_signature_set,
    result_history,
    signature_for_tool_call,
)


def _call(call_id: str, name: str = "read_file", args: str = '{"path":"a.txt"}') -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _row(call_id: str, content: str, name: str = "read_file") -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def _sig(name: str = "read_file", args: str = '{"path":"a.txt"}') -> str:
    sig = signature_for_tool_call(_call("x", name, args))
    assert sig
    return sig


def test_incomplete_write_is_not_a_repeat_signature() -> None:
    path_only = _call(
        "1",
        "write_file",
        '{"path":"a.txt","content":"","_incomplete":true}',
    )
    assert signature_for_tool_call(path_only) is None
    assert call_signature_set({"tool_calls": [path_only]}) == frozenset()


def test_complete_write_has_a_signature() -> None:
    full = _call("1", "write_file", '{"path":"a.txt","content":"hello"}')
    assert signature_for_tool_call(full)


def test_result_history_is_newest_first_per_signature() -> None:
    sig = _sig()
    messages = [
        {"role": "assistant", "tool_calls": [_call("1")]},
        _row("1", "first"),
        {"role": "assistant", "tool_calls": [_call("2")]},
        _row("2", "second"),
    ]
    history = result_history(messages)
    assert history[sig] == ["second", "first"]


def test_first_repeat_runs_so_a_poll_can_observe_a_change() -> None:
    tracker = ToolRepeatTracker()
    sig = frozenset({_sig()})
    assert tracker.observe(sig, {}).action == PROCEED
    # One prior result: cannot classify yet, let the call run once.
    decision = tracker.observe(sig, {_sig(): ["running"]})
    assert decision.action == PROCEED
    assert decision.repeated is True


def test_same_result_escalates_from_nudge_to_stop() -> None:
    tracker = ToolRepeatTracker()
    sig = frozenset({_sig()})
    first_run = {_sig(): ["running"]}
    results = {_sig(): ["running", "running"]}
    assert tracker.observe(sig, {}).action == PROCEED
    assert tracker.observe(sig, first_run).action == PROCEED
    assert tracker.observe(sig, results).action == "nudge"
    assert tracker.observe(sig, results).action == "nudge"
    assert tracker.observe(sig, results).action == STOP


def test_changed_result_resets_the_stall_counter() -> None:
    tracker = ToolRepeatTracker()
    sig = frozenset({_sig()})
    tracker.observe(sig, {})
    tracker.observe(sig, {_sig(): ["same"]})
    tracker.observe(sig, {_sig(): ["same", "same"]})
    assert tracker.stalls == 1
    decision = tracker.observe(sig, {_sig(): ["new", "same"]})
    assert decision.action == PROCEED
    assert decision.result_changed is True
    assert tracker.stalls == 0


def test_a_different_call_between_repeats_resets_everything() -> None:
    """Long tasks may repeat an operation later; that is not three strikes."""
    tracker = ToolRepeatTracker()
    first = frozenset({_sig()})
    other = frozenset({_sig(args='{"path":"b.txt"}')})
    one_run = {_sig(): ["same"]}
    results = {_sig(): ["same", "same"]}
    tracker.observe(first, {})
    tracker.observe(first, one_run)
    assert tracker.observe(first, results).action == "nudge"
    # Real work happened between the two occurrences.
    assert tracker.observe(other, {}).action == PROCEED
    tracker.observe(first, {})
    tracker.observe(first, one_run)
    assert tracker.observe(first, results).action == "nudge"
    assert tracker.stalls == 1


def test_repeat_that_never_produced_a_result_is_a_stall() -> None:
    """Dropped/blocked calls never append a tool row; they cannot advance."""
    tracker = ToolRepeatTracker()
    sig = frozenset({_sig()})
    tracker.observe(sig, {})
    assert tracker.observe(sig, {}).action == "nudge"


def test_dedupe_stub_is_not_a_changed_result() -> None:
    tracker = ToolRepeatTracker()
    sig = frozenset({_sig()})
    results = {
        _sig(): [
            "[already in context] a.txt (12 lines) is already in this conversation.",
            "full file body",
        ]
    }
    tracker.observe(sig, {})
    tracker.observe(sig, {_sig(): ["full file body"]})
    assert tracker.observe(sig, results).action == "nudge"


# --- loop-level regression: a poll that changes is not stopped ----------------


def _patch_budget(monkeypatch) -> None:
    from metateam.runtime import agent as agent_mod

    monkeypatch.setattr(agent_mod, "debug_dump_budget", lambda messages: "{}")
    monkeypatch.setattr(agent_mod, "context_budget_tokens", lambda *a, **k: 0)
    monkeypatch.setattr(agent_mod, "messages_tokens", lambda *a, **k: 0)
    monkeypatch.setattr(agent_mod, "schemas_tokens", lambda *a, **k: 0)


class _PollLLM:
    """Every tool round asks for the same poll; without tools it answers."""

    def __init__(self) -> None:
        self.rounds = 0

    def stream_chat(
        self,
        messages,
        tools=None,
        temperature=None,
        cancel_check=None,
        tool_choice=None,
    ):
        self.rounds += 1
        if tools is None:
            text = "已经完成。"
            yield ("delta", text)
            yield ("done", {"role": "assistant", "content": text, "tool_calls": []})
            return
        tc = {
            "id": f"call_{self.rounds}",
            "type": "function",
            "function": {"name": "plugin_demo_poll", "arguments": "{}"},
        }
        yield ("done", {"role": "assistant", "content": "", "tool_calls": [tc]})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": "已经完成。", "tool_calls": []}


def _make_loop_stub(results: list[str]):
    from metateam.runtime.agent import Agent
    from metateam.runtime.tool_registry import Tool, ToolRegistry

    executions: list[str] = []

    class Stub(Agent):
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "poll the job"}]
            self.llm = _PollLLM()
            self.compress_llm = None
            self.registry = ToolRegistry()
            self.registry.register(
                Tool(
                    "plugin_demo_poll",
                    "Poll demo state",
                    {"type": "object", "properties": {}},
                    lambda: "ok",
                )
            )
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
            self._response_language_src = "poll the job"
            self._goal_satisfied = None
            self._last_compressed = False

        def cancelled(self) -> bool:
            return False

        def _emit(self, name: str, data: dict) -> None:
            return None

        def _emit_paced_text(self, text: str, reset: bool = True) -> None:
            return None

        def _maybe_compress(self) -> None:
            return None

        def _model_messages(self) -> list:
            return list(self.messages)

        def _stream_text_to_ui(self, *a, **k) -> None:
            return None

        def _execute_tools(self, tool_calls):
            out = []
            for tc in tool_calls:
                index = len(executions)
                content = results[min(index, len(results) - 1)]
                executions.append(content)
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tc.get("id") or "call"),
                        "name": "plugin_demo_poll",
                        "content": content,
                    }
                )
            return out

    stub = Stub()
    return stub, executions


def _run_poll_loop(monkeypatch, results: list[str]):
    _patch_budget(monkeypatch)
    stub, executions = _make_loop_stub(results)
    final, rounds, _cancelled, _compressed = stub._run_agent_loop(
        max_iters=8,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "plugin_demo_poll",
                    "description": "poll",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        emit_assistant_text=True,
        hold_for_goal=True,
    )
    return stub, final, rounds, executions


def test_changing_poll_results_keep_the_turn_running(monkeypatch) -> None:
    stub, final, _rounds, executions = _run_poll_loop(
        monkeypatch, [f"state {i}" for i in range(1, 9)]
    )
    # Every repeat saw a new result, so none was treated as a stuck loop.
    assert len(executions) == 8
    assert "没有新进展" not in final


def test_identical_results_stop_after_consecutive_stalls(monkeypatch) -> None:
    stub, final, _rounds, executions = _run_poll_loop(monkeypatch, ["same"])
    # Two runs establish "the result never changes", then it nudges/stops.
    assert len(executions) == 2
    kinds = [
        str((m.get("sidekick") or {}).get("kind") or "")
        for m in stub.messages
        if isinstance(m.get("sidekick"), dict)
    ]
    assert "tool_repeat_stop" in kinds
    # The final status is written by the model with tools removed, not spliced
    # in as a canned sentence.
    assert final == "已经完成。"
