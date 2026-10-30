from __future__ import annotations

import json
import time

import pytest

from metateam.services import tenant_context, user_auth
from metateam.services.user_auth import AuthUser, TOKEN_TTL_SEC


def _user() -> AuthUser:
    return AuthUser(
        id="u_test",
        username="ada",
        email="ada@example.com",
        password_hash="x",
        salt="y",
        created_at=time.time(),
    )


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(user_auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(user_auth, "TOKENS_PATH", tmp_path / "auth_tokens.json")


def _expiry(token: str) -> float:
    data = json.loads(user_auth.TOKENS_PATH.read_text(encoding="utf-8"))
    return float(data["tokens"][token]["expires_at"])


def test_use_within_30_days_slides_expiry(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    user_auth._save_users([_user()])
    token = user_auth.issue_token("u_test")
    data = json.loads(user_auth.TOKENS_PATH.read_text(encoding="utf-8"))
    data["tokens"][token]["expires_at"] = time.time() + 86400
    user_auth.TOKENS_PATH.write_text(json.dumps(data), encoding="utf-8")

    assert user_auth.resolve_token(token) == ("u_test", "ada")
    assert _expiry(token) > time.time() + TOKEN_TTL_SEC - 120


def test_fresh_token_is_not_rewritten_on_every_call(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    user_auth._save_users([_user()])
    token = user_auth.issue_token("u_test")
    before = _expiry(token)
    assert user_auth.resolve_token(token) == ("u_test", "ada")
    assert _expiry(token) == before


def test_unused_for_30_days_requires_login(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    user_auth._save_users([_user()])
    token = user_auth.issue_token("u_test")
    data = json.loads(user_auth.TOKENS_PATH.read_text(encoding="utf-8"))
    data["tokens"][token]["expires_at"] = time.time() - 10
    user_auth.TOKENS_PATH.write_text(json.dumps(data), encoding="utf-8")

    assert user_auth.resolve_token(token) is None
    assert _expiry(token) < time.time()


def test_update_profile_persists_and_feeds_the_agent_block(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    user_auth._save_users([_user()])

    user_auth.update_profile(
        "u_test",
        display_name="Ada Lovelace",
        email="ADA@Example.com",
        title="Engineer",
        bio="Works on simulations",
    )

    stored = user_auth.get_user("u_test")
    assert stored is not None
    assert stored.public()["display_name"] == "Ada Lovelace"
    assert stored.email == "ada@example.com"
    assert stored.title == "Engineer"

    tenant_context.set_user("u_test", "ada")
    try:
        block = user_auth.current_user_prompt_block()
    finally:
        tenant_context.reset_user()
    assert "## Current user" in block
    assert "Ada Lovelace" in block
    assert "Works on simulations" in block


def test_update_profile_rejects_invalid_avatar(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    user_auth._save_users([_user()])

    with pytest.raises(ValueError):
        user_auth.update_profile("u_test", avatar="javascript:alert(1)")

    assert user_auth.get_user("u_test").avatar == ""
