"""Register plugin-shipped MCP servers into the tenant mcp.json store.

Plugins may declare an optional ``mcp`` block in manifest.json::

    {
      "mcp": {
        "id": "my-mcp",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "some-mcp-server"],
        "env": {}
      }
    }

On install/enable we write it with ``source: plugin``. On uninstall we remove
only entries with matching id and source=plugin.
"""

from __future__ import annotations

from typing import Any, Optional

from ..services.mcp_config import (
    McpServerConfig,
    load_mcp_config,
    save_mcp_config,
    slug_id,
)


def _mcp_block(manifest: Any) -> Optional[dict[str, Any]]:
    raw = None
    if hasattr(manifest, "mcp"):
        raw = getattr(manifest, "mcp")
    elif isinstance(manifest, dict):
        raw = manifest.get("mcp")
    # Manifest dataclass may not have mcp yet — read from public/raw if present
    if raw is None and hasattr(manifest, "raw_mcp"):
        raw = getattr(manifest, "raw_mcp")
    if not isinstance(raw, dict):
        return None
    return raw


def sync_plugin_mcp(plugin_id: str, manifest: Any, *, enabled: bool, user_id: Optional[str] = None) -> bool:
    """Upsert or remove a plugin-sourced MCP server. Returns True if config changed."""
    block = _mcp_block(manifest)
    if not block:
        return False
    setup = load_mcp_config(user_id)
    sid = slug_id(str(block.get("id") or plugin_id), fallback=plugin_id)
    others = [s for s in setup.servers if not (s.id == sid and (s.source or "user") == "plugin")]
    if not enabled:
        if len(others) == len(setup.servers):
            return False
        setup.servers = others
        save_mcp_config(setup, user_id)
        return True
    transport = str(block.get("transport") or block.get("type") or "stdio").strip().lower()
    server = McpServerConfig(
        id=sid,
        name=str(block.get("name") or getattr(manifest, "name", None) or sid),
        transport=transport,
        command=str(block.get("command") or ""),
        args=[str(a) for a in (block.get("args") or [])],
        env={str(k): str(v) for k, v in (block.get("env") or {}).items()},
        url=str(block.get("url") or ""),
        headers={str(k): str(v) for k, v in (block.get("headers") or {}).items()},
        enabled=True,
        source="plugin",
    )
    others.append(server)
    setup.servers = others
    save_mcp_config(setup, user_id)
    return True


def remove_plugin_mcp(plugin_id: str, *, user_id: Optional[str] = None) -> bool:
    setup = load_mcp_config(user_id)
    sid = slug_id(plugin_id, fallback=plugin_id)
    kept = [
        s
        for s in setup.servers
        if not ((s.id == sid or s.id == plugin_id) and (s.source or "user") == "plugin")
    ]
    if len(kept) == len(setup.servers):
        return False
    setup.servers = kept
    save_mcp_config(setup, user_id)
    return True
