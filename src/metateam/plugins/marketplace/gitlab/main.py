"""GitLab REST API (v4) tools."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

from sidekick_plugin import PluginContext, ToolError

DIFF_LIMIT = 3000
FILE_LIMIT = 20000


def _api(ctx: PluginContext) -> str:
    base = (ctx.credential("base_url") or "https://gitlab.com").rstrip("/")
    return base if base.endswith("/api/v4") else f"{base}/api/v4"


def _headers(ctx: PluginContext) -> dict[str, str]:
    return {"PRIVATE-TOKEN": ctx.require("token", "Access token")}


def _project(params: dict, required: bool = True) -> str:
    raw = str(params.get("project") or "").strip().strip("/")
    raw = re.sub(r"^https?://[^/]+/", "", raw).removesuffix(".git")
    if not raw:
        if required:
            raise ToolError("project 需要写成 group/project 路径或数字 id")
        return ""
    return raw if raw.isdigit() else quote(raw, safe="")


def _limit(params: dict, default: int = 10, cap: int = 30) -> int:
    try:
        n = int(params.get("limit") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def _get(ctx: PluginContext, path: str, what: str, **params: Any) -> Any:
    r = ctx.http.get(f"{_api(ctx)}{path}", headers=_headers(ctx), params={k: v for k, v in params.items() if v not in (None, "")})
    ctx.raise_for_status(r, what)
    return r.json()


def _post(ctx: PluginContext, path: str, what: str, body: dict) -> Any:
    r = ctx.http.post(f"{_api(ctx)}{path}", headers=_headers(ctx), json=body)
    ctx.raise_for_status(r, what)
    return r.json()


def _who(u: Any) -> str:
    return str((u or {}).get("username") or "")


def _issue_row(it: dict) -> dict:
    return {
        "iid": it.get("iid"),
        "project_id": it.get("project_id"),
        "title": it.get("title"),
        "state": it.get("state"),
        "author": _who(it.get("author")),
        "assignees": [_who(a) for a in it.get("assignees") or []],
        "labels": it.get("labels") or [],
        "updated_at": it.get("updated_at"),
        "url": it.get("web_url"),
    }


def _mr_row(mr: dict) -> dict:
    return {
        "iid": mr.get("iid"),
        "project_id": mr.get("project_id"),
        "title": mr.get("title"),
        "state": mr.get("state"),
        "draft": mr.get("draft") or mr.get("work_in_progress"),
        "author": _who(mr.get("author")),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "updated_at": mr.get("updated_at"),
        "url": mr.get("web_url"),
    }


def validate_credentials(ctx: PluginContext) -> str:
    me = _get(ctx, "/user", "校验 token")
    return f"已连接 GitLab 账号 {me.get('username')}"


def search_projects(params: dict, ctx: PluginContext) -> Any:
    q = str(params.get("query") or "").strip()
    if not q:
        raise ToolError("query 不能为空")
    data = _get(ctx, "/projects", "搜索项目", search=q, membership="true", simple="true", per_page=_limit(params), order_by="last_activity_at")
    return [
        {
            "project": p.get("path_with_namespace"),
            "id": p.get("id"),
            "description": p.get("description"),
            "default_branch": p.get("default_branch"),
            "url": p.get("web_url"),
        }
        for p in data
    ]


def list_issues(params: dict, ctx: PluginContext) -> Any:
    project = _project(params, required=False)
    path = f"/projects/{project}/issues" if project else "/issues"
    query: dict[str, Any] = {
        "state": params.get("state") or "opened",
        "labels": params.get("labels"),
        "search": params.get("search"),
        "per_page": _limit(params),
    }
    if params.get("assigned_to_me"):
        query["scope"] = "assigned_to_me"
    elif not project:
        query["scope"] = "all"
    return [_issue_row(it) for it in _get(ctx, path, "列出 Issue", **query)]


def get_issue(params: dict, ctx: PluginContext) -> Any:
    project = _project(params)
    iid = int(params.get("iid") or 0)
    it = _get(ctx, f"/projects/{project}/issues/{iid}", f"读取 Issue #{iid}")
    notes = _get(ctx, f"/projects/{project}/issues/{iid}/notes", "读取评论", sort="desc", per_page=15)
    row = _issue_row(it)
    row["description"] = (it.get("description") or "")[:FILE_LIMIT]
    row["notes"] = [
        {"author": _who(n.get("author")), "at": n.get("created_at"), "body": (n.get("body") or "")[:4000]}
        for n in reversed(notes)
        if not n.get("system")
    ]
    return row


def list_merge_requests(params: dict, ctx: PluginContext) -> Any:
    project = _project(params, required=False)
    path = f"/projects/{project}/merge_requests" if project else "/merge_requests"
    query: dict[str, Any] = {"state": params.get("state") or "opened", "per_page": _limit(params)}
    if not project:
        query["scope"] = params.get("scope") or "assigned_to_me"
    return [_mr_row(mr) for mr in _get(ctx, path, "列出 MR", **query)]


def get_merge_request(params: dict, ctx: PluginContext) -> Any:
    project = _project(params)
    iid = int(params.get("iid") or 0)
    mr = _get(ctx, f"/projects/{project}/merge_requests/{iid}", f"读取 MR !{iid}")
    row = _mr_row(mr)
    row["description"] = (mr.get("description") or "")[:FILE_LIMIT]
    row["merge_status"] = mr.get("detailed_merge_status") or mr.get("merge_status")
    row["pipeline"] = ((mr.get("head_pipeline") or mr.get("pipeline")) or {}).get("status")
    include = params.get("include_diff", True) is not False
    diffs = _get(ctx, f"/projects/{project}/merge_requests/{iid}/diffs", "读取 MR 改动", per_page=100)
    row["files"] = [
        {
            "path": d.get("new_path") or d.get("old_path"),
            "status": "added" if d.get("new_file") else "deleted" if d.get("deleted_file") else "renamed" if d.get("renamed_file") else "modified",
            **({"diff": (d.get("diff") or "")[:DIFF_LIMIT]} if include else {}),
        }
        for d in diffs
    ]
    return row


def get_file(params: dict, ctx: PluginContext) -> Any:
    project = _project(params)
    path = str(params.get("path") or "").strip().strip("/")
    ref = str(params.get("ref") or "").strip()
    if not ref:
        ref = _get(ctx, f"/projects/{project}", "读取项目").get("default_branch") or "main"
    if path:
        r = ctx.http.get(f"{_api(ctx)}/projects/{project}/repository/files/{quote(path, safe='')}", headers=_headers(ctx), params={"ref": ref})
        if r.status_code != 404:
            ctx.raise_for_status(r, f"读取 {path}")
            data = r.json()
            raw = base64.b64decode(data.get("content") or "")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return {"path": path, "ref": ref, "size": data.get("size"), "binary": True}
            return {"path": path, "ref": ref, "truncated": len(text) > FILE_LIMIT, "content": text[:FILE_LIMIT]}
    tree = _get(ctx, f"/projects/{project}/repository/tree", f"列出目录 {path or '/'}", path=path or None, ref=ref, per_page=100)
    return {"directory": path or "/", "ref": ref, "entries": [{"name": e.get("name"), "type": e.get("type")} for e in tree]}


def create_issue(params: dict, ctx: PluginContext) -> Any:
    project = _project(params)
    title = str(params.get("title") or "").strip()
    if not title:
        raise ToolError("title 不能为空")
    body = {"title": title, "description": str(params.get("description") or "")}
    if params.get("labels"):
        body["labels"] = str(params["labels"])
    it = _post(ctx, f"/projects/{project}/issues", "创建 Issue", body)
    return {"iid": it.get("iid"), "url": it.get("web_url"), "title": it.get("title")}


def add_comment(params: dict, ctx: PluginContext) -> Any:
    project = _project(params)
    target = "merge_requests" if params.get("target") == "merge_request" else "issues"
    iid = int(params.get("iid") or 0)
    text = str(params.get("body") or "").strip()
    if not text:
        raise ToolError("评论内容不能为空")
    note = _post(ctx, f"/projects/{project}/{target}/{iid}/notes", "发表评论", {"body": text})
    return {"id": note.get("id"), "created_at": note.get("created_at")}
