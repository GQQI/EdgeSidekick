"""Per-tenant MCP server configuration (stdio + remote http/sse).

MCP server JSON shapes:
- Local:  { command, args, env }
- Remote: { type: http|sse, url, headers }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...core.netguard import blocked_http_reason
from ...session.tenant_context import tenant_mcp_path

_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def slug_id(raw: str, fallback: str = "mcp") -> str:
    s = _ID_RE.sub("-", (raw or "").strip()).strip("-").lower()
    return (s or fallback)[:48]


@dataclass
class McpServerConfig:
    id: str
    name: str
    transport: str = "stdio"  # stdio | http | sse
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    # "user" | "catalog" | "plugin" — plugins that ship MCP write source=plugin
    source: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "url": self.url,
            "headers": dict(self.headers),
            "enabled": self.enabled,
            "source": self.source or "user",
        }

    def is_remote(self) -> bool:
        return self.transport in ("http", "sse", "streamable-http")

    def normalized_transport(self) -> str:
        t = (self.transport or "stdio").strip().lower()
        if t in ("streamable-http", "streamable_http"):
            return "http"
        if t in ("http", "sse", "stdio"):
            return t
        # Infer from fields
        if self.url and not self.command:
            return "http"
        return "stdio"


@dataclass
class McpSetup:
    version: int = 2
    servers: list[McpServerConfig] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        servers = [s.to_dict() for s in self.servers]
        mcp_servers: dict[str, Any] = {}
        for s in self.servers:
            entry: dict[str, Any] = {}
            transport = s.normalized_transport()
            if transport == "stdio":
                entry["command"] = s.command
                if s.args:
                    entry["args"] = list(s.args)
                if s.env:
                    entry["env"] = dict(s.env)
            else:
                entry["type"] = transport
                entry["url"] = s.url
                if s.headers:
                    entry["headers"] = dict(s.headers)
            # Sidekick extras (ignored by Claude Desktop)
            entry["disabled"] = not bool(s.enabled)
            if s.source and s.source != "user":
                entry["source"] = s.source
            mcp_servers[s.id] = entry
        return {
            "version": self.version,
            "servers": servers,
            "mcpServers": mcp_servers,
        }

    def public_dict(self) -> dict[str, Any]:
        servers = []
        for s in self.servers:
            d = s.to_dict()
            env = d.get("env") or {}
            headers = d.get("headers") or {}
            d["env"] = {k: ("***" if v else "") for k, v in env.items()}
            d["env_keys"] = list(env.keys())
            d["headers"] = {k: ("***" if v else "") for k, v in headers.items()}
            d["header_keys"] = list(headers.keys())
            servers.append(d)
        return {"version": self.version, "servers": servers}


def _parse_server(raw: dict[str, Any]) -> Optional[McpServerConfig]:
    sid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or sid).strip()
    transport = str(raw.get("transport") or raw.get("type") or "stdio").strip().lower()
    if transport in ("streamable-http", "streamable_http"):
        transport = "http"
    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not sid:
        sid = slug_id(name or command or url or "mcp")
    if not name:
        name = sid

    # Accept either local or remote
    if transport == "stdio":
        if not command and url:
            transport = "http"
        elif not command:
            return None
    else:
        if not url:
            return None

    args = raw.get("args") if isinstance(raw.get("args"), list) else []
    env_raw = raw.get("env") if isinstance(raw.get("env"), dict) else {}
    hdr_raw = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
    source = str(raw.get("source") or "user").strip().lower() or "user"
    if source not in ("user", "catalog", "plugin"):
        source = "user"
    return McpServerConfig(
        id=sid,
        name=name,
        transport=transport,
        command=command,
        args=[str(a) for a in args],
        env={str(k): str(v) for k, v in env_raw.items()},
        url=url,
        headers={str(k): str(v) for k, v in hdr_raw.items()},
        enabled=bool(raw.get("enabled", True)),
        source=source,
    )


def _community_servers(raw: Any) -> list[dict[str, Any]]:
    """Claude Desktop / Cursor style ``mcpServers`` map → list of server dicts."""
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item.setdefault("id", str(key))
        item.setdefault("name", str(key))
        # Community configs often use ``type`` instead of ``transport``.
        if "transport" not in item and "type" in item:
            item["transport"] = item.get("type")
        # Map community ``disabled`` → Sidekick ``enabled``.
        if "enabled" not in item and "disabled" in item:
            item["enabled"] = not bool(item.get("disabled"))
        item.setdefault("enabled", True)
        out.append(item)
    return out


def _parse(data: dict[str, Any]) -> McpSetup:
    servers: list[McpServerConfig] = []
    seen: set[str] = set()
    # Prefer explicit servers list; also merge community mcpServers (list wins on id clash).
    raw_list: list[Any] = []
    if isinstance(data.get("servers"), list):
        raw_list.extend(data.get("servers") or [])
    raw_list.extend(_community_servers(data.get("mcpServers")))
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_server(raw)
        if not parsed or parsed.id in seen:
            continue
        seen.add(parsed.id)
        servers.append(parsed)
    return McpSetup(version=int(data.get("version") or 2), servers=servers)


def load_mcp_config(user_id: Optional[str] = None) -> McpSetup:
    from ...core.config import ROOT
    from ...session.tenant_context import DEFAULT_USER_ID, get_user_id

    uid = user_id if user_id is not None else get_user_id()
    path = tenant_mcp_path(uid)
    if not path.exists() and uid in (None, DEFAULT_USER_ID, "default"):
        legacy = ROOT / "data" / "mcp.json"
        if legacy.exists():
            path = legacy
    if not path.exists():
        return McpSetup()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _parse(data)
    except Exception:
        pass
    return McpSetup()


def save_mcp_config(setup: McpSetup, user_id: Optional[str] = None) -> Path:
    path = tenant_mcp_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(setup.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _merge_secret_map(
    incoming: dict[str, str], previous: dict[str, str]
) -> dict[str, str]:
    merged = dict(previous)
    for k, v in incoming.items():
        if v == "***":
            continue
        merged[k] = v
    # Drop keys removed from incoming (UI deleted them)
    keep = set(incoming.keys())
    return {k: v for k, v in merged.items() if k in keep or k not in previous}


def _reject_private_remote(server: McpServerConfig) -> None:
    if server.normalized_transport() not in ("http", "sse"):
        return
    reason = blocked_http_reason(server.url, allow_loopback=False)
    if reason:
        raise ValueError(
            f"MCP server {server.id or server.name}: remote URL blocked ({reason}). "
            "Use stdio for local servers."
        )


def update_mcp_config(payload: dict[str, Any], user_id: Optional[str] = None) -> McpSetup:
    setup = _parse(payload if isinstance(payload, dict) else {})
    prev = {s.id: s for s in load_mcp_config(user_id).servers}
    for s in setup.servers:
        old = prev.get(s.id)
        if not old:
            continue
        s.env = _merge_secret_map(s.env, old.env)
        s.headers = _merge_secret_map(s.headers, old.headers)
    for s in setup.servers:
        _reject_private_remote(s)
    save_mcp_config(setup, user_id)
    return setup
