"""Hello-world offline plugin template."""

from __future__ import annotations

from typing import Any

from sidekick_plugin import PluginContext, ToolError


def greet(params: dict, ctx: PluginContext) -> Any:
    name = str(params.get("name") or "世界").strip() or "世界"
    if len(name) > 80:
        raise ToolError("name 过长")
    return {"message": f"你好，{name}！这是 Sidekick 离线插件模板。"}


def validate_credentials(ctx: PluginContext) -> str:
    return "无需凭据，可直接使用"
