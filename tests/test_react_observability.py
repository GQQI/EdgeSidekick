"""P0: ReAct gate emits auditable judge events; live labels stay human-readable."""

from __future__ import annotations


def test_assess_goal_verdict_exposes_evidence_flag() -> None:
    from metateam.runtime.goal_completion import assess_goal_verdict

    class _LLM:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            assert "Evidence present: no" in user
            return '{"state": "continue", "reason": "intent only"}'

    out = assess_goal_verdict(
        _LLM(),
        goal="生成 Dockerfile",
        evidence="",
        candidate="让我调用 generate_dockerfile 工具。",
    )
    assert out["state"] == "continue"
    assert out["reason"] == "intent only"
    assert out["evidence_present"] is False


def test_reject_early_stop_emits_goal_judge_before_continue() -> None:
    from metateam.runtime.agent import Agent

    class _Fake:
        def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
            return '{"state": "continue", "reason": "lookup missing"}'

    class Stub:
        def __init__(self) -> None:
            self.messages = [{"role": "user", "content": "查一下最新记录"}]
            self.compress_llm = _Fake()
            self._disable_tools = False
            self._goal_satisfied = None
            self.events: list[tuple[str, dict]] = []

        def _emit(self, name: str, data: dict) -> None:
            self.events.append((name, data))

    stub = Stub()
    keep, nudges = Agent._reject_early_stop(
        stub,
        "我将调用插件查询。",
        loop_start=0,
        hold_for_goal=True,
        goal_nudges=0,
        emit_assistant_text=False,
    )
    assert keep is True
    assert nudges == 1
    kinds = [n for n, _ in stub.events]
    assert "goal_judge" in kinds
    judge = next(d for n, d in stub.events if n == "goal_judge")
    assert judge["state"] == "continue"
    assert judge["reason"] == "lookup missing"
    assert judge["evidence_present"] is False
    status = next(d for n, d in stub.events if n == "assistant_status")
    assert "未完成" in status["text"]
    assert "lookup missing" in status["text"]
    assert stub.messages[-1]["sidekick"]["kind"] == "goal_continue"
    assert "Judge reason: lookup missing" in stub.messages[-1]["content"]
