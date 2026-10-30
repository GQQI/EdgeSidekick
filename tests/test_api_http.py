from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metateam.api.app import app
from metateam.services.memory import read_memory
from metateam.services.tenant_context import (
    ensure_tenant_knowledge,
    reset_user,
    set_user,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("metateam.api.middleware.peer_is_loopback", lambda host: True)
    monkeypatch.setattr(
        "metateam.api.middleware.resolve_token",
        lambda token: ("default", "test"),
    )
    return TestClient(app)


def test_cors_loopback_preflight(client: TestClient) -> None:
    r = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5177",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5177"


def test_unknown_api_path_is_json_404_not_spa_html(client: TestClient) -> None:
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body
    ctype = (r.headers.get("content-type") or "").lower()
    assert "json" in ctype
    assert "<html" not in r.text.lower()


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "workspace" in body


def test_create_and_get_session(client: TestClient) -> None:
    created = client.post("/api/sessions")
    assert created.status_code == 200
    sid = created.json()["id"]
    got = client.get(f"/api/sessions/{sid}")
    assert got.status_code == 200
    assert got.json()["id"] == sid
    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    client.delete(f"/api/sessions/{sid}")


def test_git_panel_ok(client: TestClient) -> None:
    r = client.get("/api/git")
    assert r.status_code == 200
    body = r.json()
    assert "is_repo" in body
    assert "files" in body


def test_memory_roundtrip_tenant_scoped(client: TestClient) -> None:
    try:
        set_user("default")
        _skills, mem = ensure_tenant_knowledge("default")
        before = read_memory(mem, max_chars=50_000)
        payload = (before + "\n\n- integration-test-marker\n").strip()
        r = client.put("/api/memory", json={"content": payload})
        assert r.status_code == 200
        got = client.get("/api/memory")
        assert got.status_code == 200
        assert "integration-test-marker" in got.json().get("content", "")
        client.put("/api/memory", json={"content": before or "# MEMORY\n"})
    finally:
        reset_user()


def test_register_open_after_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metateam.api.middleware.peer_is_loopback", lambda host: True)
    monkeypatch.setattr("metateam.api.http.peer_is_loopback", lambda host: True)
    seen: dict[str, str] = {}

    class FakeUser:
        def public(self) -> dict[str, str]:
            return {"id": "u_test", "username": "newuser"}

    def fake_register(username: str, password: str, *, email: str) -> tuple[FakeUser, str]:
        seen.update(username=username, email=email)
        return FakeUser(), "test-token"

    monkeypatch.setattr("metateam.api.routes.auth.register_account", fake_register)
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"username": "newuser", "email": "new@example.com", "password": "secret1"},
    )
    assert r.status_code == 200
    assert r.json()["token"] == "test-token"
    assert seen == {"username": "newuser", "email": "new@example.com"}


def test_create_user_requires_header_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metateam.api.middleware.peer_is_loopback", lambda host: True)
    monkeypatch.setattr(
        "metateam.api.middleware.resolve_token",
        lambda token: ("default", "test") if token else None,
    )
    client = TestClient(app)
    r = client.post(
        "/api/auth/users",
        json={"username": "newuser", "email": "new@example.com", "password": "secret1"},
    )
    assert r.status_code == 401
    denied = client.get("/api/sessions?token=leaked")
    assert denied.status_code == 401
    ok = client.get("/api/sessions", headers={"X-Sidekick-Token": "ok"})
    assert ok.status_code == 200


def test_skills_validate_and_create(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from metateam.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "skills_dir", tmp_path)
    bad = client.post("/api/skills/validate", json={"markdown": "no frontmatter"})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    created = client.post(
        "/api/skills",
        json={
            "name": "http-demo",
            "description": "Use when testing the skills HTTP API.",
            "content": "# Demo\n\n## Steps\n1. Validate\n2. Save\n",
            "overwrite": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["skill"]["name"] == "http-demo"
    listed = client.get("/api/skills")
    assert listed.status_code == 200
    assert any(x["name"] == "http-demo" for x in listed.json())
    gone = client.delete("/api/skills/http-demo")
    assert gone.status_code == 200
