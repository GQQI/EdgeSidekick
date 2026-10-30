"""Tool-intent detection and named-plugin resolution."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ....core.textutil import safe_clip


TOOL_INTENT_CONTINUE_LIMIT = 2

TOOL_INTENT_NUDGE = (
    # Internal developer/system instruction — not a user message.
    "Emit native tool_calls now; do not only describe intent. "
    "Match plugin_/mcp_ prefix when the user named a plugin. "
    "If already done from evidence, answer and stop."
)

TOOL_INTENT_SOFT_NUDGE = (
    "Prior reply described a tool/plugin/MCP with no tool_calls. "
    "Call the matching function now, or say unavailable and stop. "
    "Do not repeat the plan."
)

# Conservative: future/intent language about using tools or plugins.
# No product-specific tool names — plugin_*/mcp_*/skill_* / generic 工具|插件
# all share this path. Models often name a concrete tool id instead of the
# words 工具/插件 (e.g. 「我需要使用plugin_foo_bar来搜索」); that must match too.
_TOOL_ID_TOKEN = r"(?:plugin_|mcp_|skill_|builtin_)[A-Za-z0-9][A-Za-z0-9_.-]{2,}"

_TOOL_INTENT_PATTERN = "".join(
    [
        r"(?:",
        # Intent verb + use + generic tool words
        r"(?:我需要|我将|我会|让我|接下来|准备|打算)[^。\n!！?？]{0,48}",
        r"(?:使用|调用|借助|通过).{0,36}(?:工具|插件|函数|MCP|mcp)",
        r"|",
        # Bare: 使用/调用 + … + 插件/MCP/工具 (「使用X插件」「使用演示插件」)
        r"(?:使用|调用|借助|通过).{0,40}(?:插件|MCP|mcp|工具)",
        r"|",
        # Intent verb + use + concrete tool id
        r"(?:我需要|我将|我会|让我|接下来|准备|打算)[^。\n!！?？]{0,48}",
        r"(?:使用|调用|借助|通过)\s*",
        _TOOL_ID_TOKEN,
        r"|",
        # use + tool word + action
        r"(?:使用|调用|借助).{0,24}(?:工具|插件).{0,12}(?:来|去|进行|搜索|读取|查询|获取)",
        r"|",
        # use + concrete tool id + action
        r"(?:使用|调用|借助)\s*",
        _TOOL_ID_TOKEN,
        r".{0,24}(?:来|去|进行|搜索|读取|查询|获取|列出|发送)",
        r"|",
        r"需要通过.{0,24}(?:工具|插件|函数调用|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        # Planning-to-act without tool id: 「让我先搜索…」
        r"(?:让我先|接下来|开始|先去|先来|现在就).{0,12}"
        r"(?:搜索|查询|读取|打开|执行|调用|列出|获取|发送|检查)",
        r"|",
        # Bare tool id with nearby intent
        r"(?:需要|将要|将会|准备|打算).{0,24}",
        _TOOL_ID_TOKEN,
        r"|",
        r"\b(?:i\s+need\s+to|i\s+will|i'll|i\s+am\s+going\s+to|i'm\s+going\s+to|let\s+me)\b",
        r".{0,48}\b(?:use|call|invoke)\b.{0,36}",
        r"(?:\b(?:tool|plugin|function|mcp)\b|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        r"\b(?:going\s+to|about\s+to)\s+(?:use|call|invoke)\b.{0,36}",
        r"(?:\b(?:tool|plugin|function)\b|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        r"\blet\s+me\s+(?:first\s+)?(?:search|query|read|list|fetch|open|call)\b",
        r")",
    ]
)

_TOOL_INTENT_RE = re.compile(_TOOL_INTENT_PATTERN, re.IGNORECASE | re.DOTALL)

_NAMED_TOOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(" + _TOOL_ID_TOKEN + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)



# Past-tense / completed action — do not nudge (already did the work in prose).
_TOOL_DONE_RE = re.compile(
    r"(?:"
    r"(?:已经|已成功|刚完成|刚才|刚刚).{0,16}(?:使用|调用|执行)"
    r"|"
    r"\b(?:i\s+(?:used|called|invoked|ran)|already\s+(?:used|called)|here\s+(?:are|is)\s+the)\b"
    r")",
    re.IGNORECASE,
)


def looks_like_tool_intent_only(text: str) -> bool:
    """True when assistant prose promises a tool use instead of calling one.

    Conservative on purpose: plain chat, finished answers, and past-tense
    reports must not reopen the turn.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 1200:
        return False
    if _TOOL_DONE_RE.search(raw):
        return False
    return bool(_TOOL_INTENT_RE.search(raw))


def intent_probe_text(content: str | None = None, reasoning: str | None = None) -> str:
    """Text used for tool-intent detection.

    Reasoning models often put the plan in `reasoning` and leave `content`
    empty (or duplicate the same plan into both). Probe both, de-duplicated.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for piece in (content, reasoning):
        raw = (piece or "").strip()
        if not raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(raw)
    joined = "\n".join(parts).strip()
    return joined[:1200] if len(joined) > 1200 else joined


def mentions_extension_tools(text: str) -> bool:
    """True when prose refers to a plugin/MCP/tool category (not a product name)."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(
        re.search(
            r"(?:插件|MCP|mcp|工具|plugin|function\s*call|tool_calls?)",
            raw,
            re.IGNORECASE,
        )
    )


def resolve_display_named_plugin_tools(
    text: str,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Map human plugin display names/ids in prose to installed tool ids.

    Generic over the plugin registry (manifest `name` / `id` / tool names).
    Never hardcodes a product brand.
    """
    raw = text or ""
    if not raw or not plugin_summaries:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for plugin in plugin_summaries:
        if not isinstance(plugin, dict):
            continue
        labels = [
            str(plugin.get("name") or "").strip(),
            str(plugin.get("id") or "").strip(),
        ]
        labels = [x for x in labels if x]
        if not labels:
            continue
        hit = False
        for label in labels:
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(label) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                hit = True
                break
            if re.search(re.escape(label) + r"\s*插件", raw, re.IGNORECASE):
                hit = True
                break
        if not hit:
            continue
        for tool in plugin.get("tools") or []:
            if isinstance(tool, dict):
                name = str(tool.get("name") or "").strip()
            else:
                name = str(tool or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(name)
    return found












def extract_named_tool_ids(text: str) -> list[str]:
    """Return plugin_*/mcp_*/skill_*/builtin_* ids mentioned in prose.

    Order-preserving, de-duplicated. Used to validate intent against the
    live tool schema (generic — not product-specific).
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _NAMED_TOOL_RE.finditer(text or ""):
        name = match.group(1)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out



def tool_leaf_aliases(
    full_name: str,
    *,
    known_plugin_ids: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Short aliases for a registry tool id (generic over plugin_/mcp_/skill_).

    ``plugin_offline_docker_generate_dockerfile`` → also
    ``generate_dockerfile``. Used so prose can name the manifest tool leaf
    without the session prefix. Never invents names outside the full id.
    """
    name = str(full_name or "").strip()
    if not name:
        return []
    out: list[str] = [name]
    low = name.lower()
    body = ""
    if low.startswith("plugin_"):
        body = name[7:]
    elif low.startswith("mcp_"):
        body = name[4:]
    elif low.startswith("skill_"):
        body = name[6:]
    elif low.startswith("builtin_"):
        body = name[8:]
    if not body:
        return out
    known = sorted(
        {
            str(x).strip().replace("-", "_").lower()
            for x in (known_plugin_ids or [])
            if str(x).strip()
        },
        key=len,
        reverse=True,
    )
    stripped = False
    body_low = body.lower()
    for kid in known:
        prefix = kid + "_"
        if body_low == kid:
            stripped = True
            break
        if body_low.startswith(prefix):
            leaf = body[len(prefix) :]
            if leaf and leaf.lower() not in {a.lower() for a in out}:
                out.append(leaf)
            stripped = True
            break
    if not stripped and "_" in body:
        # Fallback: drop the first path segment (source id guess).
        leaf = body.split("_", 1)[1]
        if leaf and leaf.lower() not in {a.lower() for a in out}:
            out.append(leaf)
    return out


def resolve_mentioned_tools(
    text: str,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    *,
    known_plugin_ids: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Tool ids named in prose that exist in the current schema/registry.

    Prefers prefixed ids (plugin_/mcp_/skill_/builtin_), then any exact
    known_tool_names hit, then short leaf aliases derived from those names.
    Never invents names outside the provided registry.
    """
    raw = text or ""
    known = {str(n) for n in (known_tool_names or []) if str(n).strip()}
    known_lower = {n.lower(): n for n in known}
    found: list[str] = []
    seen: set[str] = set()

    for name in extract_named_tool_ids(raw):
        key = name.lower()
        if key in seen:
            continue
        # Prefixed ids are candidates; if a registry is provided, require membership.
        if known and key not in known_lower:
            continue
        seen.add(key)
        found.append(known_lower.get(key, name))

    if known:
        # Exact registry names as whole tokens.
        for key, canon in known_lower.items():
            if key in seen:
                continue
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                seen.add(key)
                found.append(canon)
        # Leaf aliases: prose says generate_dockerfile → full plugin_* id.
        # Prefer unique leaves; if several tools share a leaf, include all
        # matches so schema pinning can surface them without guessing one.
        leaf_map: dict[str, list[str]] = {}
        for canon in known:
            for alias in tool_leaf_aliases(canon, known_plugin_ids=known_plugin_ids):
                if alias.lower() == canon.lower():
                    continue
                leaf_map.setdefault(alias.lower(), []).append(canon)
        for leaf, canons in leaf_map.items():
            if not re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(leaf) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                continue
            for canon in canons:
                key = canon.lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(canon)
    return found


def tool_intent_unavailable_message(
    text: str,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> str | None:
    """When prose needs tools absent from the live schema, explain generically."""
    named = extract_named_tool_ids(text or "")
    mapped = resolve_display_named_plugin_tools(text or "", plugin_summaries)
    known_list = [str(n) for n in (known_tool_names or []) if str(n).strip()]
    known = {n.lower() for n in known_list}
    plugin_tools_in_schema = [n for n in known_list if n.lower().startswith("plugin_")]

    if named:
        if known:
            missing = [n for n in named if n.lower() not in known]
            if not missing:
                return None
            shown = ", ".join(missing[:6])
            return (
                f"当前会话的工具列表中没有这些工具：{shown}。"
                "请在设置中启用对应插件/MCP，并确认已加入本会话后再试。"
            )
        shown = ", ".join(named[:6])
        return (
            f"当前会话没有可用工具，无法调用：{shown}。"
            "请确认 plugins_in_chat / mcp_in_chat 已开启，且工具已注册到本会话。"
        )

    if mapped and known and not plugin_tools_in_schema:
        missing = [n for n in mapped if n.lower() not in known]
        if missing:
            shown = ", ".join(missing[:6])
            return (
                f"当前会话未注入对应插件工具（例如 {shown}）。"
                "请确认插件已安装、凭据已配置，且 plugins_in_chat 已开启并绑定到本会话。"
            )

    if mentions_extension_tools(text or "") and not known_list:
        return (
            "当前会话没有可用工具，无法执行插件/MCP/工具调用。"
            "请确认 plugins_in_chat / mcp_in_chat 已开启，且工具已注册到本会话。"
        )
    if mentions_extension_tools(text or "") and not plugin_tools_in_schema and (
        "插件" in (text or "") or re.search(r"\bplugin\b", text or "", re.IGNORECASE)
    ):
        return (
            "当前会话的工具列表里没有插件工具（plugin_*）。"
            "请确认插件已安装、凭据已配置，且已绑定到本会话后再试。"
        )
    return None


def should_continue_for_tool_intent(
    text: str,
    *,
    tools_available: bool,
    had_tool_results: bool,
    nudges: int,
    limit: int = TOOL_INTENT_CONTINUE_LIMIT,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> bool:
    """Auxiliary signal: narrated tool use without a native call.

    The agent loop must NOT use this alone to reopen a turn. Goal completion
    (`should_continue_for_goal` / assess_goal_state) owns stop vs continue.
    This helper only informs optional force_tool_choice after the goal judge
    already returned continue.

    Matching is generic: intent prose / planning-to-act / tool ids / registry
    display names — never product-specific hardcodes.
    """
    if not tools_available or had_tool_results:
        return False
    if nudges >= limit:
        return False
    if looks_like_tool_intent_only(text):
        return True
    plugin_ids = [
        str(p.get("id") or "").strip()
        for p in (plugin_summaries or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    ]
    mentioned = resolve_mentioned_tools(
        text, known_tool_names, known_plugin_ids=plugin_ids
    )
    if not mentioned and plugin_summaries:
        mapped = resolve_display_named_plugin_tools(text, plugin_summaries)
        if known_tool_names:
            known_lower = {str(n).lower() for n in known_tool_names}
            mentioned = [n for n in mapped if n.lower() in known_lower]
        else:
            mentioned = mapped
    if not mentioned:
        return False
    raw = (text or "").strip()
    if not raw or _TOOL_DONE_RE.search(raw):
        return False
    return bool(
        re.search(
            r"(?:需要|将要|将会|准备|打算|使用|调用|借助|通过|"
            r"\b(?:need|will|going\s+to|let\s+me|use|call|invoke)\b)",
            raw,
            re.IGNORECASE,
        )
    )

