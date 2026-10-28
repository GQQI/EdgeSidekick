"""MCP client - stdio + remote HTTP/SSE for Agent ToolRegistry."""

from __future__ import annotations

import asyncio
import threading
import time
import shutil
import subprocess
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from .mcp_config import McpServerConfig, McpSetup, load_mcp_config

_log = logging.getLogger("metateam.mcp")

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")

# Soft-fail / debounce: one bad MCP must not spam crash loops on every list_tools.
_UNHEALTHY_TTL_S = 300.0
_unhealthy_until: dict[str, float] = {}
_unhealthy_reason: dict[str, str] = {}
_unhealthy_announced: set[str] = set()
_node_major_cache: Optional[int] = None
_node_major_checked = False


def _mark_unhealthy(server_id: str, reason: str) -> None:
    sid = (server_id or "").strip() or "unknown"
    _unhealthy_until[sid] = time.monotonic() + _UNHEALTHY_TTL_S
    _unhealthy_reason[sid] = (reason or "unknown error")[:500]
    if sid not in _unhealthy_announced:
        _log.warning(
            "MCP %s marked unhealthy for %.0fs: %s",
            sid,
            _UNHEALTHY_TTL_S,
            _unhealthy_reason[sid],
        )
        _unhealthy_announced.add(sid)


def _clear_unhealthy(server_id: str) -> None:
    sid = (server_id or "").strip() or "unknown"
    _unhealthy_until.pop(sid, None)
    _unhealthy_reason.pop(sid, None)
    _unhealthy_announced.discard(sid)


def _is_unhealthy(server_id: str) -> Optional[str]:
    sid = (server_id or "").strip() or "unknown"
    until = _unhealthy_until.get(sid)
    if until is None:
        return None
    if time.monotonic() >= until:
        _clear_unhealthy(sid)
        return None
    return _unhealthy_reason.get(sid) or "previously failed"



def _is_benign_stdio_pipe_error(exc: BaseException | None) -> bool:
    """True for Windows Proactor pipe resets when an MCP stdio child dies.

    asyncio logs these from _ProactorBasePipeTransport._call_connection_lost
    as loud ConnectionResetError / WinError 10054 tracebacks even though the
    soft-fail path already recorded the unhealthy reason.
    """
    if exc is None:
        return False
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, BrokenPipeError):
        return True
    if isinstance(exc, OSError):
        winerr = getattr(exc, "winerror", None)
        if winerr == 10054:  # WSAECONNRESET
            return True
        # ECONNRESET on POSIX (104 Linux, 54 macOS)
        if getattr(exc, "errno", None) in (104, 54):
            return True
    # ExceptionGroup / BaseExceptionGroup may wrap pipe resets on 3.11+
    sub = getattr(exc, "exceptions", None)
    if sub:
        try:
            return all(_is_benign_stdio_pipe_error(e) for e in sub)
        except Exception:  # noqa: BLE001
            return False
    return False


def _install_quiet_stdio_pipe_handler() -> None:
    """Swallow benign pipe-reset callbacks for the current running loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    prev = loop.get_exception_handler()

    def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if _is_benign_stdio_pipe_error(exc if isinstance(exc, BaseException) else None):
            _log.debug("MCP stdio pipe closed (ignored): %s", exc)
            return
        msg = str(context.get("message") or "")
        if exc is None and "_call_connection_lost" in msg:
            _log.debug("MCP stdio connection_lost (ignored): %s", msg)
            return
        if prev is not None:
            prev(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


def _node_major() -> Optional[int]:
    """Best-effort Node.js major version (cached)."""
    global _node_major_cache, _node_major_checked
    if _node_major_checked:
        return _node_major_cache
    _node_major_checked = True
    node = shutil.which("node")
    if not node:
        _node_major_cache = None
        return None
    try:
        proc = subprocess.run(
            [node, "-p", "process.versions.node"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ver = (proc.stdout or "").strip().split(".")[0]
        _node_major_cache = int(ver) if ver.isdigit() else None
    except Exception:  # noqa: BLE001
        _node_major_cache = None
    return _node_major_cache


def _arg_after_flag(args: list[str], flag: str) -> str:
    try:
        idx = list(args).index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(args):
        return ""
    return str(args[idx + 1]).strip()


def _set_arg_after_flag(args: list[str], flag: str, value: str) -> list[str]:
    out = list(args)
    try:
        idx = out.index(flag)
    except ValueError:
        return out
    if idx + 1 >= len(out):
        out.append(value)
    else:
        out[idx + 1] = value
    return out


def _workspace_root() -> Any:
    from pathlib import Path as _P

    from ..core.config import get_settings

    return get_settings().workspace.resolve()


def _looks_like_git_repo(path: str) -> bool:
    from pathlib import Path as _P

    try:
        p = _P(path).expanduser()
        if not p.exists():
            return False
        # Walk up a few levels for worktrees / nested paths
        cur = p.resolve() if p.is_dir() else p.parent.resolve()
        for _ in range(6):
            if (cur / ".git").exists():
                return True
            if cur.parent == cur:
                break
            cur = cur.parent
    except Exception:  # noqa: BLE001
        return False
    return False


def _resolve_relative_path_args(args: list[str]) -> list[str]:
    """Map '.' / relative --repository and --db-path onto the active workspace."""
    from pathlib import Path as _P

    out = [str(a) for a in args]
    try:
        ws = _workspace_root()
    except Exception:  # noqa: BLE001
        return out

    repo = _arg_after_flag(out, "--repository")
    if repo in ("", ".", ".."):
        out = _set_arg_after_flag(out, "--repository", str(ws))
    elif repo and not _P(repo).expanduser().is_absolute():
        out = _set_arg_after_flag(out, "--repository", str((ws / repo).resolve()))

    db = _arg_after_flag(out, "--db-path")
    if db and not _P(db).expanduser().is_absolute():
        resolved = (ws / db).resolve()
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        out = _set_arg_after_flag(out, "--db-path", str(resolved))
    return out


def _preflight_server(server: McpServerConfig) -> Optional[str]:
    """Return a human-readable skip reason, or None if OK to launch.

    Soft-fails known broken configs (git non-repo path, Node24+sqlite3 native)
    instead of spawning processes that crash-loop. Relative '.' paths are
    rewritten to the active workspace before the check.
    """
    transport = server.normalized_transport()
    if transport != "stdio":
        return None
    args = _resolve_relative_path_args([str(a) for a in (server.args or [])])
    joined = " ".join(args).lower()
    cmd = (server.command or "").strip().lower()

    # --- Git: missing / non-repo path (after workspace rewrite) ---
    is_git = (
        server.id == "git"
        or "mcp-server-git" in joined
        or any(a == "mcp-server-git" for a in args)
    )
    if is_git:
        repo = _arg_after_flag(args, "--repository")
        if not repo and args:
            last = args[-1]
            if last and last != "mcp-server-git" and not last.startswith("-"):
                repo = last
        if not repo:
            return "Git 仓库路径未配置。请在 MCP 设置里为 --repository 填写真实本地仓库路径。"
        if not _looks_like_git_repo(repo):
            return (
                f"Git 路径不是有效仓库：{repo}。"
                "请在 MCP 设置中改为含 .git 的目录，或禁用该服务器。"
            )

    # --- SQLite via @executeautomation/database-server (native sqlite3) ---
    uses_exec_sqlite = (
        "@executeautomation/database-server" in joined
        or "@executeautomation/database-server" in " ".join(args)
    )
    if uses_exec_sqlite and (cmd in ("npx", "npm", "node") or cmd.endswith("npx") or cmd.endswith("npm.cmd")):
        major = _node_major()
        if major is not None and major >= 24:
            return (
                f"SQLite MCP（@executeautomation/database-server）依赖原生 sqlite3，"
                f"当前 Node.js {major} 无预编译包。请改用目录中的 "
                "「SQLite / Database」（uvx mcp-server-sqlite），或换 Node 20 LTS。"
            )

    return None





def _format_mcp_exc(exc: BaseException) -> str:
    """Flatten ExceptionGroup / TaskGroup errors into a readable one-liner."""
    parts: list[str] = []

    def _walk(e: BaseException) -> None:
        sub = getattr(e, "exceptions", None)
        if sub:
            for child in sub:
                if isinstance(child, BaseException):
                    _walk(child)
            return
        parts.append(f"{type(e).__name__}: {e}")

    try:
        _walk(exc)
    except Exception:  # noqa: BLE001
        parts = [str(exc)]
    msg = "; ".join(parts) if parts else str(exc)
    # Known: archived mcp-server-sqlite vs mcp>=2 (Server.list_resources removed).
    if "list_resources" in msg or "has no attribute 'list_resources'" in msg:
        msg = (
            f"{msg} — mcp-server-sqlite is incompatible with the mcp 2.x SDK. "
            "Use uvx --with 'mcp<2' mcp-server-sqlite (catalog default), then re-test."
        )
    return msg[:500]


def _stdio_command_args(server: McpServerConfig) -> tuple[str, list[str]]:
    """Return (command, args), resolving workspace paths + pinning mcp<2 for sqlite.

    mcp-server-sqlite (archived official) still calls Server.list_resources, which
    was removed in mcp 2.x. Without --with mcp<2, uvx resolves mcp 2.3+ and the
    child crashes — tools never register while Settings still show enabled.
    """
    cmd = (server.command or "").strip()
    args = _resolve_relative_path_args([str(a) for a in (server.args or [])])
    cmd_l = cmd.lower()
    if cmd_l not in ("uvx", "uv") and not cmd_l.endswith("uvx.exe") and not cmd_l.endswith("uv.exe"):
        return cmd, args
    if "mcp-server-sqlite" not in args:
        return cmd, args
    # Respect an explicit --with that already mentions mcp.
    for i, a in enumerate(args):
        if a == "--with" and i + 1 < len(args) and "mcp" in str(args[i + 1]).lower():
            return cmd, args
    out: list[str] = []
    inserted = False
    for a in args:
        if not inserted and a == "mcp-server-sqlite":
            out.extend(["--with", "mcp<2"])
            inserted = True
        out.append(a)
    if inserted:
        _log.info(
            "MCP %s: auto-inserted uvx --with mcp<2 for mcp-server-sqlite compatibility",
            server.id,
        )
    return cmd, out if inserted else args


def get_server_health(server_id: str = "") -> dict[str, Any]:
    """Public health snapshot for Settings / prompt honesty."""
    sid = (server_id or "").strip()
    if sid:
        reason = _is_unhealthy(sid)
        return {"id": sid, "ok": reason is None, "error": reason}
    out: dict[str, Any] = {}
    for key in list(_unhealthy_reason.keys()):
        reason = _is_unhealthy(key)
        if reason:
            out[key] = {"ok": False, "error": reason}
    return out



def _run_coro(coro: Any, timeout: float = 120.0) -> Any:
    """Run an async MCP coroutine from sync code with a hard timeout.

    IMPORTANT: FastAPI sync routes and Agent.__init__ often have no running
    event loop. The previous path called bare ``asyncio.run(coro)`` in that
    case, so a hung stdio MCP (e.g. npx sqlite install / EPERM) could block
    a worker thread forever and starve /api/skills, /api/mcp, /api/model.

    Uses a *daemon* thread so a timed-out MCP cannot pin the process or the
    caller's thread pool while npx/stdio keeps hanging in the background.
    """

    box: dict[str, Any] = {}
    done = threading.Event()

    def _runner() -> None:
        async def _wrapped() -> Any:
            # Quiet Windows Proactor _call_connection_lost spam when MCP child dies.
            # Real failures still propagate from await coro into box["exc"].
            _install_quiet_stdio_pipe_handler()
            return await coro

        try:
            box["value"] = asyncio.run(_wrapped())
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=_runner, name="sidekick-mcp", daemon=True).start()
    if not done.wait(timeout=timeout):
        raise TimeoutError(f"MCP operation timed out after {timeout:.0f}s")
    if "exc" in box:
        raise box["exc"]
    return box.get("value")


def mcp_tool_name(server_id: str, tool_name: str) -> str:
    sid = _SAFE_NAME.sub("_", server_id).strip("_") or "srv"
    tname = _SAFE_NAME.sub("_", tool_name).strip("_") or "tool"
    return f"mcp_{sid}_{tname}"[:64]


@dataclass
class McpToolInfo:
    server_id: str
    server_name: str
    name: str
    qualified: str
    description: str
    input_schema: dict[str, Any]


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401

        return True
    except ImportError:
        return False


@asynccontextmanager
async def _open_session(server: McpServerConfig) -> AsyncIterator[Any]:
    from mcp import ClientSession

    transport = server.normalized_transport()
    if transport in ("http", "sse"):
        from ..core.netguard import blocked_http_reason

        reason = blocked_http_reason(server.url, allow_loopback=False)
        if reason:
            raise ValueError(
                f"MCP remote URL blocked ({reason}); use stdio for local servers"
            )

    if transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, **(server.env or {})}
        launch_cmd, launch_args = _stdio_command_args(server)
        params = StdioServerParameters(
            command=launch_cmd,
            args=list(launch_args),
            env=env,
        )
        _install_quiet_stdio_pipe_handler()
        yielded = False
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yielded = True
                    yield session
        except BaseException as exc:  # noqa: BLE001
            # After a successful session, pipe reset on close is expected when the
            # child already exited. Before yield, still a real connect failure.
            if yielded and _is_benign_stdio_pipe_error(exc):
                _log.debug("MCP stdio cleanup pipe reset for %s: %s", server.id, exc)
                return
            raise
        return

    if transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(server.url, headers=dict(server.headers or {}) or None) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    # http / streamable-http
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    headers = dict(server.headers or {})
    http_client = create_mcp_http_client(headers=headers or None)
    async with http_client:
        async with streamable_http_client(server.url, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _list_tools_async(
    server: McpServerConfig, *, timeout: float = 18.0
) -> list[McpToolInfo]:
    async def _inner() -> list[McpToolInfo]:
        tools: list[McpToolInfo] = []
        async with _open_session(server) as session:
            result = await session.list_tools()
            for t in result.tools or []:
                schema = (
                    t.inputSchema
                    if isinstance(getattr(t, "inputSchema", None), dict)
                    else {"type": "object", "properties": {}}
                )
                tools.append(
                    McpToolInfo(
                        server_id=server.id,
                        server_name=server.name,
                        name=t.name,
                        qualified=mcp_tool_name(server.id, t.name),
                        description=(t.description or f"MCP:{server.name}/{t.name}")[:500],
                        input_schema=schema,
                    )
                )
        return tools

    # Inner wait_for so a stuck initialize/list_tools cannot outlive the
    # thread-level timeout (stdio npx can ignore cancellation otherwise).
    return await asyncio.wait_for(_inner(), timeout=max(5.0, float(timeout)))


async def _call_tool_async(
    server: McpServerConfig, tool_name: str, arguments: dict[str, Any]
) -> str:
    async with _open_session(server) as session:
        result = await session.call_tool(tool_name, arguments=arguments or {})
        parts: list[str] = []
        for block in result.content or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
            else:
                parts.append(str(block))
        out = "\n".join(parts).strip() or "(empty MCP result)"
        if getattr(result, "isError", False):
            return f"ERROR: MCP tool failed\n{out}"
        if len(out) > 14_000:
            out = out[:14_000] + "\n...[truncated]"
        return out


def list_enabled_tools(
    setup: Optional[McpSetup] = None,
    *,
    server_ids: Optional[list[str]] = None,
) -> list[McpToolInfo]:
    """List tools from enabled MCP servers.

    ``server_ids``: None = all enabled; [] = none; list = subset. Implements 启用 ≠ 绑定: when provided, only those server
    ids are contacted even if other servers are marked enabled in config.
    """
    if not _mcp_available():
        _log.warning("mcp package not installed - MCP tools disabled")
        return []
    cfg = setup or load_mcp_config()
    # None = all enabled; explicit list (including empty) = only those ids.
    if server_ids is None:
        allow = None
    else:
        allow = {str(x).strip() for x in server_ids if str(x).strip()}
    out: list[McpToolInfo] = []
    for server in cfg.servers:
        if not server.enabled:
            continue
        if allow is not None and server.id not in allow:
            continue
        transport = server.normalized_transport()
        if transport == "stdio" and not (server.command or "").strip():
            continue
        if transport in ("http", "sse") and not (server.url or "").strip():
            continue
        unhealthy = _is_unhealthy(server.id)
        if unhealthy:
            _log.debug("MCP skip unhealthy %s: %s", server.id, unhealthy)
            continue
        reason = _preflight_server(server)
        if reason:
            _mark_unhealthy(server.id, reason)
            continue
        try:
            # Keep per-server budget short so one broken MCP (sqlite/npx)
            # cannot stall agent boot / exhaust the API thread pool.
            tools = _run_coro(_list_tools_async(server, timeout=18.0), timeout=20.0)
            _clear_unhealthy(server.id)
            out.extend(tools)
        except BaseException as exc:  # noqa: BLE001 - include ExceptionGroup / TimeoutError
            msg = _format_mcp_exc(exc)
            # Enrich known Node native-binding crashes for clearer logs.
            if "Could not locate the bindings file" in msg or "node-v137" in msg:
                msg = (
                    f"{msg} - native sqlite3/better-sqlite3 missing for this Node ABI; "
                    "use uvx --with 'mcp<2' mcp-server-sqlite or Node 20 LTS."
                )
            if "timed out" in msg.lower() or "TimeoutError" in msg:
                msg = f"连接超时：{msg}"
            _mark_unhealthy(server.id, msg)
            _log.warning("MCP list_tools failed for %s: %s", server.id, msg)
    return out


def call_tool(server: McpServerConfig, tool_name: str, arguments: dict[str, Any]) -> str:
    if not _mcp_available():
        return "ERROR: mcp package not installed (pip install mcp)"
    try:
        return _run_coro(_call_tool_async(server, tool_name, arguments), timeout=120.0)
    except BaseException as exc:  # noqa: BLE001
        return f"ERROR: MCP call failed: {exc}"


def test_server(server: McpServerConfig) -> dict[str, Any]:
    if not _mcp_available():
        return {"ok": False, "error": "未安装 mcp 包（pip install mcp）"}
    # Explicit test clears prior unhealthy so the user can re-check after editing paths.
    _clear_unhealthy(server.id)
    reason = _preflight_server(server)
    if reason:
        _mark_unhealthy(server.id, reason)
        return {"ok": False, "error": reason}
    cmd = (server.command or "").strip().lower()
    # uvx cold-start (first package pull) often exceeds 20s.
    budget = 60.0 if cmd in ("uvx", "uv") or cmd.endswith("uvx.exe") or cmd.endswith("uv.exe") else 30.0
    try:
        tools = _run_coro(
            _list_tools_async(server, timeout=max(15.0, budget - 5.0)),
            timeout=budget,
        )
        _clear_unhealthy(server.id)
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": [{"name": t.name, "qualified": t.qualified} for t in tools[:40]],
        }
    except BaseException as exc:  # noqa: BLE001
        msg = _format_mcp_exc(exc)
        if "timed out" in msg.lower() or "TimeoutError" in msg:
            msg = (
                f"连接超时（{int(budget)}s）。若首次使用 uvx，请确认本机已安装 uv/uvx 后重试；"
                f"也可检查 --db-path / --repository 是否指向有效路径。原始错误：{msg}"
            )
        if "Could not locate the bindings file" in msg or "node-v137" in msg:
            msg = (
                f"{msg} — 原生 sqlite3 与当前 Node ABI 不兼容；"
                "请改用目录中的 SQLite（uvx --with 'mcp<2' mcp-server-sqlite）或 Node 20 LTS。"
            )
        _mark_unhealthy(server.id, msg)
        return {"ok": False, "error": msg}


def register_mcp_tools(
    registry: Any,
    *,
    Tool: Any,
    setup: Optional[McpSetup] = None,
    server_ids: Optional[list[str]] = None,
) -> int:
    """Register MCP tools into the agent ToolRegistry.

    Always Ask-First for MCP (external side effects). Callers pass
    ``server_ids`` to bind a subset of enabled servers for this session.
    """
    # Drop previous MCP tools so disable / rebind mid-session takes effect.
    if hasattr(registry, "drop_prefix"):
        registry.drop_prefix("mcp_")
    cfg = setup or load_mcp_config()
    by_id = {s.id: s for s in cfg.servers}
    tools = list_enabled_tools(cfg, server_ids=server_ids)
    count = 0
    for info in tools:
        server = by_id.get(info.server_id)
        if not server:
            continue

        def _make(srv: McpServerConfig, tname: str) -> Callable[..., str]:
            def _fn(**kwargs: Any) -> str:
                return call_tool(srv, tname, dict(kwargs))

            return _fn

        schema = info.input_schema if isinstance(info.input_schema, dict) else {
            "type": "object",
            "properties": {},
        }
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}, "description": info.description}

        registry.register(
            Tool(
                info.qualified,
                f"[MCP:{info.server_name}] {info.description}",
                schema,
                _make(server, info.name),
                parallel_safe=False,
                requires_approval=True,
                readonly=False,
            )
        )
        count += 1
    return count


def tools_public_list(
    setup: Optional[McpSetup] = None,
    *,
    server_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Serialize enabled MCP tools for /api/mcp/tools."""
    return [
        {
            "server_id": t.server_id,
            "server_name": t.server_name,
            "name": t.name,
            "qualified": t.qualified,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in list_enabled_tools(setup, server_ids=server_ids)
    ]
