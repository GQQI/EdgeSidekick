"""Curated open-source MCP server catalog for one-click install into mcp.json.

Package names are verified against the official modelcontextprotocol/servers
README and well-known community packages (npm / PyPI). Do not invent packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .mcp_config import McpServerConfig, McpSetup, load_mcp_config, save_mcp_config, slug_id


@dataclass(frozen=True)
class McpCatalogEntry:
    id: str
    name: str
    description: str
    author: str
    license: str
    homepage: str
    category: str
    tags: tuple[str, ...] = ()
    offline_capable: bool = False
    requires_api_key: bool = False
    requires_network: bool = False
    transport: str = "stdio"
    command: str = "npx"
    args: tuple[str, ...] = ()
    env_placeholders: tuple[str, ...] = ()
    install_hint: str = ""
    package: str = ""  # npm or PyPI package name for matching installed servers

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "homepage": self.homepage,
            "category": self.category,
            "tags": list(self.tags),
            "offline_capable": self.offline_capable,
            "requires_api_key": self.requires_api_key,
            "requires_network": self.requires_network,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "env_placeholders": list(self.env_placeholders),
            "install_hint": self.install_hint,
            "package": self.package,
        }


# Official reference servers (modelcontextprotocol/servers):
# TS/npm: filesystem, memory, sequential-thinking
# Python/PyPI via uvx: fetch, git, time
# SQLite: prefer uvx mcp-server-sqlite (stdlib sqlite3). Node @executeautomation/
# database-server uses native sqlite3 and crashes on Node 24 (no ABI 137 prebuilds).
CATALOG: tuple[McpCatalogEntry, ...] = (
    McpCatalogEntry(
        id="filesystem",
        name="Filesystem",
        description="官方参考实现：在指定目录内安全读写文件。",
        author="Model Context Protocol",
        license="MIT / Apache-2.0",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        category="文件",
        tags=("official", "offline", "files"),
        offline_capable=True,
        requires_network=False,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "."),
        install_hint="首次运行需 npm 拉取包；之后可离线使用。添加前填写允许访问的目录。",
        package="@modelcontextprotocol/server-filesystem",
    ),
    McpCatalogEntry(
        id="memory",
        name="Memory",
        description="官方参考实现：基于知识图谱的本地持久记忆（实体 / 关系 / 观察）。",
        author="Model Context Protocol",
        license="MIT / Apache-2.0",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        category="记忆",
        tags=("official", "offline", "memory"),
        offline_capable=True,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-memory"),
        install_hint="本地 JSONL 知识图谱；首次需 npm 缓存。",
        package="@modelcontextprotocol/server-memory",
    ),
    McpCatalogEntry(
        id="sequential-thinking",
        name="Sequential Thinking",
        description="官方参考实现：分步、可修订的思考链工具，适合复杂推理。",
        author="Model Context Protocol",
        license="MIT / Apache-2.0",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        category="推理",
        tags=("official", "offline", "reasoning"),
        offline_capable=True,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
        install_hint="本地进程，无外网依赖（首次 npx 除外）。",
        package="@modelcontextprotocol/server-sequential-thinking",
    ),
    McpCatalogEntry(
        id="time",
        name="Time",
        description="官方参考实现（Python）：当前时间与 IANA 时区转换。",
        author="Model Context Protocol",
        license="MIT",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        category="工具",
        tags=("official", "offline", "time"),
        offline_capable=True,
        command="uvx",
        args=("mcp-server-time",),
        install_hint="需安装 uv/uvx。包名 mcp-server-time（PyPI），非 npm。",
        package="mcp-server-time",
    ),
    McpCatalogEntry(
        id="fetch",
        name="Fetch",
        description="官方参考实现（Python）：抓取网页并转为 Markdown，供 LLM 阅读。",
        author="Model Context Protocol",
        license="MIT",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        category="网络",
        tags=("official", "network", "web"),
        offline_capable=False,
        requires_network=True,
        command="uvx",
        args=("mcp-server-fetch",),
        install_hint="需联网与 uv/uvx。包名 mcp-server-fetch（PyPI）。",
        package="mcp-server-fetch",
    ),
    McpCatalogEntry(
        id="git",
        name="Git",
        description="官方参考实现（Python）：读取与操作本地 Git 仓库（status / log / diff / commit 等）。",
        author="Model Context Protocol",
        license="MIT",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        category="开发",
        tags=("official", "offline", "git"),
        offline_capable=True,
        command="uvx",
        # No bare "." — install must supply a real repo path (cwd of Sidekick is often non-git).
        args=("mcp-server-git", "--repository", ""),
        install_hint="必须填写本机已有 Git 仓库的绝对路径（不能是 .）。需 uv/uvx。",
        package="mcp-server-git",
    ),
    McpCatalogEntry(
        id="sqlite",
        name="SQLite / Database",
        description="官方归档参考实现（Python/PyPI）：本地 SQLite 查询与洞察。用 uvx 避免 Node sqlite3 原生绑定在 Node 24 上崩溃。",
        author="Model Context Protocol",
        license="MIT",
        homepage="https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite",
        category="数据库",
        tags=("official", "offline", "sqlite"),
        offline_capable=True,
        command="uvx",
        args=("mcp-server-sqlite", "--db-path", "./data.db"),
        install_hint="添加前填写本地 .db 路径。需 uv/uvx。勿用 @executeautomation/database-server（依赖 sqlite3，Node 24 无预编译绑定会崩溃）。",
        package="mcp-server-sqlite",
    ),
    McpCatalogEntry(
        id="brave-search",
        name="Brave Search",
        description="Brave 官方开源搜索 MCP（MIT）：网页 / 图片 / 新闻等。需要 Brave Search API Key。",
        author="Brave Software",
        license="MIT",
        homepage="https://github.com/brave/brave-search-mcp-server",
        category="搜索",
        tags=("community", "network", "search"),
        offline_capable=False,
        requires_api_key=True,
        requires_network=True,
        command="npx",
        args=("-y", "@brave/brave-search-mcp-server", "--transport", "stdio"),
        env_placeholders=("BRAVE_API_KEY",),
        install_hint="在 https://brave.com/search/api/ 申请密钥，安装时填入 BRAVE_API_KEY。",
        package="@brave/brave-search-mcp-server",
    ),
)



def _arg_after_flag(args: list[str], flag: str) -> str:
    try:
        idx = args.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(args):
        return ""
    return str(args[idx + 1]).strip()


def _is_placeholder_path(path: str) -> bool:
    p = (path or "").strip()
    if not p:
        return True
    low = p.lower()
    if p in (".", "..") or low in ("<repo-path>", "<path>", "/path/to/repo"):
        return True
    if p.startswith("<") and p.endswith(">"):
        return True
    return False


def validate_catalog_install_args(entry: McpCatalogEntry, final_args: list[str]) -> None:
    """Reject catalog installs that would launch with unusable path defaults."""
    if entry.id == "git":
        repo = _arg_after_flag(final_args, "--repository")
        if not repo and final_args:
            # positional fallback (rare)
            last = str(final_args[-1]).strip()
            if last and last != "mcp-server-git" and not last.startswith("-"):
                repo = last
        if _is_placeholder_path(repo):
            raise ValueError(
                "Git MCP 需要有效的本地仓库绝对路径（不能为空或 .）。"
                "请在添加前填写真实 Git 仓库路径。"
            )
        return
    if entry.id == "sqlite":
        db = _arg_after_flag(final_args, "--db-path")
        if not db and final_args:
            last = str(final_args[-1]).strip()
            if last and not last.startswith("-") and last not in (
                "mcp-server-sqlite",
                "@executeautomation/database-server",
                "-y",
            ):
                db = last
        if _is_placeholder_path(db):
            raise ValueError("SQLite MCP 需要有效的数据库文件路径（不能为空或 .）。")
        return
    if entry.id == "filesystem":
        path = str(final_args[-1]).strip() if final_args else ""
        if _is_placeholder_path(path):
            raise ValueError("Filesystem MCP 需要至少一个允许访问的目录路径。")


def get_entry(catalog_id: str) -> Optional[McpCatalogEntry]:
    cid = (catalog_id or "").strip()
    for entry in CATALOG:
        if entry.id == cid:
            return entry
    return None


def _package_tokens(server: McpServerConfig) -> set[str]:
    tokens: set[str] = set()
    for a in server.args or []:
        s = str(a).strip()
        if s:
            tokens.add(s)
    if server.command:
        tokens.add(server.command.strip())
    return tokens


def is_entry_installed(entry: McpCatalogEntry, setup: Optional[McpSetup] = None) -> bool:
    setup = setup if setup is not None else load_mcp_config()
    for s in setup.servers:
        if s.id == entry.id:
            return True
        tokens = _package_tokens(s)
        if entry.package and entry.package in tokens:
            return True
    return False


def list_catalog(user_id: Optional[str] = None) -> dict[str, Any]:
    setup = load_mcp_config(user_id)
    items = []
    for entry in CATALOG:
        d = entry.public()
        d["installed"] = is_entry_installed(entry, setup)
        items.append(d)
    categories = sorted({e.category for e in CATALOG})
    return {"items": items, "categories": categories}


def install_from_catalog(
    catalog_id: str,
    *,
    args: Optional[list[str]] = None,
    env: Optional[dict[str, str]] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    entry = get_entry(catalog_id)
    if not entry:
        raise ValueError(f"目录中没有 MCP 条目：{catalog_id}")

    setup = load_mcp_config(user_id)
    if is_entry_installed(entry, setup):
        return {
            "status": "already_installed",
            "id": entry.id,
            **setup.public_dict(),
            "catalog": list_catalog(user_id),
        }

    final_args = list(args) if args is not None else list(entry.args)
    validate_catalog_install_args(entry, final_args)
    final_env: dict[str, str] = {}
    for key in entry.env_placeholders:
        final_env[key] = ""
    if env:
        for k, v in env.items():
            final_env[str(k)] = str(v)

    server = McpServerConfig(
        id=slug_id(entry.id),
        name=entry.name,
        transport=entry.transport,
        command=entry.command,
        args=final_args,
        env=final_env,
        enabled=True,
    )
    setup.servers.append(server)
    save_mcp_config(setup, user_id)
    return {
        "status": "ok",
        "id": entry.id,
        **setup.public_dict(),
        "catalog": list_catalog(user_id),
    }


def uninstall_from_catalog(catalog_id: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
    entry = get_entry(catalog_id)
    if not entry:
        raise ValueError(f"目录中没有 MCP 条目：{catalog_id}")

    setup = load_mcp_config(user_id)
    kept: list[McpServerConfig] = []
    removed = False
    for s in setup.servers:
        if s.id == entry.id or (entry.package and entry.package in _package_tokens(s)):
            removed = True
            continue
        kept.append(s)
    if not removed:
        return {
            "status": "not_installed",
            "id": entry.id,
            **setup.public_dict(),
            "catalog": list_catalog(user_id),
        }
    setup.servers = kept
    save_mcp_config(setup, user_id)
    return {
        "status": "ok",
        "id": entry.id,
        **setup.public_dict(),
        "catalog": list_catalog(user_id),
    }
