from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from metateam.plugins import marketplace, oauth, runner, store
from metateam.plugins.manifest import ManifestError, load_manifest, parse_manifest
from metateam.plugins.tools import register_plugin_tools
from metateam.runtime.tool_registry import READONLY_TOOLS, Tool, ToolRegistry, filter_readonly_schemas


def mock_executor(monkeypatch, handler):
    """Run plugin code in-process, answering its HTTP calls with ``handler``."""
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def execute(req, timeout):
        client = httpx.Client(transport=httpx.MockTransport(wrapped))
        try:
            return runner.execute(req, http=client)
        finally:
            client.close()

    monkeypatch.setattr(store, "executor", execute)
    return calls


def make_zip(files: dict[str, str | bytes], prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(prefix + name, content)
    return buf.getvalue()


ECHO_MANIFEST = {
    "id": "echo",
    "name": "Echo",
    "version": "1.0.0",
    "description": "test plugin",
    "credentials": [{"name": "token", "label": "Token", "type": "secret", "required": True}],
    "tools": [
        {"name": "say", "description": "echo text back", "readonly": True,
         "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
        {"name": "boom", "description": "always fails"},
        {"name": "slow", "description": "sleeps"},
    ],
}
ECHO_MAIN = '''
import time
from sidekick_plugin import ToolError

def say(params, ctx):
    print("noise on stdout must not corrupt the response")
    return {"echo": params["text"], "token_len": len(ctx.credential("token"))}

def boom(params, ctx):
    raise ToolError("nope")

def slow(params, ctx):
    time.sleep(10)

def validate_credentials(ctx):
    if ctx.credential("token") != "good":
        raise ToolError("token rejected")
    return "fine"
'''


# ── manifests ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pkg", sorted(p.name for p in marketplace.BUNDLED_DIR.iterdir() if p.is_dir()))
def test_bundled_packages_are_valid_and_implement_every_tool(pkg):
    path = marketplace.BUNDLED_DIR / pkg
    manifest = load_manifest(path)
    mod = runner._load_entry(path, manifest.entry, manifest.id)
    for tool in manifest.tools:
        assert callable(getattr(mod, tool.name, None)), f"{pkg}.{tool.name} not implemented"
        assert len(manifest.tool_name(tool.name)) <= 64
    assert callable(getattr(mod, "validate_credentials", None))
    assert (path / "README.md").is_file() and (path / manifest.icon).is_file()


def test_manifest_rejects_bad_input():
    base = json.loads(json.dumps(ECHO_MANIFEST))
    for mutate, needle in [
        (lambda m: m.update(id="Bad Id"), "id"),
        (lambda m: m.update(tools=[]), "at least one tool"),
        (lambda m: m["tools"][0].update(description=""), "description"),
        (lambda m: m["tools"][0].update(name="Say"), "snake_case"),
        (lambda m: m.update(entry="../evil.py"), "entry"),
        (lambda m: m["credentials"][0].update(type="password"), "type"),
        (lambda m: m["tools"][0]["parameters"].update(required=["missing"]), "required"),
        (lambda m: m.update(oauth={"authorize_url": "https://a", "token_url": "https://b", "token_field": "nope"}), "undeclared"),
    ]:
        data = json.loads(json.dumps(base))
        mutate(data)
        with pytest.raises(ManifestError, match=needle):
            parse_manifest(data)


# ── install / credentials / registry ─────────────────────────────────────


def test_install_configure_and_register_tools(data_dir, monkeypatch):
    plugin = marketplace.install_from_marketplace("github")
    assert plugin.enabled and plugin.state["source"] == "marketplace"
    assert store.credential_status(plugin) == {
        "configured": False,
        "missing": ["token"],
        "fields": {"token": {"set": False}, "api_base": {"set": False, "value": ""}},
    }

    reg = ToolRegistry()
    assert register_plugin_tools(reg, Tool=Tool) == 0, "unconfigured plugins expose no tools"

    store.set_credentials("github", {"token": "ghp_secret_value"})
    raw_state = store.state_path().read_text(encoding="utf-8")
    assert "ghp_secret_value" not in raw_state and "enc:" in raw_state

    assert register_plugin_tools(reg, Tool=Tool) == 9
    search = reg.get("plugin_github_search_issues")
    create = reg.get("plugin_github_create_issue")
    assert search and search.readonly and not search.requires_approval
    assert create and create.requires_approval and not create.readonly
    assert "[插件:GitHub]" in search.description

    names = reg.readonly_names()
    assert "plugin_github_search_issues" in names and "plugin_github_create_issue" not in names
    assert READONLY_TOOLS <= names
    kept = {s["function"]["name"] for s in filter_readonly_schemas(reg.schemas(), names)}
    assert "plugin_github_get_pull_request" in kept and "plugin_github_comment_issue" not in kept

    store.set_enabled("github", False)
    assert register_plugin_tools(reg, Tool=Tool) == 0
    assert not any(n.startswith("plugin_") for n in reg.names()), "disabling drops the tools on refresh"


def test_empty_secret_keeps_value_and_clear_removes_it(data_dir):
    marketplace.install_from_marketplace("gitlab")
    store.set_credentials("gitlab", {"token": "glpat-1", "base_url": "https://git.example.com"})
    store.set_credentials("gitlab", {"token": ""})
    plugin = store.get_installed("gitlab")
    assert store.credentials_for(plugin) == {"token": "glpat-1", "base_url": "https://git.example.com"}
    store.set_credentials("gitlab", {}, clear=["token"])
    assert store.credential_status(store.get_installed("gitlab"))["missing"] == ["token"]
    with pytest.raises(store.PluginError, match="未知凭据"):
        store.set_credentials("gitlab", {"nope": "x"})


def test_zip_install_upgrade_and_safety(data_dir):
    v1 = make_zip({"manifest.json": json.dumps(ECHO_MANIFEST), "main.py": ECHO_MAIN}, prefix="echo-1.0.0/")
    plugin = store.install_from_zip(v1, source="upload")
    assert plugin.id == "echo" and plugin.manifest.version == "1.0.0"
    store.set_credentials("echo", {"token": "good"})

    upgraded = dict(ECHO_MANIFEST, version="1.1.0")
    plugin = store.install_from_zip(make_zip({"manifest.json": json.dumps(upgraded), "main.py": ECHO_MAIN}), source="upload")
    assert plugin.manifest.version == "1.1.0"
    assert store.credentials_for(plugin)["token"] == "good", "upgrade keeps credentials"
    assert sorted(p.name for p in store.packages_dir().iterdir()) == ["echo"], "no staging leftovers"

    with pytest.raises(store.PluginError, match="非法路径"):
        store.install_from_zip(make_zip({"manifest.json": json.dumps(ECHO_MANIFEST), "main.py": "", "../../evil.py": "x"}), source="upload")
    with pytest.raises(store.PluginError, match="manifest.json"):
        store.install_from_zip(make_zip({"readme.txt": "hi"}), source="upload")
    with pytest.raises(store.PluginError, match="清单无效"):
        store.install_from_zip(make_zip({"manifest.json": "{}", "main.py": ""}), source="upload")
    with pytest.raises(store.PluginError, match="zip"):
        store.install_from_zip(b"not a zip", source="upload")

    store.uninstall("echo")
    assert store.list_installed() == []
    assert not (store.packages_dir() / "echo").exists()


# ── out-of-process runner ────────────────────────────────────────────────


def test_subprocess_runner_end_to_end(data_dir):
    store.install_from_zip(make_zip({"manifest.json": json.dumps(dict(ECHO_MANIFEST, timeout=3)), "main.py": ECHO_MAIN}), source="upload")
    res = store.invoke_tool("echo", "say", {"text": "hi"})
    assert res["ok"] is False and "缺少凭据" in res["error"]

    store.set_credentials("echo", {"token": "good"})
    res = store.invoke_tool("echo", "say", {"text": "你好"})
    assert res["ok"], res
    assert json.loads(res["result"]) == {"echo": "你好", "token_len": 4}

    assert store.invoke_tool("echo", "boom", {}) == {"ok": False, "error": "nope"}
    assert store.verify_credentials("echo") == {"ok": True, "result": "fine"}
    store.set_credentials("echo", {"token": "bad"})
    assert store.verify_credentials("echo") == {"ok": False, "error": "token rejected"}

    slow = store.invoke_tool("echo", "slow", {})
    assert slow["ok"] is False and "超时" in slow["error"]


def test_child_env_does_not_leak_model_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:1")
    env = store._child_env()
    assert "OPENAI_API_KEY" not in env and env["HTTPS_PROXY"] == "http://proxy:1"


# ── bundled plugins against mocked APIs ──────────────────────────────────


def test_github_tools(data_dir, monkeypatch):
    marketplace.install_from_marketplace("github")
    store.set_credentials("github", {"token": "ghp_x"})

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer ghp_x"
        if req.url.path == "/search/issues":
            assert req.url.params["q"] == "repo:a/b is:open"
            return httpx.Response(200, json={"total_count": 1, "items": [
                {"number": 7, "title": "Crash", "state": "open", "user": {"login": "ann"},
                 "labels": [{"name": "bug"}], "comments": 2, "html_url": "https://github.com/a/b/issues/7", "body": "x" * 9000}
            ]})
        if req.url.path == "/repos/a/b/pulls/3":
            return httpx.Response(200, json={"number": 3, "title": "Fix", "state": "open", "user": {"login": "bo"},
                                             "head": {"ref": "fix"}, "base": {"ref": "main"}, "body": "desc"})
        if req.url.path == "/repos/a/b/pulls/3/files":
            return httpx.Response(200, json=[{"filename": "app.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@"}])
        if req.url.path == "/repos/a/b/contents/README.md":
            return httpx.Response(200, json={"path": "README.md", "encoding": "base64", "size": 5,
                                             "content": base64.b64encode("hello".encode()).decode()})
        if req.url.path == "/repos/a/b/issues" and req.method == "POST":
            assert json.loads(req.content) == {"title": "T", "body": "B", "labels": ["bug"]}
            return httpx.Response(201, json={"number": 8, "html_url": "u", "title": "T"})
        if req.url.path == "/user":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(404, json={"message": "Not Found"})

    mock_executor(monkeypatch, handler)
    res = store.invoke_tool("github", "search_issues", {"query": "repo:a/b is:open"})
    rows = json.loads(res["result"])["items"]
    assert rows == [{"number": 7, "title": "Crash", "state": "open", "type": "issue", "author": "ann",
                     "labels": ["bug"], "comments": 2, "updated_at": None, "url": "https://github.com/a/b/issues/7"}]

    pr = json.loads(store.invoke_tool("github", "get_pull_request", {"repo": "https://github.com/a/b.git", "number": 3})["result"])
    assert pr["files"][0] == {"path": "app.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@"}

    f = json.loads(store.invoke_tool("github", "get_file", {"repo": "a/b", "path": "README.md"})["result"])
    assert f["content"] == "hello"

    created = json.loads(store.invoke_tool("github", "create_issue", {"repo": "a/b", "title": "T", "body": "B", "labels": ["bug"]})["result"])
    assert created["number"] == 8

    bad = store.verify_credentials("github")
    assert bad["ok"] is False and "HTTP 401" in bad["error"] and "凭据无效" in bad["error"] and "Bad credentials" in bad["error"]

    assert "owner/name" in store.invoke_tool("github", "get_issue", {"repo": "not a repo", "number": 1})["error"]


def test_gitlab_tools_encode_project_paths(data_dir, monkeypatch):
    marketplace.install_from_marketplace("gitlab")
    store.set_credentials("gitlab", {"token": "glpat-x", "base_url": "https://git.example.com/"})

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "git.example.com" and req.headers["private-token"] == "glpat-x"
        path = req.url.raw_path.decode()
        if path.startswith("/api/v4/projects/grp%2Fsub%2Fapp/merge_requests/5/diffs"):
            return httpx.Response(200, json=[{"new_path": "a.py", "new_file": True, "diff": "+x"}])
        if path.startswith("/api/v4/projects/grp%2Fsub%2Fapp/merge_requests/5"):
            return httpx.Response(200, json={"iid": 5, "title": "MR", "state": "opened", "author": {"username": "li"},
                                             "source_branch": "f", "target_branch": "main", "head_pipeline": {"status": "success"}})
        if path.startswith("/api/v4/issues"):
            assert req.url.params["scope"] == "all"
            return httpx.Response(200, json=[{"iid": 1, "title": "I", "state": "opened", "author": {"username": "li"}, "labels": ["x"]}])
        return httpx.Response(404, json={"message": "404 Not Found"})

    mock_executor(monkeypatch, handler)
    mr = json.loads(store.invoke_tool("gitlab", "get_merge_request", {"project": "grp/sub/app", "iid": 5})["result"])
    assert mr["pipeline"] == "success" and mr["files"] == [{"path": "a.py", "status": "added", "diff": "+x"}]
    issues = json.loads(store.invoke_tool("gitlab", "list_issues", {})["result"])
    assert issues[0]["iid"] == 1


def test_gmail_refreshes_token_and_composes_replies(data_dir, monkeypatch):
    marketplace.install_from_marketplace("gmail")
    store.set_credentials("gmail", {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"})
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com":
            form = parse_qs(req.content.decode())
            assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt"]
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer at"
        path = req.url.path.removeprefix("/gmail/v1/users/me")
        if path == "/messages" and req.method == "GET":
            return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
        if path == "/messages/m1":
            return httpx.Response(200, json={
                "id": "m1", "threadId": "t1", "snippet": "Hi &amp; hello",
                "payload": {"headers": [{"name": "From", "value": "a@x.com"}, {"name": "Subject", "value": "Plan"},
                                        {"name": "Message-ID", "value": "<abc@x>"}],
                            "mimeType": "text/html",
                            "body": {"data": base64.urlsafe_b64encode(b"<p>Hello<br>team</p>").decode()}},
            })
        if path == "/messages/send":
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "m2", "threadId": "t1"})
        return httpx.Response(404, json={"error": {"message": "nf"}})

    mock_executor(monkeypatch, handler)
    found = json.loads(store.invoke_tool("gmail", "search_messages", {"query": "is:unread"})["result"])
    assert found["messages"][0]["snippet"] == "Hi & hello" and found["messages"][0]["subject"] == "Plan"
    body = json.loads(store.invoke_tool("gmail", "read_message", {"id": "m1"})["result"])["body"]
    assert body == "Hello\nteam"

    res = store.invoke_tool("gmail", "send_message", {"to": "b@x.com", "body": "OK", "reply_to_id": "m1"})
    assert json.loads(res["result"])["status"] == "已发送"
    raw = base64.urlsafe_b64decode(sent[0]["raw"]).decode()
    assert sent[0]["threadId"] == "t1"
    assert "Subject: Re: Plan" in raw and "In-Reply-To: <abc@x>" in raw and "To: b@x.com" in raw


def test_gmail_revoked_grant_is_explained(data_dir, monkeypatch):
    marketplace.install_from_marketplace("gmail")
    store.set_credentials("gmail", {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"})
    mock_executor(monkeypatch, lambda req: httpx.Response(400, json={"error": "invalid_grant"}))
    res = store.invoke_tool("gmail", "list_labels", {})
    assert res["ok"] is False and "重新授权" in res["error"]


# ── OAuth ────────────────────────────────────────────────────────────────


def test_oauth_flow_stores_refresh_token(data_dir):
    marketplace.install_from_marketplace("gmail")
    with pytest.raises(store.PluginError, match="Client ID"):
        oauth.start("gmail", "http://127.0.0.1:8787/api/plugins/oauth/callback", "default")
    store.set_credentials("gmail", {"client_id": "cid", "client_secret": "cs"})
    started = oauth.start("gmail", "http://127.0.0.1:8787/api/plugins/oauth/callback", "default")
    q = parse_qs(urlparse(started["url"]).query)
    assert q["client_id"] == ["cid"] and q["access_type"] == ["offline"] and q["state"] == [started["state"]]
    assert q["scope"] == ["https://www.googleapis.com/auth/gmail.modify"]

    def token_endpoint(req: httpx.Request) -> httpx.Response:
        form = parse_qs(req.content.decode())
        assert form["code"] == ["the-code"] and form["redirect_uri"] == ["http://127.0.0.1:8787/api/plugins/oauth/callback"]
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "new-rt"})

    client = httpx.Client(transport=httpx.MockTransport(token_endpoint))
    assert oauth.complete(started["state"], "the-code", http=client)["plugin_id"] == "gmail"
    assert store.credentials_for(store.get_installed("gmail"))["refresh_token"] == "new-rt"
    with pytest.raises(store.PluginError, match="过期或无效"):
        oauth.complete(started["state"], "the-code", http=client)


# ── remote marketplace ───────────────────────────────────────────────────


def test_remote_marketplace_index_and_install(data_dir, monkeypatch):
    package = make_zip({"manifest.json": json.dumps(ECHO_MANIFEST), "main.py": ECHO_MAIN})
    wrong = make_zip({"manifest.json": json.dumps(dict(ECHO_MANIFEST, id="other")), "main.py": ECHO_MAIN})
    monkeypatch.setenv("SIDEKICK_PLUGIN_MARKETPLACE_URLS", "https://market.example.com/index.json")
    monkeypatch.setattr(marketplace, "_remote_cache", {})
    monkeypatch.setattr("metateam.core.netguard._resolve_ips", lambda host: [], raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/index.json":
            return httpx.Response(200, json={"name": "Team", "plugins": [
                {"id": "echo", "name": "Echo", "version": "2.0.0", "download_url": "https://market.example.com/echo.zip"},
                {"id": "other-id", "name": "Mismatch", "version": "1.0.0", "download_url": "https://market.example.com/wrong.zip"},
                {"id": "BAD", "download_url": "x"},
                {"id": "github", "name": "Shadow", "version": "9.9.9", "download_url": "https://market.example.com/gh.zip"},
            ]})
        if req.url.path == "/echo.zip":
            return httpx.Response(200, content=package)
        if req.url.path == "/wrong.zip":
            return httpx.Response(200, content=wrong)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cat = marketplace.catalog(http=client)
    by_id = {i["id"]: i for i in cat["items"]}
    assert {"github", "gitlab", "gmail", "echo", "other-id"} <= set(by_id) and "BAD" not in by_id
    assert by_id["github"]["source"] == "bundled", "remote entries cannot shadow bundled ones"
    assert by_id["echo"]["source_name"] == "Team" and not by_id["echo"]["installed"]

    plugin = marketplace.install_from_marketplace("echo", http=client)
    assert plugin.state["origin"] == "https://market.example.com/echo.zip"
    cat = marketplace.catalog(http=client)
    echo = next(i for i in cat["items"] if i["id"] == "echo")
    assert echo["installed"] and echo["update_available"], "index 2.0.0 > installed 1.0.0"

    with pytest.raises(store.PluginError, match="不一致"):
        marketplace.install_from_marketplace("other-id", http=client)
    assert [p.id for p in store.list_installed()] == ["echo"]


def test_unreachable_source_is_reported_not_fatal(data_dir, monkeypatch):
    monkeypatch.setenv("SIDEKICK_PLUGIN_MARKETPLACE_URLS", "https://down.example.com/index.json")
    monkeypatch.setattr(marketplace, "_remote_cache", {})
    monkeypatch.setattr("metateam.core.netguard._resolve_ips", lambda host: [], raising=False)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    cat = marketplace.catalog(http=client)
    assert cat["errors"] and "503" in cat["errors"][0]["error"]
    assert {"github", "gitlab", "gmail"} <= {i["id"] for i in cat["items"]}


# ── HTTP routes ──────────────────────────────────────────────────────────


def test_routes(data_dir, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from metateam.api.routes import plugins as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    items = client.get("/api/plugins/marketplace").json()["items"]
    gh = next(i for i in items if i["id"] == "github")
    assert gh["icon"].startswith("data:image/svg+xml;base64,") and gh["readme"].startswith("# GitHub")

    r = client.post("/api/plugins/install", json={"id": "github"})
    assert r.status_code == 200 and r.json()["credential_status"]["missing"] == ["token"]
    assert client.post("/api/plugins/install", json={}).status_code == 400
    assert client.post("/api/plugins/install", json={"id": "nope"}).status_code == 404

    calls = mock_executor(monkeypatch, lambda req: httpx.Response(200, json={"login": "octo"}))
    r = client.put("/api/plugins/github/credentials", json={"values": {"token": "ghp_x"}})
    body = r.json()
    assert body["credential_status"]["configured"] and body["verify"] == {"ok": True, "result": "已连接 GitHub 账号 octo"}
    assert "ghp_x" not in r.text
    assert calls and calls[0].url.path == "/user"

    assert client.patch("/api/plugins/github", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/api/plugins").json()[0]["id"] == "github"

    upload = make_zip({"manifest.json": json.dumps(ECHO_MANIFEST), "main.py": ECHO_MAIN})
    r = client.post("/api/plugins/upload", files={"file": ("echo.zip", upload, "application/zip")})
    assert r.status_code == 200 and r.json()["id"] == "echo"

    r = client.get("/api/plugins/oauth/callback", params={"state": "bogus", "code": "x"})
    assert r.status_code == 400 and "过期或无效" in r.text

    assert client.delete("/api/plugins/echo").json() == {"status": "ok"}
    assert client.delete("/api/plugins/echo").status_code == 404
