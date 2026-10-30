# -*- coding: utf-8 -*-
from __future__ import annotations

from metateam.runtime.tool_registry import Tool, ToolRegistry
from metateam.runtime.tools.extension_budget import (
    apply_extension_schema_budget,
    note_tool_success,
)


def _add(reg: ToolRegistry, name: str, desc: str = "d") -> None:
    reg.register(
        Tool(
            name,
            desc,
            {"type": "object", "properties": {}},
            lambda **kwargs: "ok",
            requires_approval=False,
            readonly=True,
        )
    )


def test_note_tool_success_orders_and_filters():
    recent = note_tool_success([], "mcp_a_t1")
    recent = note_tool_success(recent, "read_file")  # ignored
    recent = note_tool_success(recent, "plugin_x_do")
    recent = note_tool_success(recent, "mcp_a_t1")
    assert recent[0] == "mcp_a_t1"
    assert "plugin_x_do" in recent
    assert "read_file" not in recent


def test_schema_budget_enables_search_over_threshold():
    reg = ToolRegistry()
    _add(reg, "read_file", "builtin")
    for i in range(25):
        _add(reg, f"mcp_s_tool{i}", f"mcp tool {i}")
    info = apply_extension_schema_budget(reg, threshold=20, recent=["mcp_s_tool3"], Tool=Tool)
    assert info["search_mode"] is True
    names = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    assert "search_tools" in names
    assert "get_tool_schema" in names
    assert "mcp_s_tool3" in names  # hot
    assert "read_file" in names
    # Cold tools hidden from schemas but still executable
    assert "mcp_s_tool24" not in names
    assert reg.get("mcp_s_tool24") is not None
    # search expands focus
    out = reg.get("search_tools").handler(query="tool24")
    assert "mcp_s_tool24" in out
    names2 = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    assert "mcp_s_tool24" in names2


def test_below_threshold_no_search():
    reg = ToolRegistry()
    _add(reg, "read_file")
    for i in range(5):
        _add(reg, f"mcp_s_tool{i}")
    info = apply_extension_schema_budget(reg, threshold=20, recent=[], Tool=Tool)
    assert info["search_mode"] is False
    names = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    assert "search_tools" not in names
    assert "mcp_s_tool0" in names


def test_mention_pins_matching_plugin_into_hot_set():
    """User names a plugin → that plugin's tools stay callable under budget.

    Reproduces: many extension tools + alphabetically-earlier unrelated plugin
    would otherwise monopolize the hot set (e.g. forge before mail).
    """
    from metateam.runtime.tools.extension_budget import (
        apply_extension_schema_budget,
        match_mentioned_extension_tools,
    )

    reg = ToolRegistry()
    _add(reg, "read_file", "builtin")
    forge = [f"plugin_forge_create_issue", f"plugin_forge_search_repos",
             f"plugin_forge_get_issue", f"plugin_forge_list_prs",
             f"plugin_forge_comment", f"plugin_forge_get_file",
             f"plugin_forge_list_commits", f"plugin_forge_search_issues",
             f"plugin_forge_get_pr"]
    mail = [f"plugin_mail_search_messages", f"plugin_mail_read_message",
            f"plugin_mail_read_thread", f"plugin_mail_list_labels",
            f"plugin_mail_create_draft", f"plugin_mail_send_message"]
    other = [f"plugin_forge2_tool{i}" for i in range(10)]
    for n in forge + mail + other:
        _add(reg, n, f"desc {n}")

    summaries = [
        {"id": "forge", "name": "Forge", "tools": [{"name": n} for n in forge]},
        {"id": "mail", "name": "Mail", "tools": [{"name": n} for n in mail]},
        {"id": "forge2", "name": "Forge2", "tools": [{"name": n} for n in other]},
    ]
    assert len(forge) + len(mail) + len(other) > 20

    # Without mention: forge alphabetically fills hot set; mail dropped.
    info0 = apply_extension_schema_budget(reg, threshold=20, recent=[], Tool=Tool)
    assert info0["search_mode"] is True
    names0 = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    # May or may not include mail depending on diversify — but create_issue-like
    # forge tools must not be the ONLY option when user asked for Mail.
    # Re-apply WITH mention of Mail (display name).
    info = apply_extension_schema_budget(
        reg,
        threshold=20,
        recent=[],
        mention_text="查询一下Mail中最新的一个邮件是啥",
        plugin_summaries=summaries,
        Tool=Tool,
    )
    assert info["search_mode"] is True
    assert any("mail" in n for n in info["pinned"])
    names = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    assert "plugin_mail_search_messages" in names
    # Must not be forced into only forge create_issue
    assert "plugin_mail_search_messages" in names
    # Unrelated forge create_issue may still be present via diversify, but mail is required
    pinned = match_mentioned_extension_tools(
        "查询一下Mail中最新的一个邮件是啥",
        forge + mail + other,
        plugin_summaries=summaries,
    )
    assert "plugin_mail_search_messages" in pinned
    assert "plugin_forge_create_issue" not in pinned


def test_match_mentioned_pins_short_leaf_tool_name():
    """Prose 'generate_dockerfile' pins plugin_offline_docker_generate_dockerfile."""
    from metateam.runtime.tools.extension_budget import match_mentioned_extension_tools

    ext = [
        "plugin_offline_docker_generate_dockerfile",
        "plugin_offline_docker_analyze_project",
        "plugin_forge_create_issue",
    ]
    summaries = [
        {
            "id": "offline-docker",
            "name": "离线 Docker 交付",
            "tools": [{"name": n} for n in ext[:2]],
        }
    ]
    pinned = match_mentioned_extension_tools(
        "让我调用generate_dockerfile工具。",
        ext,
        plugin_summaries=summaries,
    )
    assert "plugin_offline_docker_generate_dockerfile" in pinned
    assert "plugin_forge_create_issue" not in pinned


def test_pin_mentioned_expands_schema_focus():
    from metateam.runtime.tools.extension_budget import (
        apply_extension_schema_budget,
        pin_mentioned_extension_schemas,
    )

    reg = ToolRegistry()
    _add(reg, "read_file")
    for i in range(12):
        _add(reg, f"plugin_forge_tool{i}")
    for n in ("plugin_mail_search_messages", "plugin_mail_read_message"):
        _add(reg, n)
    summaries = [
        {"id": "forge", "name": "Forge", "tools": []},
        {"id": "mail", "name": "Mail", "tools": [
            {"name": "plugin_mail_search_messages"},
            {"name": "plugin_mail_read_message"},
        ]},
    ]
    apply_extension_schema_budget(reg, threshold=10, recent=[], Tool=Tool)
    names_before = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    # mail may be cold
    pinned = pin_mentioned_extension_schemas(
        reg,
        "用 Mail 查最新邮件",
        plugin_summaries=summaries,
    )
    assert "plugin_mail_search_messages" in pinned
    names_after = {((s.get("function") or {}).get("name")) for s in reg.schemas()}
    assert "plugin_mail_search_messages" in names_after
