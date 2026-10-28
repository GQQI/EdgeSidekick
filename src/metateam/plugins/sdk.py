"""Plugin SDK, importable from plugin code as ``sidekick_plugin``.

Kept free of package-relative imports: the out-of-process runner loads this
file directly, without the rest of ``metateam`` on ``sys.path``.

A plugin's entry module defines one function per tool declared in its
manifest::

    from sidekick_plugin import PluginContext, ToolError

    def search_issues(params: dict, ctx: PluginContext):
        r = ctx.http.get("https://api.github.com/search/issues", params={"q": params["query"]})
        ctx.raise_for_status(r)
        return r.json()

An optional ``validate_credentials(ctx)`` is called when the user saves or
verifies credentials; raise ``ToolError`` with a readable message to reject them.
"""

from __future__ import annotations

import json
from typing import Any, Optional


class ToolError(Exception):
    """A failure the model (and the user) should read verbatim."""


class PluginContext:
    def __init__(
        self,
        credentials: Optional[dict[str, str]] = None,
        *,
        http: Any = None,
        timeout: float = 30.0,
        plugin_id: str = "",
    ) -> None:
        self.credentials: dict[str, str] = dict(credentials or {})
        self.plugin_id = plugin_id
        self._timeout = timeout
        self._http = http
        self.logs: list[str] = []

    @property
    def http(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": f"Sidekick-Plugin/{self.plugin_id or 'unknown'}"},
            )
        return self._http

    def credential(self, name: str, default: str = "") -> str:
        return str(self.credentials.get(name) or default).strip()

    def require(self, name: str, label: str = "") -> str:
        value = self.credential(name)
        if not value:
            raise ToolError(f"缺少凭据「{label or name}」，请在「设置 → 插件」里配置。")
        return value

    def log(self, message: str) -> None:
        self.logs.append(str(message)[:500])

    @staticmethod
    def raise_for_status(response: Any, what: str = "") -> None:
        code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= code < 300:
            return
        detail = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                detail = str(data.get("message") or data.get("error_description") or data.get("error") or "")
                if not detail and isinstance(data.get("errors"), list) and data["errors"]:
                    detail = json.dumps(data["errors"][0], ensure_ascii=False)
        except Exception:  # noqa: BLE001
            detail = str(getattr(response, "text", "") or "")[:300]
        hint = {401: "凭据无效或已过期", 403: "没有权限或触发了速率限制", 404: "资源不存在或无权访问"}.get(code, "")
        message = "；".join(p for p in (f"HTTP {code}", hint, detail) if p)
        raise ToolError(f"{what}失败：{message}" if what else message)

    def close(self) -> None:
        if self._http is not None:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass


def to_text(result: Any, limit: int = 24000) -> str:
    """Normalize a tool return value into the string handed back to the model."""
    if result is None:
        text = "ok"
    elif isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n…[已截断，共 {len(text)} 字符]"
    return text
