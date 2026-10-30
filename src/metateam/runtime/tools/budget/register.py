"""Session MCP/plugin binding, recent successes, and tool-schema budget."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

DEFAULT_TOOL_SCHEMA_THRESHOLD = 20
HOT_SET_LIMIT = 8
RECENT_LIMIT = 24
META_SEARCH = "search_tools"
META_GET_SCHEMA = "get_tool_schema"
EXTENSION_PREFIXES = ("mcp_", "plugin_")


def is_extension_tool(name: str) -> bool:
    n = str(name or "")
    return n.startswith("mcp_") or n.startswith("plugin_")


def normalize_id_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def note_tool_success(recent: list[str], name: str, *, limit: int = RECENT_LIMIT) -> list[str]:
    """Push a successful extension tool name to the front of the recent list."""
    n = str(name or "").strip()
    if not n or not is_extension_tool(n):
        return list(recent or [])
    out = [n] + [x for x in (recent or []) if x != n]
    return out[: max(1, int(limit))]


def extension_source_id(
    name: str,
    *,
    known_plugin_ids: Iterable[str] | None = None,
) -> str:
    """Best-effort source id for mcp_*/plugin_* tool names.

    For plugins, prefer longest match against known plugin ids (ids may contain
    underscores). Falls back to the first path segment after the prefix.
    """
    n = str(name or "")
    if n.startswith("mcp_"):
        body = n[4:]
        return body.split("_", 1)[0] if body else ""
    if n.startswith("plugin_"):
        body = n[7:]
        if not body:
            return ""
        known = sorted(
            {str(x).strip().replace("-", "_").lower() for x in (known_plugin_ids or []) if str(x).strip()},
            key=len,
            reverse=True,
        )
        low = body.lower()
        for kid in known:
            if low == kid or low.startswith(kid + "_"):
                return kid
        return body.split("_", 1)[0]
    return ""


def _catalog_entry(
    tool: Any,
    *,
    known_plugin_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    name = str(getattr(tool, "name", "") or "")
    desc = str(getattr(tool, "description", "") or "")
    one = desc.replace("\n", " ").strip()
    if len(one) > 160:
        one = one[:157] + "..."
    kind = "mcp" if name.startswith("mcp_") else "plugin" if name.startswith("plugin_") else "other"
    source_id = extension_source_id(name, known_plugin_ids=known_plugin_ids)
    return {
        "name": name,
        "kind": kind,
        "source_id": source_id,
        "description": one,
    }


def extension_tool_names(registry: Any) -> list[str]:
    names = []
    for n in registry.names():
        if is_extension_tool(n):
            names.append(n)
    return names


def _plugin_ids_from_summaries(plugin_summaries: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for plugin in plugin_summaries or []:
        if not isinstance(plugin, dict):
            continue
        pid = str(plugin.get("id") or "").strip()
        if not pid:
            continue
        key = pid.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pid)
    return out


def match_mentioned_extension_tools(
    text: str,
    extension_names: Iterable[str],
    *,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Pin extension tools whose plugin/MCP source the user (or prose) named.

    Generic: matches registry tool ids, plugin manifest name/id, and
    ``plugin_<id>_`` / ``mcp_<id>_`` prefixes. Never hardcodes a product brand.
    """
    raw = text or ""
    if not raw:
        return []
    ext = [str(n) for n in extension_names if str(n).strip()]
    if not ext:
        return []
    pinned: list[str] = []
    seen: set[str] = set()

    ext_set = set(ext)

    def _add(name: str) -> None:
        if not name or name not in ext_set or name in seen:
            return
        seen.add(name)
        pinned.append(name)

    known_ids = _plugin_ids_from_summaries(plugin_summaries)

    # 1) Exact tool ids mentioned in prose.
    for name in ext:
        if re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
            raw,
            re.IGNORECASE,
        ):
            _add(name)

    # 1b) Short leaf aliases (manifest tool name without plugin_/mcp_ prefix).
    # Prose often says "generate_dockerfile" instead of the qualified id.
    try:
        from ...goal_completion import tool_leaf_aliases
    except Exception:
        tool_leaf_aliases = None  # type: ignore[assignment]
    if tool_leaf_aliases is not None:
        leaf_map: dict[str, list[str]] = {}
        for name in ext:
            for alias in tool_leaf_aliases(name, known_plugin_ids=known_ids):
                if alias.lower() == name.lower():
                    continue
                leaf_map.setdefault(alias.lower(), []).append(name)
        for leaf, names in leaf_map.items():
            if not re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(leaf) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                continue
            for name in names:
                _add(name)

    # 2) Plugin display name / id from summaries → all tools of that plugin.
    source_keys: set[str] = set()
    for plugin in plugin_summaries or []:
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
        pid = str(plugin.get("id") or "").strip().replace("-", "_").lower()
        if pid:
            source_keys.add(pid)

    # 3) Bare plugin_<id> / mcp_<id> tokens (without a full tool suffix).
    for match in re.finditer(
        r"(?<![A-Za-z0-9_])(plugin|mcp)_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)(?![A-Za-z0-9_])",
        raw,
        re.IGNORECASE,
    ):
        kind = match.group(1).lower()
        body = match.group(2).lower()
        # Prefer longest known plugin id prefix when this looks like plugin_<id>_<tool>
        if kind == "plugin" and known_ids:
            ordered = sorted(
                {x.replace("-", "_").lower() for x in known_ids},
                key=len,
                reverse=True,
            )
            matched_id = None
            for kid in ordered:
                if body == kid or body.startswith(kid + "_"):
                    matched_id = kid
                    break
            if matched_id:
                source_keys.add(matched_id)
                continue
        # Otherwise treat the whole body as a source id candidate (e.g. "gmail").
        source_keys.add(body.split("_", 1)[0] if "_" in body else body)

    if source_keys:
        for name in ext:
            sid = extension_source_id(name, known_plugin_ids=known_ids or source_keys).lower()
            low = name.lower()
            for key in source_keys:
                if sid == key or low.startswith(f"plugin_{key}_") or low.startswith(f"mcp_{key}_") or low in (
                    f"plugin_{key}",
                    f"mcp_{key}",
                ):
                    _add(name)
                    break

    return pinned


def build_hot_set(
    registered_extension: Iterable[str],
    recent: Iterable[str],
    *,
    limit: int = HOT_SET_LIMIT,
    priority: Iterable[str] | None = None,
) -> list[str]:
    """Build the hot schema set.

    Order: mention/priority pins → recent successes → diversify by source id
    across the remaining registered extension tools. Priority pins are always
    kept even when that exceeds ``limit`` (capped softly by priority size).
    """
    have = set(registered_extension)
    hot: list[str] = []

    for n in priority or []:
        if n in have and n not in hot:
            hot.append(n)

    soft_cap = max(int(limit), len(hot))

    for n in recent or []:
        if n in have and n not in hot:
            hot.append(n)
        if len(hot) >= soft_cap:
            break

    if len(hot) < soft_cap:
        # Diversify fill: round-robin by source so one plugin cannot own the budget.
        buckets: dict[str, list[str]] = {}
        order: list[str] = []
        for n in registered_extension:
            if n in hot:
                continue
            sid = extension_source_id(n) or n
            if sid not in buckets:
                buckets[sid] = []
                order.append(sid)
            buckets[sid].append(n)
        idx = 0
        while len(hot) < soft_cap and order:
            sid = order[idx % len(order)]
            bucket = buckets.get(sid) or []
            if bucket:
                hot.append(bucket.pop(0))
            if not bucket:
                order = [s for s in order if s != sid]
                if not order:
                    break
                idx = idx % len(order)
                continue
            idx += 1
    return hot


def pin_mentioned_extension_schemas(
    registry: Any,
    text: str,
    *,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Expand schema focus to include extension tools named/implied by ``text``.

    No-op when search mode is off (``_schema_focus`` is None). Returns pinned names.
    """
    ext = extension_tool_names(registry)
    pinned = match_mentioned_extension_tools(
        text, ext, plugin_summaries=plugin_summaries
    )
    if not pinned:
        return []
    if hasattr(registry, "expand_schema_focus"):
        registry.expand_schema_focus(pinned)
    return pinned


def apply_extension_schema_budget(
    registry: Any,
    *,
    threshold: int = DEFAULT_TOOL_SCHEMA_THRESHOLD,
    recent: Optional[list[str]] = None,
    mention_text: Optional[str] = None,
    plugin_summaries: Optional[list[dict[str, Any]]] = None,
    Tool: Any = None,
) -> dict[str, Any]:
    """If extension tools exceed threshold, expose catalog metas + hot full schemas."""
    ext = extension_tool_names(registry)
    known_ids = _plugin_ids_from_summaries(plugin_summaries)
    info: dict[str, Any] = {
        "extension_count": len(ext),
        "threshold": int(threshold),
        "search_mode": False,
        "hot": [],
        "pinned": [],
    }
    if Tool is None:
        from metateam.runtime.tool_registry import Tool as _Tool

        Tool = _Tool

    for meta in (META_SEARCH, META_GET_SCHEMA):
        if hasattr(registry, "unregister"):
            registry.unregister(meta)
        else:
            registry._tools.pop(meta, None)  # type: ignore[attr-defined]

    summaries = plugin_summaries
    if summaries is None and (mention_text or "").strip():
        try:
            from metateam.plugins.tools import plugin_tool_summary

            summaries = plugin_tool_summary()
            known_ids = _plugin_ids_from_summaries(summaries)
        except Exception:
            summaries = None

    pinned = match_mentioned_extension_tools(
        mention_text or "", ext, plugin_summaries=summaries
    )
    info["pinned"] = list(pinned)

    if len(ext) <= max(0, int(threshold)):
        if hasattr(registry, "set_schema_focus"):
            registry.set_schema_focus(None)
        return info

    hot = build_hot_set(ext, recent or [], limit=HOT_SET_LIMIT, priority=pinned)
    info["hot"] = list(hot)
    info["search_mode"] = True

    catalog = [
        _catalog_entry(registry.get(n), known_plugin_ids=known_ids)
        for n in ext
        if registry.get(n)
    ]

    def _search(
        query: str = "",
        server_id: str = "",
        plugin_id: str = "",
        limit: int = 20,
    ) -> str:
        q = (query or "").strip().lower()
        sid = (server_id or "").strip().lower()
        pid = (plugin_id or "").strip().lower()
        lim = max(1, min(50, int(limit or 20)))
        hits: list[dict[str, Any]] = []
        for row in catalog:
            name = str(row.get("name") or "")
            blob = f"{name} {row.get('description') or ''} {row.get('source_id') or ''}".lower()
            if sid and not (
                row.get("kind") == "mcp"
                and (sid in str(row.get("source_id") or "").lower() or sid in name.lower())
            ):
                continue
            if pid and not (
                row.get("kind") == "plugin"
                and (pid in str(row.get("source_id") or "").lower() or pid in name.lower())
            ):
                continue
            if q and q not in blob:
                continue
            hits.append(row)
            if len(hits) >= lim:
                break
        expand = [str(h.get("name") or "") for h in hits if h.get("name")]
        if hasattr(registry, "expand_schema_focus"):
            registry.expand_schema_focus(expand)
        return json.dumps(
            {
                "count": len(hits),
                "total_extension_tools": len(catalog),
                "matches": hits,
                "hint": "Full schemas for these tools are now available; call them directly.",
            },
            ensure_ascii=False,
        )

    def _get_schema(name: str = "") -> str:
        n = str(name or "").strip()
        tool = registry.get(n) if n else None
        if tool is None:
            return json.dumps({"ok": False, "error": f"unknown tool: {n}"}, ensure_ascii=False)
        if hasattr(registry, "expand_schema_focus"):
            registry.expand_schema_focus([n])
        schema = tool.openai_schema() if hasattr(tool, "openai_schema") else {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        return json.dumps({"ok": True, "schema": schema}, ensure_ascii=False)

    registry.register(
        Tool(
            META_SEARCH,
            "Search available MCP (mcp_*) and plugin (plugin_*) tools by keyword, "
            "server_id, or plugin_id. Returns a short catalog; matching tools become "
            "callable with full schemas on the next step.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to match name/description"},
                    "server_id": {"type": "string", "description": "Filter MCP server id"},
                    "plugin_id": {"type": "string", "description": "Filter local plugin id"},
                    "limit": {"type": "integer", "description": "Max matches (default 20)"},
                },
            },
            _search,
            parallel_safe=True,
            requires_approval=False,
            readonly=True,
        )
    )
    registry.register(
        Tool(
            META_GET_SCHEMA,
            "Fetch the full JSON schema for one MCP/plugin tool by exact name, "
            "and expose it for calling on the next step.",
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact tool name (e.g. mcp_time_get_current_time)",
                    },
                },
                "required": ["name"],
            },
            _get_schema,
            parallel_safe=True,
            requires_approval=False,
            readonly=True,
        )
    )

    if hasattr(registry, "set_schema_focus"):
        focus = {n for n in registry.names() if not is_extension_tool(n)}
        focus.update(hot)
        focus.add(META_SEARCH)
        focus.add(META_GET_SCHEMA)
        registry.set_schema_focus(focus)
    return info


def resolve_binding_ids(
    request_ids: Any,
    *,
    session_ids: Any = None,
    settings_ids: Any = None,
) -> list[str]:
    """Prefer request binding, then session, then settings.

    Empty list means unbound at this layer; callers treat empty as auto-all
    enabled (optional composer filter only when non-empty).
    """
    if request_ids is not None:
        return normalize_id_list(request_ids)
    if session_ids is not None:
        return normalize_id_list(session_ids)
    if settings_ids is not None:
        return normalize_id_list(settings_ids)
    return []
