from __future__ import annotations

import pytest


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Point tenant storage and the secret key at a throwaway folder."""
    from metateam.services import secret_store, tenant_context

    monkeypatch.setattr(tenant_context, "ROOT", tmp_path)
    monkeypatch.setattr(secret_store, "SECRET_PATH", tmp_path / "data" / ".secret_key")
    monkeypatch.setattr(secret_store, "_fernet", None)
    tenant_context.reset_user()
    return tmp_path
