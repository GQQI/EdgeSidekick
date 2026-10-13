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
    return {"url": f"{spec.authorize_url}?{urlencode(query)}", "state": state}


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
