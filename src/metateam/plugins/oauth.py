"""OAuth2 authorization-code flow for plugins that declare an ``oauth`` section.

The browser is sent to the provider with a loopback ``redirect_uri`` on this
server; the callback is unauthenticated (it is a plain browser redirect), so
the one-time ``state`` is what binds it to the user who started the flow.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

from .store import PluginError, credentials_for, get_installed, set_credentials

STATE_TTL = 600.0

# Provider error codes returned on the callback (?error=...) — keep actionable in Chinese.
_PROVIDER_ERRORS = {
    "access_denied": (
        "授权被拒绝（403 access_denied）。常见原因：Google OAuth 同意屏幕处于「测试中」，"
        "而当前登录的 Google 账号不在测试用户列表里。"
        "请到 Google Cloud 控制台 → API 和服务 → OAuth 同意屏幕 → 测试用户，"
        "添加你正在登录的那个邮箱，等一两分钟后再点「用 Google 授权」。"
        "也可把同意屏幕发布为「正式环境」（需满足 Google 政策；Gmail 敏感权限可能仍要验证）。"
        "另外请把同意屏幕上的应用名称改成可读名称（不要用「1」这类占位名）。"
    ),
    "admin_policy_enforced": "管理员策略禁止了此授权，请联系 Workspace 管理员或换个人 Google 账号。",
    "org_internal": "此 OAuth 客户端仅限组织内部用户，请用该组织账号登录，或把同意屏幕用户类型改为「外部」。",
}


def explain_provider_error(error: str, description: str = "") -> str:
    key = (error or "").strip()
    base = _PROVIDER_ERRORS.get(key)
    detail = (description or "").strip()
    if base and detail:
        return f"{base}（详情：{detail}）"
    if base:
        return base
    if key or detail:
        return f"授权未完成：{(key + (' — ' if key and detail else '') + detail).strip()}"
    return "授权未完成：提供方没有返回授权码。"



@dataclass
class _Pending:
    plugin_id: str
    user_id: str
    redirect_uri: str
    created: float


_pending: dict[str, _Pending] = {}
_lock = threading.Lock()


def _prune(now: float) -> None:
    for key in [k for k, v in _pending.items() if now - v.created > STATE_TTL]:
        _pending.pop(key, None)


def start(plugin_id: str, redirect_uri: str, user_id: str) -> dict[str, str]:
    plugin = get_installed(plugin_id, user_id)
    spec = plugin.manifest.oauth
    if spec is None:
        raise PluginError("这个插件不支持 OAuth 授权")
    creds = credentials_for(plugin)
    client_id = creds.get(spec.client_id_field, "")
    if not client_id or not creds.get(spec.client_secret_field, ""):
        raise PluginError("请先填写并保存 Client ID 和 Client Secret，再发起授权")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        _prune(now)
        _pending[state] = _Pending(plugin_id, user_id, redirect_uri, now)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(spec.scopes),
        "state": state,
        **spec.extra_params,
    }
    return {"url": f"{spec.authorize_url}?{urlencode(query)}", "state": state, "redirect_uri": redirect_uri}


def complete(state: str, code: str, *, http: Any = None) -> dict[str, str]:
    with _lock:
        _prune(time.time())
        pending = _pending.pop(state or "", None)
    if pending is None:
        raise PluginError("授权请求已过期或无效，请回到设置页重新点击授权")
    if not code:
        raise PluginError("授权被取消或没有返回授权码")
    plugin = get_installed(pending.plugin_id, pending.user_id)
    spec = plugin.manifest.oauth
    assert spec is not None
    creds = credentials_for(plugin)
    import httpx

    client = http or httpx.Client(timeout=30)
    try:
        resp = client.post(
            spec.token_url,
            data={
                "code": code,
                "client_id": creds.get(spec.client_id_field, ""),
                "client_secret": creds.get(spec.client_secret_field, ""),
                "redirect_uri": pending.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        data = resp.json() if resp.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise PluginError(f"换取令牌失败：{exc}") from exc
    finally:
        if http is None:
            client.close()
    if resp.status_code >= 400 or not isinstance(data, dict):
        detail = data.get("error_description") or data.get("error") if isinstance(data, dict) else ""
        raise PluginError(f"换取令牌失败：HTTP {resp.status_code} {detail or ''}".strip())
    token = str(data.get(spec.token_field) or "")
    if not token:
        raise PluginError(
            f"服务商没有返回 {spec.token_field}。如果之前授权过，请在服务商的账号设置里移除本应用后重试。"
        )
    set_credentials(pending.plugin_id, {spec.token_field: token}, user_id=pending.user_id)
    return {"plugin_id": pending.plugin_id, "name": plugin.manifest.name}


def pending_count(user_id: Optional[str] = None) -> int:
    with _lock:
        return sum(1 for v in _pending.values() if user_id is None or v.user_id == user_id)
