"""A finished worker must stop: the judge sees its delegated task, not a bootstrap line."""

from __future__ import annotations

from typing import Any, Iterator, Optional

from metateam.runtime.agent import Agent
from metateam.runtime.tool_registry import Tool, ToolRegistry


class _WorkerLLM:
    """One tool round, then the worker's final report (no tool_calls)."""

    def __init__(self) -> None:
        self.rounds = 0
        self.tool_choices: list[Any] = []

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        cancel_check: Optional[Any] = None,
        tool_choice: Optional[Any] = None,
    ) -> Iterator[tuple[str, Any]]:
        self.rounds += 1
        self.tool_choices.append(tool_choice)
        if self.rounds == 1:
            tc = {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"server.py"}'},
            }
            yield ("done", {"role": "assistant", "content": "", "tool_calls": [tc]})
            return
        text = "findings: 3 bugs\nrecommendation: fix them\nopen_questions: none"
        yield ("delta", text)
        yield ("done", {"role": "assistant", "content": text, "tool_calls": []})

    def chat(self, messages, tools=None, temperature=None, tool_choice=None):
        return {"role": "assistant", "content": "done", "tool_calls": []}


class _GoalJudge:
    """Mimics the failure: no visible task => continue forever."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
        self.payloads.append(user)
        if "审计 server.py" in user and "# Delegated worker contract" in user:
            return '{"state": "done", "reason": "report delivered"}'
        return '{"state": "continue", "reason": "assigned task is not visible"}'


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


def test_worker_loop_stops_when_its_deliverable_is_reported(monkeypatch) -> None:
    from metateam.runtime import agent as agent_mod

    _patch_budget(monkeypatch, agent_mod)
    events: list[tuple[str, dict]] = []
    judge = _GoalJudge()

    class Stub(Agent):
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
                {"role": "user", "content": "Begin now. Use tools, then summarize."}
            ]
            self.llm = _WorkerLLM()
            self.compress_llm = judge
            self.registry = ToolRegistry()
            self.registry.register(
                Tool(
                    "read_file",
                    "read a file",
                    {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    lambda path: "1|def handler(): ...",
                )
            )
            self.settings = type(
                "S",
                (),
                {"context_limit": 32000, "temperature": 0.2, "plugin_bound_ids": None},
            )()
            self._disable_tools = False
            self._block_delegate = False
            self._requires_initial_delegation = False
            self._delegation_started = False
            self._classify = False
            self._response_language_src = "审计 server.py"
            self._goal_satisfied = None
            self._last_compressed = False
            self._last_shown_reasoning = ""

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

        def _execute_tools(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for tc in tool_calls:
                call_id = str(tc.get("id") or "call")
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": "read_file",
                        "content": "1|def handler(): ...",
                    }
                )
            return out

    stub = Stub()
    final, turned, cancelled, _ = stub._run_agent_loop(
        max_iters=6,
        tools=[_schema("read_file")],
        emit_assistant_text=True,
        hold_for_goal=True,
    )

    assert cancelled is False
    # One tool round + one final report; no extra "continue" round.
    assert stub.llm.rounds == 2
    assert turned == 2
    assert stub._goal_satisfied is True
    assert "findings" in final
    payload = judge.payloads[-1]
    assert "审计 server.py" in payload
    assert "# Delegated worker contract" in payload
    # The bootstrap user line is not mistaken for the worker's obligation.
    assert "Begin now" not in payload.split("Tool evidence")[0]
    settled = [d for name, d in events if name == "goal_settled"]
    assert settled and settled[-1].get("state") == "done"
