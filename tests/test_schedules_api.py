from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import metateam.services.scheduler as sched
from metateam.api.app import app
from metateam.services.scheduler import SCHEDULES


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("metateam.api.middleware.peer_is_loopback", lambda host: True)
    monkeypatch.setattr(
        "metateam.api.middleware.resolve_token",
        lambda token: ("default", "test"),
    )
    monkeypatch.setattr(
        sched, "tenant_schedules_path", lambda user_id=None: tmp_path / "schedules.json"
    )
    SCHEDULES._tasks.clear()
    with TestClient(app) as c:
        yield c
    SCHEDULES._tasks.clear()


def _make_session(client: TestClient) -> str:
    created = client.post("/api/sessions")
    assert created.status_code == 200
    return created.json()["id"]


def test_schedules_crud_roundtrip(client: TestClient) -> None:
    sid = _make_session(client)
    payload = {
        "title": "每日站会",
        "prompt": "总结今日待办",
        "kind": "daily",
        "time": "09:00",
        "session_id": sid,
    }
    created = client.post("/api/schedules", json=payload)
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["id"].startswith("sch_")
    assert task["schedule_text"] == "每天 09:00"
    assert task["enabled"] is True

    listed = client.get("/api/schedules")
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()["items"]] == [task["id"]]
    assert listed.json()["enabled"] == 1

    detail = client.get(f"/api/schedules/{task['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["upcoming"]) == 3

    patched = client.patch(f"/api/schedules/{task['id']}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/schedules/{task['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/schedules/{task['id']}").status_code == 404
    client.delete(f"/api/sessions/{sid}")


def test_create_validation_errors(client: TestClient) -> None:
    sid = _make_session(client)
    # kind/param mismatch
    bad = client.post(
        "/api/schedules",
        json={"title": "t", "prompt": "p", "kind": "daily", "session_id": sid, "time": "9:00"},
    )
    assert bad.status_code == 400
    # unknown session
    missing = client.post(
        "/api/schedules",
        json={"title": "t", "prompt": "p", "kind": "daily", "time": "09:00", "session_id": "nope"},
    )
    assert missing.status_code == 404
    # pydantic: bad kind
    bad_kind = client.post(
        "/api/schedules",
        json={"title": "t", "prompt": "p", "kind": "hourly", "session_id": sid},
    )
    assert bad_kind.status_code == 422
    client.delete(f"/api/sessions/{sid}")


def test_hidden_session_anchors_schedule_home(client: TestClient) -> None:
    sid = _make_session(client)
    from metateam.services.store import STORE

    sess = STORE.get(sid)
    sess.hidden = True
    created = client.post(
        "/api/schedules",
        json={"title": "t", "prompt": "p", "kind": "daily", "time": "09:00", "session_id": sid},
    )
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["session_id"].startswith("schedhome_")
    assert task["session_id"] != sid
    home = STORE.get(task["session_id"])
    assert home is not None
    assert home.hidden is False
    client.delete(f"/api/schedules/{task['id']}")
    client.delete(f"/api/sessions/{sid}")
    client.delete(f"/api/sessions/{task['session_id']}")
