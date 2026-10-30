from __future__ import annotations

import json

import pytest

from metateam.services import mcp_catalog
from metateam.services.mcp_config import load_mcp_config


def test_catalog_lists_verified_packages(data_dir):
    cat = mcp_catalog.list_catalog()
    ids = {i["id"] for i in cat["items"]}
    assert {
        "filesystem",
        "memory",
        "sequential-thinking",
        "time",
        "fetch",
        "git",
        "sqlite",
        "brave-search",
    } <= ids
    by_id = {i["id"]: i for i in cat["items"]}
    assert by_id["filesystem"]["package"] == "@modelcontextprotocol/server-filesystem"
    assert by_id["time"]["command"] == "uvx" and by_id["time"]["package"] == "mcp-server-time"
    assert by_id["fetch"]["requires_network"] is True
    assert by_id["brave-search"]["requires_api_key"] is True
    assert by_id["brave-search"]["env_placeholders"] == ["BRAVE_API_KEY"]
    assert by_id["sqlite"]["package"] == "mcp-server-sqlite"
    assert by_id["sqlite"]["command"] == "uvx"
    assert by_id["git"]["args"][-1] in ("",) or by_id["git"]["args"][-1] != "."
    assert "--repository" in by_id["git"]["args"]
    assert all(i["installed"] is False for i in cat["items"])


def test_install_and_uninstall_idempotent(data_dir):
    res = mcp_catalog.install_from_catalog("memory")
    assert res["status"] == "ok"
    setup = load_mcp_config()
    assert any(s.id == "memory" for s in setup.servers)
    assert any("@modelcontextprotocol/server-memory" in (s.args or []) for s in setup.servers)

    again = mcp_catalog.install_from_catalog("memory")
    assert again["status"] == "already_installed"
    assert len(load_mcp_config().servers) == 1

    # Custom args + env for brave-search
    res2 = mcp_catalog.install_from_catalog(
        "brave-search",
        env={"BRAVE_API_KEY": "secret-key"},
    )
    assert res2["status"] == "ok"
    brave = next(s for s in load_mcp_config().servers if s.id == "brave-search")
    assert brave.env.get("BRAVE_API_KEY") == "secret-key"
    assert "@brave/brave-search-mcp-server" in brave.args

    gone = mcp_catalog.uninstall_from_catalog("memory")
    assert gone["status"] == "ok"
    assert not any(s.id == "memory" for s in load_mcp_config().servers)

    missing = mcp_catalog.uninstall_from_catalog("memory")
    assert missing["status"] == "not_installed"


def test_install_unknown_raises(data_dir):
    with pytest.raises(ValueError, match="没有"):
        mcp_catalog.install_from_catalog("no-such-server")


def test_catalog_http_routes(data_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from metateam.api.routes import system as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    cat = client.get("/api/mcp/catalog").json()
    assert len(cat["items"]) >= 8
    assert any(i["id"] == "filesystem" for i in cat["items"])

    r = client.post("/api/mcp/catalog/install", json={"id": "filesystem", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert any(s["id"] == "filesystem" for s in body["servers"])
    fs = next(s for s in body["servers"] if s["id"] == "filesystem")
    assert fs["args"][-1] == "/tmp"

    r2 = client.post("/api/mcp/catalog/install", json={"id": "filesystem"})
    assert r2.json()["status"] == "already_installed"

    r3 = client.post("/api/mcp/catalog/uninstall", json={"id": "filesystem"})
    assert r3.status_code == 200 and r3.json()["status"] == "ok"

    assert client.post("/api/mcp/catalog/install", json={"id": "nope"}).status_code == 404

    # mcp.json written under tmp tenant dir
    setup = load_mcp_config()
    assert all(s.id != "filesystem" for s in setup.servers)


def test_install_git_rejects_dot_repo(data_dir):
    with pytest.raises(ValueError, match="Git"):
        mcp_catalog.install_from_catalog(
            "git",
            args=["mcp-server-git", "--repository", "."],
        )
    with pytest.raises(ValueError, match="Git"):
        mcp_catalog.install_from_catalog("git")  # catalog default has empty path


def test_install_git_accepts_real_path(data_dir, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    res = mcp_catalog.install_from_catalog(
        "git",
        args=["mcp-server-git", "--repository", str(repo)],
    )
    assert res["status"] == "ok"
    setup = load_mcp_config()
    git = next(s for s in setup.servers if s.id == "git")
    assert git.args[-1] == str(repo)


def test_install_sqlite_uvx_package(data_dir, tmp_path):
    db = tmp_path / "app.db"
    db.write_text("")
    res = mcp_catalog.install_from_catalog(
        "sqlite",
        args=["mcp-server-sqlite", "--db-path", str(db)],
    )
    assert res["status"] == "ok"
    setup = load_mcp_config()
    sqlite = next(s for s in setup.servers if s.id == "sqlite")
    assert "mcp-server-sqlite" in sqlite.args
    assert "--db-path" in sqlite.args
