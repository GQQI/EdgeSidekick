"""Gmail API tools (OAuth refresh-token flow)."""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from typing import Any

from sidekick_plugin import PluginContext, ToolError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
BODY_LIMIT = 12000


def _access_token(ctx: PluginContext) -> str:
    cached = getattr(ctx, "_gmail_token", "")
    if cached:
        return cached
    r = ctx.http.post(
        TOKEN_URL,
        data={
            "client_id": ctx.require("client_id", "OAuth Client ID"),
            "client_secret": ctx.require("client_secret", "OAuth Client Secret"),
            "refresh_token": ctx.require("refresh_token", "Refresh token"),
            "grant_type": "refresh_token",
        },
    )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", "")
        except Exception:  # noqa: BLE001
            err = ""
        if err == "invalid_grant":
            raise ToolError("Google 授权已失效或被撤销，请在「设置 → 插件 → Gmail」重新授权。")
        ctx.raise_for_status(r, "刷新 Google 授权")
    token = str(r.json().get("access_token") or "")
    if not token:
        raise ToolError("Google 没有返回 access token")
    ctx._gmail_token = token  # type: ignore[attr-defined]
    return token


def _get(ctx: PluginContext, path: str, what: str, **params: Any) -> Any:
    r = ctx.http.get(f"{API}{path}", headers={"Authorization": f"Bearer {_access_token(ctx)}"}, params=params)
    ctx.raise_for_status(r, what)
    return r.json()


def _post(ctx: PluginContext, path: str, what: str, body: dict) -> Any:
    r = ctx.http.post(f"{API}{path}", headers={"Authorization": f"Bearer {_access_token(ctx)}"}, json=body)
    ctx.raise_for_status(r, what)
    return r.json()


def _headers(payload: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in (payload or {}).get("headers") or []}


def _b64(data: str) -> str:
    raw = base64.urlsafe_b64decode((data or "") + "=" * (-len(data or "") % 4))
    return raw.decode("utf-8", errors="replace")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _walk(part: dict, plain: list[str], rich: list[str], files: list[str]) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body") or {}
    if part.get("filename"):
        files.append(part["filename"])
    elif mime == "text/plain" and body.get("data"):
        plain.append(_b64(body["data"]))
    elif mime == "text/html" and body.get("data"):
        rich.append(_b64(body["data"]))
    for child in part.get("parts") or []:
        _walk(child, plain, rich, files)


def _message(msg: dict, full: bool = True) -> dict:
    payload = msg.get("payload") or {}
    h = _headers(payload)
    row: dict[str, Any] = {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": h.get("from", ""),
        "to": h.get("to", ""),
        "cc": h.get("cc", ""),
        "subject": h.get("subject", ""),
        "date": h.get("date", ""),
        "labels": msg.get("labelIds") or [],
    }
    if not full:
        row["snippet"] = html.unescape(msg.get("snippet") or "")
        return row
    plain: list[str] = []
    rich: list[str] = []
    files: list[str] = []
    _walk(payload, plain, rich, files)
    text = "\n".join(plain).strip() or _html_to_text("\n".join(rich))
    row["body"] = text[:BODY_LIMIT] + ("\n…[正文已截断]" if len(text) > BODY_LIMIT else "")
    row["attachments"] = files
    return row


def validate_credentials(ctx: PluginContext) -> str:
    profile = _get(ctx, "/profile", "读取邮箱信息")
    return f"已连接 {profile.get('emailAddress')}（共 {profile.get('messagesTotal')} 封邮件）"


def search_messages(params: dict, ctx: PluginContext) -> Any:
    try:
        limit = max(1, min(int(params.get("limit") or 10), 25))
    except (TypeError, ValueError):
        limit = 10
    query = str(params.get("query") or "").strip() or "in:inbox"
    listing = _get(ctx, "/messages", "搜索邮件", q=query, maxResults=limit)
    out = []
    for ref in listing.get("messages") or []:
        msg = _get(
            ctx,
            f"/messages/{ref['id']}",
            "读取邮件摘要",
            format="metadata",
            metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
        )
        out.append(_message(msg, full=False))
    return {"query": query, "count": len(out), "messages": out}


def read_message(params: dict, ctx: PluginContext) -> Any:
    mid = str(params.get("id") or "").strip()
    if not mid:
        raise ToolError("id 不能为空")
    return _message(_get(ctx, f"/messages/{mid}", "读取邮件", format="full"))


def read_thread(params: dict, ctx: PluginContext) -> Any:
    tid = str(params.get("thread_id") or "").strip()
    if not tid:
        raise ToolError("thread_id 不能为空")
    thread = _get(ctx, f"/threads/{tid}", "读取会话", format="full")
    return {"thread_id": tid, "messages": [_message(m) for m in thread.get("messages") or []]}


def list_labels(params: dict, ctx: PluginContext) -> Any:
    data = _get(ctx, "/labels", "读取标签")
    out = []
    for lb in data.get("labels") or []:
        detail = _get(ctx, f"/labels/{lb['id']}", "读取标签") if lb.get("type") == "system" and lb["id"] in ("INBOX", "UNREAD") else lb
        out.append({"id": lb.get("id"), "name": lb.get("name"), "type": lb.get("type"), "unread": detail.get("messagesUnread")})
    return out


def _compose(params: dict, ctx: PluginContext) -> dict:
    to = str(params.get("to") or "").strip()
    if not to:
        raise ToolError("收件人 to 不能为空")
    msg = EmailMessage()
    msg["To"] = to
    if params.get("cc"):
        msg["Cc"] = str(params["cc"])
    subject = str(params.get("subject") or "")
    thread_id = ""
    reply_to = str(params.get("reply_to_id") or "").strip()
    if reply_to:
        orig = _get(ctx, f"/messages/{reply_to}", "读取原邮件", format="metadata", metadataHeaders=["Subject", "Message-ID", "References"])
        h = _headers(orig.get("payload") or {})
        thread_id = orig.get("threadId") or ""
        if not subject:
            base = h.get("subject", "")
            subject = base if base.lower().startswith("re:") else f"Re: {base}"
        if h.get("message-id"):
            msg["In-Reply-To"] = h["message-id"]
            msg["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
    msg["Subject"] = subject
    msg.set_content(str(params.get("body") or ""))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    body: dict[str, Any] = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    return body


def create_draft(params: dict, ctx: PluginContext) -> Any:
    draft = _post(ctx, "/drafts", "创建草稿", {"message": _compose(params, ctx)})
    return {"draft_id": draft.get("id"), "message_id": (draft.get("message") or {}).get("id"), "status": "草稿已保存，未发送"}


def send_message(params: dict, ctx: PluginContext) -> Any:
    sent = _post(ctx, "/messages/send", "发送邮件", _compose(params, ctx))
    return {"id": sent.get("id"), "thread_id": sent.get("threadId"), "status": "已发送"}
