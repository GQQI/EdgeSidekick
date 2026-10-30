"""File/Git panel calls follow a per-request `workspace` override.

Several chats may be pinned to different folders at once; the side panels
must be able to preview any of those folders without touching the tenant's
single "default" workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metateam.api.http import resolve_panel_workspace, workspace_override
from metateam.services import fs_api, workspace_store


def test_workspace_override_binds_and_resets(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "marker.txt").write_text("hi", encoding="utf-8")

    before = fs_api.workspace_root()
    with workspace_override(str(other)):
        assert fs_api.workspace_root() == other.resolve()
        resolved = fs_api.safe_resolve("marker.txt")
        assert resolved.name == "marker.txt"
    assert fs_api.workspace_root() == before


def test_workspace_override_ignores_missing_folder(tmp_path: Path) -> None:
    before = fs_api.workspace_root()
    with workspace_override(str(tmp_path / "does-not-exist")):
        assert fs_api.workspace_root() == before
    assert fs_api.workspace_root() == before


def test_workspace_override_noop_when_blank() -> None:
    before = fs_api.workspace_root()
    with workspace_override(None):
        assert fs_api.workspace_root() == before
    with workspace_override(""):
        assert fs_api.workspace_root() == before


def test_resolve_panel_workspace_falls_back_to_default(tmp_path: Path) -> None:
    from metateam.core.config import get_settings

    default = Path(get_settings().workspace)
    assert resolve_panel_workspace(None) == default
    assert resolve_panel_workspace("") == default
    assert resolve_panel_workspace(str(tmp_path / "missing")) == default

    real = tmp_path / "real"
    real.mkdir()
    assert resolve_panel_workspace(str(real)) == real.resolve()


@pytest.fixture()
def isolated_workspace_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect workspace.json to a scratch file so tests never touch real user data."""
    state_file = tmp_path / "workspace.json"
    monkeypatch.setattr(workspace_store, "tenant_workspace_path", lambda uid=None: state_file)
    return state_file


def test_forget_recent_removes_folder(tmp_path: Path, isolated_workspace_state: Path) -> None:
    default_ws = tmp_path / "default-ws"
    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    default_ws.mkdir()
    a.mkdir()
    b.mkdir()

    # Mirror real usage: the tenant already has an active workspace (set at
    # first boot) before any extra folders get pinned via remember_recent.
    # Seed the isolated state file directly — avoid set_workspace() here
    # since it also mutates the process-wide Settings singleton.
    import json

    isolated_workspace_state.write_text(
        json.dumps({"path": str(default_ws.resolve()), "recent": []}),
        encoding="utf-8",
    )
    workspace_store.remember_recent(a)
    workspace_store.remember_recent(b)
    paths = {item["path"] for item in workspace_store.list_workspaces()}
    assert str(a.resolve()) in paths
    assert str(b.resolve()) in paths

    remaining = workspace_store.forget_recent(str(a))
    remaining_paths = {item["path"] for item in remaining}
    assert str(a.resolve()) not in remaining_paths
    assert str(b.resolve()) in remaining_paths


def test_forget_recent_requires_a_path(isolated_workspace_state: Path) -> None:
    with pytest.raises(ValueError):
        workspace_store.forget_recent("")


def test_each_session_keeps_its_own_folder(tmp_path: Path, isolated_workspace_state: Path) -> None:
    from metateam.services.store import STORE

    folder_a = tmp_path / "alpha"
    folder_b = tmp_path / "beta"
    folder_c = tmp_path / "gamma"
    folder_a.mkdir()
    folder_b.mkdir()
    folder_c.mkdir()
    first = STORE.create(str(folder_a))
    second = STORE.create(str(folder_b))
    try:
        workspace_store.set_workspace(str(folder_c))
        assert first.agent.settings.workspace.resolve() == folder_a.resolve()
        assert second.agent.settings.workspace.resolve() == folder_b.resolve()
        STORE.bind_workspace(first, str(folder_b))
        assert first.agent.settings.workspace.resolve() == folder_b.resolve()
        assert second.agent.settings.workspace.resolve() == folder_b.resolve()
    finally:
        with STORE._lock:
            STORE._sessions.pop(first.id, None)
            STORE._sessions.pop(second.id, None)
