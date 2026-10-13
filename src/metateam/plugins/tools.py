"""Expose installed, enabled, configured plugins as agent tools."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .manifest import TOOL_PREFIX
from .store import credential_status, current_user, invoke_tool, list_installed


def plugin_tool_summary(user_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Enabled plugins and whether they are usable, for prompts and the UI."""
    out: list[dict[str, Any]] = []
    for plugin in list_installed(user_id):
        if not plugin.enabled:
            continue
        out.append(
            {
                "id": plugin.id,
                "name": plugin.manifest.name,
                "description": plugin.manifest.description,
                "configured": credential_status(plugin)["configured"],
                "tools": [
                    {"name": plugin.manifest.tool_name(t.name), "label": t.label, "readonly": t.readonly}
                    for t in plugin.manifest.tools
                ],
            }
        )
    return out


def register_plugin_tools(registry: Any, *, Tool: Any, user_id: Optional[str] = None) -> int:
    uid = user_id or current_user()
    registry.drop_prefix(TOOL_PREFIX)
    count = 0
    for plugin in list_installed(uid):
        if not plugin.enabled or not credential_status(plugin)["configured"]:
            continue
        for spec in plugin.manifest.tools:

            def _make(pid: str, tool: str, label: str) -> Callable[..., str]:
                def _fn(**kwargs: Any) -> str:
                    res = invoke_tool(pid, tool, dict(kwargs), user_id=uid)
                    if res.get("ok"):
                        return str(res.get("result") or "ok")
                    return f"error: [{label}] {res.get('error') or '插件调用失败'}"

                return _fn

            registry.register(
                Tool(
                    plugin.manifest.tool_name(spec.name),
                    f"[插件:{plugin.manifest.name}] {spec.description}",
                    spec.parameters,
                    _make(plugin.id, spec.name, plugin.manifest.name),
                    parallel_safe=spec.readonly,
                    requires_approval=not spec.readonly,
                    readonly=spec.readonly,
                )
            )
            count += 1
    return count
