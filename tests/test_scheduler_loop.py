from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

import metateam.services.scheduler as sched
import metateam.services.scheduler_loop as loop_mod
from metateam.services.scheduler import SCHEDULES
from metateam.services.scheduler_loop import SCHEDULER, SchedulerLoop


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(
        sched, "tenant_schedules_path", lambda user_id=None: tmp_path / "schedules.json"
    )
    SCHEDULES._tasks.clear()
    yield
    SCHEDULES._tasks.clear()


class FakeBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeAgent:
    def __init__(self, fail: bool = False) -> None:
        self.bus = FakeBus()
        self.agent_id = "agent_x"
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def run(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(text="done", cancelled=False, iterations=1, messages=[], review=None)


class FakeSession:
    def __init__(self, busy: bool = False, fail: bool = False) -> None:
        self.id = "sess_x"
        self.title = "T"
        self.busy = busy
        self.stop_requested = False
        self.agent = FakeAgent(fail=fail)
        self.pending_job_notices: list[dict[str, Any]] = []


def _mk_task(**overrides):
    kwargs = dict(
        user_id="u1",
        session_id="sess_x",
        title="任务A",
        prompt="做点事",
        kind="once",
        at=time.time() + 3600,
    )
    kwargs.update(overrides)
    return SCHEDULES.create(**kwargs)


def _make_due(task) -> None:
    with SCHEDULES._lock:
        task.next_run = time.time() - 1


def test_missing_session_marks_orphan(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task()
    _make_due(task)
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: None))
    SCHEDULER._run_task(task)
    fresh = SCHEDULES.get(task.id, "u1")
    assert not fresh.enabled
    assert fresh.last_status == "orphan"


def test_busy_once_defers_and_queues_notice(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task()
    _make_due(task)
    sess = FakeSession(busy=True)
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: sess))
    SCHEDULER._run_task(task)
    fresh = SCHEDULES.get(task.id, "u1")
    assert fresh.enabled
    assert fresh.defer_count == 1
    assert fresh.next_run > time.time()
    assert len(sess.pending_job_notices) == 1
    assert sess.pending_job_notices[0]["sidekick"]["kind"] == "schedule_notice"
    assert not sess.agent.calls  # agent never ran


def test_busy_recurring_advances(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task(kind="daily", time_str="09:00")
    _make_due(task)
    sess = FakeSession(busy=True)
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: sess))
    SCHEDULER._run_task(task)
    fresh = SCHEDULES.get(task.id, "u1")
    assert fresh.enabled and fresh.next_run > time.time()
    assert fresh.run_count == 0


def test_normal_once_run_executes_and_disables(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task()
    _make_due(task)
    sess = FakeSession()
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: sess))
    SCHEDULER._run_task(task)
    assert len(sess.agent.calls) == 1
    call = sess.agent.calls[0]
    assert call["prompt"] == "做点事"
    assert call["display"] == "定时任务 · 任务A"
    assert sess.busy is False
    fresh = SCHEDULES.get(task.id, "u1")
    assert not fresh.enabled
    assert fresh.last_status == "ok"
    assert fresh.run_count == 1
    types = [e.type for e in sess.agent.bus.events]
    assert types.count("schedule_trigger") == 1
    assert types.count("final") == 1


def test_failure_marks_error(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task()
    _make_due(task)
    sess = FakeSession(fail=True)
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: sess))
    SCHEDULER._run_task(task)
    fresh = SCHEDULES.get(task.id, "u1")
    assert fresh.last_status == "error"
    assert "boom" in fresh.last_error
    assert sess.busy is False


def test_tick_respects_max_parallel(env, monkeypatch: pytest.MonkeyPatch) -> None:
    task = _mk_task()
    _make_due(task)
    monkeypatch.setattr(loop_mod, "STORE", SimpleNamespace(get=lambda sid: FakeSession()))
    monkeypatch.setattr(SchedulerLoop, "MAX_PARALLEL", 0, raising=False)
    loop = SchedulerLoop()
    loop._tick()
    fresh = SCHEDULES.get(task.id, "u1")
    # deferred because capacity is 0
    assert fresh.next_run > time.time()
    assert fresh.id not in loop._inflight
