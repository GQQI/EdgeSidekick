"""Account creation follows the shared Sidekick backend contract."""

from __future__ import annotations


def test_setup_then_admin_creates_user(data_dir, monkeypatch):
    from metateam.services import user_auth

    monkeypatch.setattr(user_auth, "ROOT", data_dir)
    monkeypatch.setattr(user_auth, "USERS_PATH", data_dir / "data" / "users.json")
    monkeypatch.setattr(user_auth, "TOKENS_PATH", data_dir / "data" / "auth_tokens.json")

    first, token1 = user_auth.setup_admin("管理员", "secret1", email="admin@example.com")
    assert first.email == "admin@example.com"
    assert user_auth.auth_status()["needs_setup"] is False
    assert token1

    second = user_auth.create_user("小明", "secret2", email="ming@example.com")
    assert second.email == "ming@example.com"
    assert second.id != first.id
    assert user_auth.login(email="ming@example.com", password="secret2")[0].id == second.id


def test_create_user_rejects_duplicate_email(data_dir, monkeypatch):
    from metateam.services import user_auth

    monkeypatch.setattr(user_auth, "ROOT", data_dir)
    monkeypatch.setattr(user_auth, "USERS_PATH", data_dir / "data" / "users.json")
    monkeypatch.setattr(user_auth, "TOKENS_PATH", data_dir / "data" / "auth_tokens.json")

    user_auth.setup_admin("管理员", "secret1", email="admin@example.com")
    try:
        user_auth.create_user("另一个", "secret2", email="admin@example.com")
    except ValueError as exc:
        assert str(exc) == "email already registered"
    else:
        raise AssertionError("duplicate email was accepted")
