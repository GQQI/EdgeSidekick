"""Forced continuation follows observable progress, not a magic count."""

from __future__ import annotations

import json
from types import SimpleNamespace

from metateam.runtime.agent import Agent
from metateam.runtime.evidence import EvidenceLedger


class _Judge:
    def __init__(self, state: str | None) -> None:
        self.state = state
        self.calls = 0

    def complete_text(self, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls += 1
        if self.state is None:
            return "not json"
        return json.dumps({"state": self.state, "reason": "still missing"})


def _agent(state: str | None, evidence: list[tuple[str, str]] | None = None):
    ledger = EvidenceLedger()
    for name, content in evidence or []:
        ledger.record(name, content)
    emitted: list[tuple[str, dict]] = []
    fake = SimpleNamespace(
        messages=[
            {"role": "user", "content": "修复页面图片不显示的问题"},
            {"role": "assistant", "content": "请查看截图确认。"},
        ],
        compress_llm=_Judge(state),
        _evidence=ledger,
        _goal_satisfied=None,
        _goal_evidence_count=ledger.count(),
        _disable_tools=False,
        _emit=lambda name, data: emitted.append((name, data)),
    )
    return fake, ledger, emitted


def _reject(fake, *, nudges: int, candidate: str = "请查看截图确认。") -> tuple[bool, int]:
    return Agent._reject_early_stop(
        fake,
        candidate,
        loop_start=0,
        hold_for_goal=True,
        goal_nudges=nudges,
        emit_assistant_text=False,
    )


def test_a_continuation_without_new_evidence_stops_unresolved() -> None:
    fake, _ledger, events = _agent("continue", [("read_file", "image src exists")])
    assert _reject(fake, nudges=0) == (True, 1)
    assert fake._goal_satisfied is None

    allowed, nudges = _reject(fake, nudges=1)
    assert allowed is False
    assert nudges == 1
    # A stall is not a finished goal.
    assert fake._goal_satisfied is False
    name, data = events[-1]
    assert name == "goal_settled"
    assert data.get("satisfied") is False
    assert data.get("blocked") is False
    assert data.get("stalled") is True


def test_new_evidence_keeps_a_long_task_going_beyond_the_old_cap() -> None:
    fake, ledger, _events = _agent("continue", [("read_file", "v1")])
    nudges = 0
    for i in range(5):
        allowed, nudges = _reject(fake, nudges=nudges)
        assert allowed is True
        ledger.record("read_file", f"v{i + 2}")
    assert nudges == 5


def test_an_unreadable_verdict_stops_without_claiming_done() -> None:
    fake, _ledger, _events = _agent(None, [("read_file", "v1")])
    allowed, _nudges = _reject(fake, nudges=0)
    assert allowed is False
    assert fake._goal_satisfied is None


def test_a_done_verdict_settles_the_goal() -> None:
    fake, _ledger, events = _agent("done", [("read_file", "v1")])
    allowed, _nudges = _reject(fake, nudges=0)
    assert allowed is False
    assert fake._goal_satisfied is True
    name, data = events[-1]
    assert name == "goal_settled"
    assert data.get("satisfied") is True
    assert data.get("blocked") is False
    assert data.get("state") == "done"


def test_a_blocked_verdict_leaves_the_goal_open_for_the_user() -> None:
    fake, _ledger, events = _agent("blocked", [("read_file", "v1")])
    allowed, _nudges = _reject(fake, nudges=0)
    assert allowed is False
    assert fake._goal_satisfied is False
    name, data = events[-1]
    assert name == "goal_settled"
    assert data.get("satisfied") is False
    assert data.get("blocked") is True
    assert data.get("state") == "blocked"
