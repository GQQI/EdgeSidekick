# -*- coding: utf-8 -*-
from __future__ import annotations

from metateam.services.mcp_config import McpServerConfig, McpSetup
from metateam.services import mcp_runtime as rt


def setup_function():
    rt._unhealthy_until.clear()
    rt._unhealthy_reason.clear()
    rt._unhealthy_announced.clear()


def test_stdio_auto_pins_mcp_server_sqlite():
    s = McpServerConfig(
        id="sqlite",
        name="SQLite",
        command="uvx",
        args=["mcp-server-sqlite", "--db-path", "./data.db"],
    )
    cmd, args = rt._stdio_command_args(s)
    assert cmd == "uvx"
    assert args[:3] == ["--with", "mcp<2", "mcp-server-sqlite"]


def test_stdio_respects_existing_mcp_with_pin():
    s = McpServerConfig(
        id="sqlite",
        name="SQLite",
        command="uvx",
        args=["--with", "mcp==1.9.4", "mcp-server-sqlite", "--db-path", "x.db"],
    )
    _, args = rt._stdio_command_args(s)
    assert args.count("--with") == 1
    assert "mcp==1.9.4" in args


def test_extensions_prompt_marks_unhealthy_honest(monkeypatch):
    from metateam.runtime.prompts import build_extensions_prompt_block

    monkeypatch.setattr("metateam.services.app_prefs.mcp_in_chat", lambda: True)
    monkeypatch.setattr("metateam.services.app_prefs.plugins_in_chat", lambda: True)
    monkeypatch.setattr(
        "metateam.services.mcp_config.load_mcp_config",
        lambda: McpSetup(
            servers=[
                McpServerConfig(id="sqlite", name="SQLite", command="uvx", enabled=True),
                McpServerConfig(id="git", name="Git", command="uvx", enabled=True),
            ]
        ),
    )
    monkeypatch.setattr(
        "metateam.plugins.tools.plugin_tool_summary",
        lambda user_id=None, plugin_ids=None: [],
    )
    rt._mark_unhealthy("sqlite", "list_resources missing")

    class _S:
        mcp_enabled = True
        mcp_bound_ids = []
        plugin_bound_ids = []

    block = build_extensions_prompt_block(settings=_S())
    assert "UNAVAILABLE" in block
    assert "mcp_* tools NOT injected" in block
    assert "No MCP tools are callable this turn" in block or "UNAVAILABLE" in block
