from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import metateam.services.scheduler as sched
from metateam.services.scheduler import SCHEDULES


@pytest.fixture
def mgr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fresh ScheduleManager with persistence redirected to tmp_path."""
    monkeypatch.setattr(
        sched,
        "tenant_schedules_path",
        lambda user_id=None: tmp_path / "tenants" / (user_id or "default") / "schedules.json",
    )
    monkeypatch.setattr(sched, "tenants_root", lambda: tmp_path / "tenants")
    SCHEDULES._tasks.clear()
    yield SCHEDULES
    SCHEDULES._tasks.clear()


def _future_at(hours: float = 2) -> float:
    return time.time() + hours * 3600


def _mk(mgr, **overrides):
    kwargs = dict(
        user_id="u1",
        session_id="s1",
        title="每日提醒",
        prompt="提醒喝水",
        kind="daily",
        time_str="09:00",
    )
    kwargs.update(overrides)
    return mgr.create(**kwargs)


def test_create_and_list_roundtrip(mgr) -> None:
    task = _mk(mgr)
    assert task.id.startswith("sch_")
    assert task.enabled and task.next_run > time.time()
    listed = mgr.list("u1")
    assert [t.id for t in listed] == [task.id]
    d = task.to_dict()
    assert d["schedule_text"] == "每天 09:00"


def test_tenant_isolation(mgr) -> None:
    task = _mk(mgr, user_id="u1")
    assert mgr.get(task.id, "u2") is None
    assert mgr.list("u2") == []
    assert mgr.delete(task.id, "u2") is False
    assert mgr.get(task.id, "u1") is not None


def test_per_user_limit(mgr, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "LIMIT_PER_USER", 5)
    for i in range(5):
        _mk(mgr, user_id="u2", title=f"t{i}", kind="once", at=_future_at(hours=1 + i))
    with pytest.raises(ValueError):
        _mk(mgr, user_id="u2", kind="once", at=_future_at(hours=9))
    # other users unaffected
    _mk(mgr, user_id="u3", kind="once", at=_future_at(hours=9))
    assert len(mgr.list("u3")) == 1


def test_persist_and_reload(mgr) -> None:
    _mk(mgr, user_id="u1")
    _mk(mgr, user_id="u1", kind="once", at=_future_at(hours=3), title="一次性")

    persisted = sched.tenant_schedules_path("u1")
    data = json.loads(persisted.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["tasks"]) == 2

    # simulate restart
    SCHEDULES._tasks.clear()
    SCHEDULES.load_all()
    assert len(SCHEDULES.list("u1")) == 2


def test_load_all_catchup_and_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tenants = tmp_path / "tenants"
    (tenants / "u1").mkdir(parents=True)
    now = time.time()
    catch_up = dict(
        id="sch_catch", user_id="u1", session_id="s", title="t", prompt="p",
        kind="once", at=now - 1800, enabled=True, next_run=now - 1800,
    )
    expired = dict(
        id="sch_old", user_id="u1", session_id="s", title="t", prompt="p",
        kind="once", at=now - 3 * 86400, enabled=True, next_run=now - 3 * 86400,
    )
    recurring = dict(
        id="sch_rec", user_id="u1", session_id="s", title="t", prompt="p",
        kind="daily", time="09:00", enabled=True, next_run=now - 86400,
    )
    (tenants / "u1" / "schedules.json").write_text(
        json.dumps({"version": 1, "tasks": [catch_up, expired, recurring]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "tenant_schedules_path", lambda user_id=None: tenants / "u1" / "schedules.json")
    monkeypatch.setattr(sched, "tenants_root", lambda: tenants)
    SCHEDULES._tasks.clear()
    try:
        SCHEDULES.load_all()
        catch = SCHEDULES.get("sch_catch", "u1")
        assert catch is not None and catch.enabled and catch.next_run > time.time()
        old = SCHEDULES.get("sch_old", "u1")
        assert old is not None and not old.enabled and old.last_status == "missed"
        rec = SCHEDULES.get("sch_rec", "u1")
        assert rec is not None and rec.enabled and rec.next_run > time.time()
    finally:
        SCHEDULES._tasks.clear()


def test_patch_recomputes_next_run(mgr) -> None:
    task = _mk(mgr, kind="daily", time_str="23:00")
    old_next = task.next_run
    updated = mgr.patch(task.id, "u1", {"time": "01:00"})
    assert updated.next_run != old_next
    assert updated.to_dict()["schedule_text"] == "每天 01:00"

    # pause then re-enable → next_run recomputed into the future
    paused = mgr.patch(task.id, "u1", {"enabled": False})
    assert not paused.enabled
    paused.next_run = time.time() - 10  # pretend it went stale while paused
    resumed = mgr.patch(task.id, "u1", {"enabled": True})
    assert resumed.enabled and resumed.next_run > time.time()

    with pytest.raises(ValueError):
        mgr.patch(task.id, "u1", {"time": "not-a-time"})


def test_pause_for_session(mgr) -> None:
    t1 = _mk(mgr, session_id="s1", kind="once", at=_future_at(hours=1))
    t2 = _mk(mgr, session_id="s2", kind="once", at=_future_at(hours=2))
    count = mgr.pause_for_session("u1", "s1")
    assert count == 1
    assert not mgr.get(t1.id, "u1").enabled
    assert mgr.get(t2.id, "u1").enabled


def test_upcoming(mgr) -> None:
    task = _mk(mgr, kind="daily", time_str="09:00")
    ups = mgr.upcoming(task, 3)
    assert len(ups) == 3
    assert ups == sorted(ups) and all(u > time.time() for u in ups)
    once = _mk(mgr, kind="once", at=_future_at(hours=1), title="once")
    assert len(mgr.upcoming(once, 3)) == 1
