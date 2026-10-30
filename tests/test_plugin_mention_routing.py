# -*- coding: utf-8 -*-
"""User-named plugin must stay in schema; unrelated plugin tools must not win."""

from __future__ import annotations

from metateam.runtime.tool_registry import Tool, ToolRegistry
from metateam.runtime.tools.extension_budget import apply_extension_schema_budget


def _add(reg: ToolRegistry, name: str) -> None:
    reg.register(
        Tool(
            name,
            f"tool {name}",
            {"type": "object", "properties": {"q": {"type": "string"}}},
            lambda **kwargs: "ok",
            requires_approval=False,
            readonly=True,
        )
    )


def test_gmail_like_query_keeps_mail_not_only_forge_create_issue():
    reg = ToolRegistry()
    _add(reg, "read_file")
    forge = [
        "plugin_forge_comment_issue",
        "plugin_forge_create_issue",
        "plugin_forge_get_file",
        "plugin_forge_get_issue",
        "plugin_forge_get_pull_request",
        "plugin_forge_list_commits",
        "plugin_forge_list_pull_requests",
        "plugin_forge_search_issues",
        "plugin_forge_search_repositories",
    ]
    mail = [
        "plugin_mail_search_messages",
        "plugin_mail_read_message",
        "plugin_mail_read_thread",
        "plugin_mail_list_labels",
        "plugin_mail_create_draft",
        "plugin_mail_send_message",
    ]
    # Extra plugins to push over threshold (mirrors github+gitlab+gmail).
    extra = [f"plugin_other_tool{i}" for i in range(8)]
    for n in forge + mail + extra:
        _add(reg, n)

    summaries = [
        {"id": "forge", "name": "Forge", "tools": [{"name": n} for n in forge]},
        {"id": "mail", "name": "Mail", "tools": [{"name": n} for n in mail]},
        {"id": "other", "name": "Other", "tools": [{"name": n} for n in extra]},
    ]
    user = "查询一下Mail中最新的一个邮件是啥"
    info = apply_extension_schema_budget(
        reg,
        threshold=20,
        recent=[],
        mention_text=user,
        plugin_summaries=summaries,
        Tool=Tool,
    )
    assert info["search_mode"] is True
    schema_names = {
        ((s.get("function") or {}).get("name")) for s in reg.schemas()
    }
    assert "plugin_mail_search_messages" in schema_names
    # The bug was: only forge create_issue (and siblings) callable while mail
    # was advertised in the prompt. Mail must be callable.
    assert any(n and n.startswith("plugin_mail_") for n in schema_names)
    # Calling forge create_issue would be wrong for this user ask — ensure we
    # did not pin forge from the Mail mention.
    assert "plugin_forge_create_issue" not in set(info["pinned"])
