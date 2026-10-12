"""Human-in-the-loop approval gate for write/destructive tools."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.pathutil import path_outside_workspace
from .shell_policy import is_dangerous_shell


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args: dict[str, Any]
    summary: str
    scope: str = ""
    created_at: float = field(default_factory=time.time)


class ApprovalGate:
    """Blocks worker threads until the UI decides approve/reject.

    ``_allowed_tools`` holds remember-keys for the current agent turn
    (cleared via ``begin_turn``). File ops outside the workspace share
    ``outside_workspace``; shell commands that mention outside paths share
    ``shell_outside_workspace``; drive/home-root deletes share
    ``dangerous_shell``. A single 「本轮记住」covers later same-scope calls.

    ``_allow_all`` is a user switch (not cleared each turn): when on, no
    dialog is shown and every tool is treated as preapproved.
    """

    def __init__(self, timeout_sec: float = 300.0) -> None:
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._ui_lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._pending: dict[str, ApprovalRequest] = {}
        self._allowed_tools: set[str] = set()
        self._allow_all = False
        self._early: dict[str, bool] = {}
        self._patches: dict[str, dict[str, Any]] = {}

    def serialize_ui(self) -> threading.Lock:
        """One in-flight approval dialog at a time (parallel subagents share this gate)."""
        return self._ui_lock

    def begin_turn(self) -> None:
        with self._lock:
            self._allowed_tools.clear()

    def set_allow_all(self, enabled: bool) -> None:
        pending_ids: list[str] = []
        with self._lock:
            self._allow_all = bool(enabled)
            if enabled:
                pending_ids = list(self._events.keys())
        if enabled:
            for approval_id in pending_ids:
                self.decide(approval_id, True, remember_all=True)

    def is_allow_all(self) -> bool:
        with self._lock:
            return self._allow_all

    def is_preapproved(self, tool: str) -> bool:
        with self._lock:
            return self._allow_all or tool in self._allowed_tools

    def remember_tool(self, tool: str) -> None:
        name = (tool or "").strip()
        if not name:
            return
        with self._lock:
            self._allowed_tools.add(name)

    def request(
        self,
        approval_id: str,
        tool: str,
        args: dict[str, Any],
        summary: str,
        *,
        scope: str | None = None,
    ) -> bool:
        remember_key = (scope or tool or "").strip() or (tool or "")
        with self._lock:
            if self._allow_all or remember_key in self._allowed_tools:
                return True
            if approval_id in self._early:
                return bool(self._early.pop(approval_id))
        ev = threading.Event()
        req = ApprovalRequest(
            id=approval_id,
            tool=tool,
            args=args,
            summary=summary,
            scope=remember_key,
        )
        with self._lock:
            if approval_id in self._early:
                return bool(self._early.pop(approval_id))
            self._events[approval_id] = ev
            self._pending[approval_id] = req
            self._decisions.pop(approval_id, None)
        ok = ev.wait(timeout=self.timeout_sec)
        with self._lock:
            decided = self._decisions.pop(approval_id, False) if ok else False
            self._events.pop(approval_id, None)
            self._pending.pop(approval_id, None)
            self._early.pop(approval_id, None)
            patch = self._patches.pop(approval_id, None)
        if ok and decided and patch:
            args.update(patch)
        return bool(ok and decided)

    def decide(
        self,
        approval_id: str,
        approved: bool,
        *,
        remember: bool = False,
        remember_all: bool = False,
        patch_args: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if approved and patch_args:
                self._patches[approval_id] = dict(patch_args)
            if approved and remember_all:
                self._allow_all = True
            ev = self._events.get(approval_id)
            if not ev:
                self._early[approval_id] = bool(approved)
                return True
            if approved and remember and not remember_all:
                req = self._pending.get(approval_id)
                key = ((req.scope if req else "") or (req.tool if req else "")).strip()
                if key:
                    self._allowed_tools.add(key)
            self._decisions[approval_id] = bool(approved)
            ev.set()
            return True

    def cancel_all(self) -> None:
        """Reject every pending approval (used on stop)."""
        with self._lock:
            ids = list(self._events.keys())
        for approval_id in ids:
            self.decide(approval_id, False)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": r.id,
                    "tool": r.tool,
                    "args": r.args,
                    "summary": r.summary,
                    "created_at": r.created_at,
                }
                for r in self._pending.values()
            ]


# Tools that mutate the workspace / system and need confirmation.
# Keep in sync with Tool.requires_approval on builtins in tools.py.
APPROVAL_TOOLS = {
    "write_file",
    "str_replace",
    "delete_file",
    "run_shell",
    "skill_save",
    "memory_append",
    "memory_remove",
    "memory_write",
    "git_commit",
    "verify_run",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_press_key",
}

# File tools that take a path and may escape the workspace.
FILE_PATH_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "str_replace",
        "delete_file",
        "list_dir",
        "search_text",
    }
)
OUTSIDE_WORKSPACE_SCOPE = "outside_workspace"
SHELL_COMMAND_TOOLS = frozenset({"run_shell", "verify_run"})
SHELL_OUTSIDE_SCOPE = "shell_outside_workspace"
DANGEROUS_SHELL_SCOPE = "dangerous_shell"


def _tool_path_arg(args: dict[str, Any] | None) -> str:
    rec = args if isinstance(args, dict) else {}
    return str(rec.get("path") or rec.get("dir") or "").strip()


def _command_outside_workspace(
    args: dict[str, Any] | None,
    workspace: Path | str,
) -> bool:
    cmd = str((args if isinstance(args, dict) else {}).get("command") or "").strip()
    if not cmd:
        return False
    from ..services.shell_sandbox import ShellSandboxPolicy, outside_shell_paths

    ws = Path(workspace)
    policy = ShellSandboxPolicy.for_workspace(ws)
    return bool(outside_shell_paths(cmd, cwd=ws, policy=policy))


def approval_scope(
    name: str,
    args: dict[str, Any] | None = None,
    workspace: Path | str | None = None,
) -> str:
    """Remember-key for this call. Outside file/shell ops have their own scopes."""
    tool_name = (name or "").strip()
    root = str(workspace or "").strip()
    rec = args if isinstance(args, dict) else {}
    if tool_name in SHELL_COMMAND_TOOLS and is_dangerous_shell(str(rec.get("command") or "")):
        return DANGEROUS_SHELL_SCOPE
    if tool_name in FILE_PATH_TOOLS and root:
        raw = _tool_path_arg(args)
        if raw and path_outside_workspace(raw, root):
            return OUTSIDE_WORKSPACE_SCOPE
    if tool_name in SHELL_COMMAND_TOOLS and root and _command_outside_workspace(args, root):
        return SHELL_OUTSIDE_SCOPE
    return tool_name


def tool_needs_approval(name: str) -> bool:
    if name in APPROVAL_TOOLS:
        return True
    # All MCP tools mutate external systems — always gate
    if (name or "").startswith("mcp_"):
        return True
    return False


def approval_required(
    name: str,
    tool: Any | None = None,
    *,
    args: dict[str, Any] | None = None,
    workspace: Path | str | None = None,
) -> bool:
    """Single gate: outside-workspace file/shell ops, Tool.requires_approval, or table."""
    scope = approval_scope(name, args, workspace)
    if scope in (OUTSIDE_WORKSPACE_SCOPE, SHELL_OUTSIDE_SCOPE, DANGEROUS_SHELL_SCOPE):
        return True
    if tool is not None and bool(getattr(tool, "requires_approval", False)):
        return True
    return tool_needs_approval(name)


def _short(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _tool_summary_body(name: str, args: dict[str, Any]) -> str:
    if name == "write_file":
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        return f"写入 {path or '（路径待定）'}（{len(content)} 字符）"
    if name == "str_replace":
        path = str(args.get("path") or "")
        old = str(args.get("old_string") or args.get("oldString") or "")
        new = str(args.get("new_string") or args.get("newString") or "")
        n_old = old.count("\n") + (1 if old else 0)
        n_new = new.count("\n") + (1 if new else 0)
        return f"替换 {path or '（路径待定）'}（−{n_old}/+{n_new} 行）"
    if name == "delete_file":
        return f"删除 {args.get('path') or ''}"
    if name == "read_file":
        path = str(args.get("path") or "")
        return f"读取 {path}" if path else "读取文件"
    if name == "list_dir":
        path = str(args.get("path") or ".")
        return f"列出 {path}"
    if name == "search_text":
        q = str(args.get("query") or args.get("pattern") or "")
        path = str(args.get("path") or ".")
        return f"搜索 “{_short(q, 40)}” @ {path}"
    if name == "run_shell":
        cmd = str(args.get("command") or "")
        bg = " · 后台" if args.get("background") else ""
        return f"shell{bg}: {_short(cmd, 100)}"
    if name == "shell_job_list":
        return "列出后台脚本"
    if name == "shell_job_log":
        return f"后台日志 {args.get('job_id') or ''}"
    if name == "shell_job_wait":
        return f"等待后台任务 {args.get('job_id') or ''}"
    if name == "shell_job_stop":
        return f"停止后台任务 {args.get('job_id') or ''}"
    if (name or "").startswith("mcp_"):
        return f"MCP {name}: {_short(str(args), 100)}"
    if name == "browser_navigate":
        return f"浏览器打开: {_short(str(args.get('url') or ''), 80)}"
    if name == "browser_click":
        return f"浏览器点击: {_short(str(args.get('selector') or ''), 60)}"
    if name == "browser_type":
        return f"浏览器输入 {_short(str(args.get('selector') or ''), 40)}: {_short(str(args.get('text') or ''), 40)}"
    if name == "browser_press_key":
        key = str(args.get("key") or "")
        sel = str(args.get("selector") or "")
        return f"浏览器按键 {key}" + (f" @ {_short(sel, 40)}" if sel else "")
    if name == "skill_save":
        return f"保存技能 {args.get('name') or ''}"
    if name == "memory_append":
        note = str(args.get("note") or "")
        cat = str(args.get("category") or "")
        return f"追加记忆{(' · ' + cat) if cat else ''}: {_short(note, 80)}"
    if name == "memory_remove":
        return f"删除记忆: {_short(str(args.get('memory_id') or args.get('match') or ''), 80)}"
    if name == "memory_write":
        title = str(args.get("title") or args.get("memory_id") or "")
        return f"写入记忆{(' · ' + title) if title else ''}（{len(str(args.get('content') or ''))} 字符）"
    if name == "memory_read":
        return "读取记忆库"
    if name == "memory_list":
        return "列出记忆库"
    if name == "delegate_task":
        goal = str(args.get("goal") or args.get("task") or "")
        return f"委派: {_short(goal, 80)}"
    if name == "delegate_dialogue":
        topic = str(args.get("topic") or "")
        n = len(args.get("speakers") or [])
        return f"多智能体会话: {_short(topic, 60)}（{n} 方）"
    if name == "ask_user":
        q = str(args.get("question") or "")
        return f"询问用户: {_short(q, 80)}"
    if name == "git_status":
        return "git status"
    if name == "git_diff":
        path = str(args.get("path") or "")
        staged = "staged " if args.get("staged") else ""
        return f"git {staged}diff{(' ' + path) if path else ''}"
    if name == "git_log":
        return "git log"
    if name == "git_branch":
        return "git branch"
    if name == "git_commit":
        return f"git commit: {_short(str(args.get('message') or ''), 80)}"
    if name == "verify_run":
        return f"验收: {_short(str(args.get('command') or ''), 100)}"
    if name.startswith("skill_"):
        return f"调用技能 {name}"
    for key in ("path", "command", "query", "name", "goal", "note", "question"):
        if key in args and args[key]:
            return f"{name}: {_short(str(args[key]), 80)}"
    return name


def summarize_tool_call(
    name: str,
    args: dict[str, Any],
    workspace: Path | str | None = None,
) -> str:
    body = _tool_summary_body(name, args)
    scope = approval_scope(name, args, workspace)
    if scope == DANGEROUS_SHELL_SCOPE:
        return f"危险删除 · {body}"
    if scope in (OUTSIDE_WORKSPACE_SCOPE, SHELL_OUTSIDE_SCOPE):
        return f"工作区外 · {body}"
    return body
