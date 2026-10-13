"""GitHub REST API tools."""

from __future__ import annotations

import base64
import re
from typing import Any

from sidekick_plugin import PluginContext, ToolError

PATCH_LIMIT = 3000
FILE_LIMIT = 20000


def _base(ctx: PluginContext) -> str:
    return (ctx.credential("api_base") or "https://api.github.com").rstrip("/")


def _headers(ctx: PluginContext) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ctx.require('token', 'Personal access token')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo(params: dict) -> str:
    repo = str(params.get("repo") or "").strip().strip("/")
    repo = re.sub(r"^https?://[^/]+/", "", repo).removesuffix(".git")
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise ToolError("repo 需要写成 owner/name 形式")
    return repo


def _limit(params: dict, default: int = 10, cap: int = 30) -> int:
    try:
        n = int(params.get("limit") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def _get(ctx: PluginContext, path: str, what: str, **params: Any) -> Any:
    r = ctx.http.get(f"{_base(ctx)}{path}", headers=_headers(ctx), params={k: v for k, v in params.items() if v not in (None, "")})
    ctx.raise_for_status(r, what)
    return r.json()


def _post(ctx: PluginContext, path: str, what: str, body: dict) -> Any:
    r = ctx.http.post(f"{_base(ctx)}{path}", headers=_headers(ctx), json=body)
    ctx.raise_for_status(r, what)
    return r.json()


def _user(u: Any) -> str:
    return str((u or {}).get("login") or "")


def _issue_row(it: dict) -> dict:
    return {
        "number": it.get("number"),
        "title": it.get("title"),
        "state": it.get("state"),
        "type": "pr" if it.get("pull_request") else "issue",
        "author": _user(it.get("user")),
        "labels": [lb.get("name") for lb in it.get("labels") or [] if isinstance(lb, dict)],
        "comments": it.get("comments"),
        "updated_at": it.get("updated_at"),
        "url": it.get("html_url"),
    }


def validate_credentials(ctx: PluginContext) -> str:
    me = _get(ctx, "/user", "校验 token")
    return f"已连接 GitHub 账号 {me.get('login')}"


def search_repositories(params: dict, ctx: PluginContext) -> Any:
    q = str(params.get("query") or "").strip()
    if not q:
        raise ToolError("query 不能为空")
    data = _get(ctx, "/search/repositories", "搜索仓库", q=q, per_page=_limit(params))
    return {
        "total": data.get("total_count"),
        "items": [
            {
                "repo": it.get("full_name"),
                "description": it.get("description"),
                "stars": it.get("stargazers_count"),
                "language": it.get("language"),
                "default_branch": it.get("default_branch"),
                "updated_at": it.get("pushed_at"),
                "url": it.get("html_url"),
            }
            for it in data.get("items") or []
        ],
    }


def search_issues(params: dict, ctx: PluginContext) -> Any:
    q = str(params.get("query") or "").strip()
    if not q:
        raise ToolError("query 不能为空")
    data = _get(ctx, "/search/issues", "搜索 Issue", q=q, per_page=_limit(params))
    return {"total": data.get("total_count"), "items": [_issue_row(it) for it in data.get("items") or []]}


def get_issue(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    number = int(params.get("number") or 0)
    if number <= 0:
        raise ToolError("number 必须是正整数")
    issue = _get(ctx, f"/repos/{repo}/issues/{number}", f"读取 {repo}#{number}")
    want = max(0, min(int(params.get("comments") or 10), 30))
    comments: list = []
    total = int(issue.get("comments") or 0)
    if want and total:
        last = max(1, (total + want - 1) // want)
        for page in ([last - 1, last] if last > 1 and total % want else [last]):
            comments += _get(ctx, f"/repos/{repo}/issues/{number}/comments", "读取评论", per_page=want, page=page)
    row = _issue_row(issue)
    row["body"] = (issue.get("body") or "")[:FILE_LIMIT]
    row["comments_shown"] = [
        {"author": _user(c.get("user")), "at": c.get("created_at"), "body": (c.get("body") or "")[:4000]}
        for c in comments[-want:]
    ]
    return row


def list_pull_requests(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    state = str(params.get("state") or "open")
    data = _get(ctx, f"/repos/{repo}/pulls", "列出 PR", state=state, per_page=_limit(params))
    return [
        {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": pr.get("state"),
            "draft": pr.get("draft"),
            "author": _user(pr.get("user")),
            "head": (pr.get("head") or {}).get("ref"),
            "base": (pr.get("base") or {}).get("ref"),
            "updated_at": pr.get("updated_at"),
            "url": pr.get("html_url"),
        }
        for pr in data
    ]


def get_pull_request(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    number = int(params.get("number") or 0)
    pr = _get(ctx, f"/repos/{repo}/pulls/{number}", f"读取 PR {repo}#{number}")
    files = _get(ctx, f"/repos/{repo}/pulls/{number}/files", "读取 PR 文件", per_page=100)
    include_patch = params.get("include_patch", True) is not False
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "merged": pr.get("merged"),
        "author": _user(pr.get("user")),
        "head": (pr.get("head") or {}).get("ref"),
        "base": (pr.get("base") or {}).get("ref"),
        "body": (pr.get("body") or "")[:FILE_LIMIT],
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "url": pr.get("html_url"),
        "files": [
            {
                "path": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                **({"patch": (f.get("patch") or "")[:PATCH_LIMIT]} if include_patch else {}),
            }
            for f in files
        ],
    }


def get_file(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    path = str(params.get("path") or "").strip().strip("/")
    data = _get(ctx, f"/repos/{repo}/contents/{path}", f"读取 {path or '/'}", ref=params.get("ref"))
    if isinstance(data, list):
        return {"directory": path or "/", "entries": [{"name": e.get("name"), "type": e.get("type"), "size": e.get("size")} for e in data]}
    if data.get("encoding") == "base64" and data.get("content") is not None:
        raw = base64.b64decode(data["content"])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"path": data.get("path"), "size": data.get("size"), "binary": True}
        return {
            "path": data.get("path"),
            "sha": data.get("sha"),
            "size": data.get("size"),
            "truncated": len(text) > FILE_LIMIT,
            "content": text[:FILE_LIMIT],
        }
    return {"path": data.get("path"), "type": data.get("type"), "download_url": data.get("download_url")}


def list_commits(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    data = _get(ctx, f"/repos/{repo}/commits", "读取提交", sha=params.get("ref"), path=params.get("path"), per_page=_limit(params))
    return [
        {
            "sha": (c.get("sha") or "")[:12],
            "author": ((c.get("commit") or {}).get("author") or {}).get("name"),
            "date": ((c.get("commit") or {}).get("author") or {}).get("date"),
            "message": ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0][:200],
            "url": c.get("html_url"),
        }
        for c in data
    ]


def create_issue(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    title = str(params.get("title") or "").strip()
    if not title:
        raise ToolError("title 不能为空")
    body: dict[str, Any] = {"title": title, "body": str(params.get("body") or "")}
    labels = [str(x) for x in params.get("labels") or [] if str(x).strip()]
    if labels:
        body["labels"] = labels
    it = _post(ctx, f"/repos/{repo}/issues", "创建 Issue", body)
    return {"number": it.get("number"), "url": it.get("html_url"), "title": it.get("title")}


def comment_issue(params: dict, ctx: PluginContext) -> Any:
    repo = _repo(params)
    number = int(params.get("number") or 0)
    text = str(params.get("body") or "").strip()
    if not text:
        raise ToolError("评论内容不能为空")
    c = _post(ctx, f"/repos/{repo}/issues/{number}/comments", "发表评论", {"body": text})
    return {"id": c.get("id"), "url": c.get("html_url")}
