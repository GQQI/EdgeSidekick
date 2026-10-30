# -*- coding: utf-8 -*-
from __future__ import annotations

from metateam.services.mcp_config import McpServerConfig, McpSetup, _parse, update_mcp_config
from metateam.services import mcp_runtime as rt
from metateam.runtime.tool_registry import Tool, ToolRegistry


def setup_function():
    rt._unhealthy_until.clear()
    rt._unhealthy_reason.clear()
    rt._unhealthy_announced.clear()


def test_community_mcp_servers_shape():
    setup = _parse(
        {
            "mcpServers": {
                "time": {
                    "command": "uvx",
                    "args": ["mcp-server-time"],
                },
                "remote": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                    "disabled": True,
                },
            }
        }
    )
    assert {s.id for s in setup.servers} == {"time", "remote"}
    time_s = next(s for s in setup.servers if s.id == "time")
    assert time_s.enabled is True
    assert time_s.command == "uvx"
    remote = next(s for s in setup.servers if s.id == "remote")
    assert remote.enabled is False
    assert remote.normalized_transport() == "http"
    dumped = setup.to_dict()
    assert "mcpServers" in dumped
    assert dumped["mcpServers"]["time"]["command"] == "uvx"
    assert dumped["mcpServers"]["remote"]["disabled"] is True


def test_register_respects_server_ids(monkeypatch):
    monkeypatch.setattr(rt, "_mcp_available", lambda: True)

    def fake_list(setup=None, server_ids=None):
        allow = set(server_ids or []) if server_ids is not None else None
        tools = [
            rt.McpToolInfo("a", "A", "t1", "mcp_a_t1", "d1", {"type": "object", "properties": {}}),
            rt.McpToolInfo("b", "B", "t2", "mcp_b_t2", "d2", {"type": "object", "properties": {}}),
        ]
        if allow is None:
            return tools
        return [t for t in tools if t.server_id in allow]

    monkeypatch.setattr(rt, "list_enabled_tools", fake_list)
    monkeypatch.setattr(
        rt,
        "load_mcp_config",
        lambda: McpSetup(
            servers=[
                McpServerConfig(id="a", name="A", command="x", enabled=True),
                McpServerConfig(id="b", name="B", command="y", enabled=True),
            ]
        ),
    )

    reg = ToolRegistry()
    n = rt.register_mcp_tools(reg, Tool=Tool, server_ids=["a"])
    assert n == 1
    assert "mcp_a_t1" in reg.names()
    assert "mcp_b_t2" not in reg.names()
    tool = reg.get("mcp_a_t1")
    assert tool is not None and tool.requires_approval is True

    # Rebind drops previous
    n2 = rt.register_mcp_tools(reg, Tool=Tool, server_ids=["b"])
    assert n2 == 1
    assert "mcp_a_t1" not in reg.names()
    assert "mcp_b_t2" in reg.names()


def test_empty_server_ids_api_vs_product_auto(monkeypatch):
    """Low-level [] = none; product empty binding = all enabled (auto)."""
    monkeypatch.setattr(rt, "_mcp_available", lambda: True)

    def fake_list(setup=None, server_ids=None):
        if server_ids is None:
            allow = None
        else:
            allow = {str(x).strip() for x in server_ids if str(x).strip()}
        tools = [
            rt.McpToolInfo("a", "A", "t1", "mcp_a_t1", "d1", {"type": "object", "properties": {}}),
            rt.McpToolInfo("b", "B", "t2", "mcp_b_t2", "d2", {"type": "object", "properties": {}}),
        ]
        if allow is None:
            return tools
        return [t for t in tools if t.server_id in allow]

    monkeypatch.setattr(rt, "list_enabled_tools", fake_list)
    monkeypatch.setattr(
        rt,
        "load_mcp_config",
        lambda: McpSetup(
            servers=[
                McpServerConfig(id="a", name="A", command="x", enabled=True),
                McpServerConfig(id="b", name="B", command="y", enabled=True),
            ]
        ),
    )

    # Low-level API: explicit [] still means bind nothing.
    reg = ToolRegistry()
    n = rt.register_mcp_tools(reg, Tool=Tool, server_ids=[])
    assert n == 0
    assert "mcp_a_t1" not in reg.names() and "mcp_b_t2" not in reg.names()

    from metateam.runtime.tools.build import register_mcp_safely
    from metateam.core.config import Settings

    # Product layer: empty / missing binding → auto all enabled.
    reg2 = ToolRegistry()
    settings = Settings()
    settings.mcp_enabled = True
    settings.mcp_bound_ids = []  # empty → all
    monkeypatch.setattr(
        "metateam.services.app_prefs.mcp_in_chat",
        lambda: True,
    )
    register_mcp_safely(reg2, settings=settings, server_ids=None)
    assert "mcp_a_t1" in reg2.names()
    assert "mcp_b_t2" in reg2.names()

    # Explicit subset still narrows.
    reg3 = ToolRegistry()
    register_mcp_safely(reg3, settings=settings, server_ids=["a"])
    assert "mcp_a_t1" in reg3.names()
    assert "mcp_b_t2" not in reg3.names()


def test_extensions_prompt_separates_plugins_and_mcp(monkeypatch):
    from metateam.runtime.prompts import build_extensions_prompt_block

    monkeypatch.setattr(
        "metateam.services.app_prefs.mcp_in_chat",
        lambda: True,
    )
    monkeypatch.setattr(
        "metateam.services.app_prefs.plugins_in_chat",
        lambda: True,
    )
    monkeypatch.setattr(
        "metateam.services.mcp_config.load_mcp_config",
        lambda: McpSetup(
            servers=[McpServerConfig(id="time", name="Time", command="uvx", enabled=True)]
        ),
    )
    monkeypatch.setattr(
        "metateam.plugins.tools.plugin_tool_summary",
        lambda user_id=None, plugin_ids=None: [],
    )
    class _S:
        mcp_enabled = True
        mcp_bound_ids = ["time"]
        plugin_bound_ids = []

    block = build_extensions_prompt_block(settings=_S())
    assert "## MCP servers (external)" in block
    assert "## Plugins (local Sidekick)" in block
    assert "Time" in block
    assert "bound for this chat" not in block.lower() or "MCP servers active" in block
    assert "Plugins ≠ MCP" in block or "not MCP" in block.lower() or "Plugins" in block
    assert "are separate" in block


def test_extensions_prompt_empty_binding_lists_all_enabled(monkeypatch):
    from metateam.runtime.prompts import build_extensions_prompt_block

    monkeypatch.setattr("metateam.services.app_prefs.mcp_in_chat", lambda: True)
    monkeypatch.setattr("metateam.services.app_prefs.plugins_in_chat", lambda: True)
    monkeypatch.setattr(
        "metateam.services.mcp_config.load_mcp_config",
        lambda: McpSetup(
            servers=[McpServerConfig(id="time", name="Time", command="uvx", enabled=True)]
        ),
    )
    monkeypatch.setattr(
        "metateam.plugins.tools.plugin_tool_summary",
        lambda user_id=None, plugin_ids=None: [
            {
                "id": "gmail",
                "name": "Gmail",
                "configured": True,
                "tools": [{"name": "plugin_gmail_send", "label": "send", "readonly": False}],
            }
        ]
        if plugin_ids is None or "gmail" in (plugin_ids or [])
        else [],
    )

    class _S:
        mcp_enabled = True
        mcp_bound_ids = []  # unbound = auto
        plugin_bound_ids = []

    block = build_extensions_prompt_block(settings=_S())
    assert "Time" in block
    assert "all enabled" in block.lower() or "available this chat" in block.lower()
    assert "Gmail" in block
    assert "select MCP" not in block
    assert "请先选择" not in block

