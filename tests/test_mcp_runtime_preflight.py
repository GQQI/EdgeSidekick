from __future__ import annotations

from metateam.services.mcp_config import McpServerConfig
from metateam.services import mcp_runtime as rt


def setup_function():
    # Reset unhealthy state between tests
    rt._unhealthy_until.clear()
    rt._unhealthy_reason.clear()
    rt._unhealthy_announced.clear()


def test_preflight_git_dot_rewrites_to_workspace(tmp_path, monkeypatch):
    """Bare '.' becomes the active workspace; fails only if that path is not a repo."""
    monkeypatch.setattr(rt, "_workspace_root", lambda: tmp_path.resolve())
    server = McpServerConfig(
        id="git",
        name="Git",
        command="uvx",
        args=["mcp-server-git", "--repository", "."],
    )
    reason = rt._preflight_server(server)
    assert reason and ("仓库" in reason or "git" in reason.lower())

    (tmp_path / ".git").mkdir()
    assert rt._preflight_server(server) is None


def test_preflight_git_rejects_non_repo(tmp_path):
    server = McpServerConfig(
        id="git",
        name="Git",
        command="uvx",
        args=["mcp-server-git", "--repository", str(tmp_path)],
    )
    reason = rt._preflight_server(server)
    assert reason and ("仓库" in reason or "git" in reason.lower())


def test_preflight_git_ok(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / ".git").mkdir()
    server = McpServerConfig(
        id="git",
        name="Git",
        command="uvx",
        args=["mcp-server-git", "--repository", str(repo)],
    )
    assert rt._preflight_server(server) is None


def test_preflight_executeautomation_node24(monkeypatch):
    monkeypatch.setattr(rt, "_node_major", lambda: 24)
    server = McpServerConfig(
        id="sqlite",
        name="SQLite",
        command="npx",
        args=["-y", "@executeautomation/database-server", "./data.db"],
    )
    reason = rt._preflight_server(server)
    assert reason and ("Node.js 24" in reason or "Node 20" in reason)
    assert "mcp-server-sqlite" in reason


def test_unhealthy_debounce_skips_repeat(monkeypatch):
    monkeypatch.setattr(rt, "_mcp_available", lambda: True)
    monkeypatch.setattr(rt, "_node_major", lambda: 24)

    from metateam.services.mcp_config import McpSetup

    setup = McpSetup(
        servers=[
            McpServerConfig(
                id="sqlite",
                name="SQLite",
                command="npx",
                args=["-y", "@executeautomation/database-server", "./data.db"],
                enabled=True,
            )
        ]
    )
    # First pass marks unhealthy via preflight (no process spawn)
    assert rt.list_enabled_tools(setup) == []
    assert rt._is_unhealthy("sqlite")

    called = {"n": 0}

    def boom(_server):
        called["n"] += 1
        raise RuntimeError("should not launch")

    monkeypatch.setattr(rt, "_list_tools_async", boom)
    # Second pass must skip without launching
    assert rt.list_enabled_tools(setup) == []
    assert called["n"] == 0
