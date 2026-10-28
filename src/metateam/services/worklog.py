"""Short record of who changed which file, for the speak judge.

Social turns are one-shot sessions. The chat window alone does not say
that someone already edited a file or listed a directory. This reads the
saved session files and returns a few short lines, not file bodies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_EDIT_TOOLS = {"write_file", "str_replace", "delete_file", "apply_patch"}
_LOOK_TOOLS = {"list_dir", "read_file"}
_ACTOR_RE = re.compile(r"你是「([^」]{1,16})」")


def _one_line(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def _actor(data: dict[str, Any]) -> str:
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        match = _ACTOR_RE.search(str(msg.get("content") or ""))
        if match:
            return match.group(1)
    title = str((data.get("meta") or {}).get("title") or "").strip()
    if title and title.lower() != "new chat":
        return title[:12]
    return "成员"


def _same_workspace(session_ws: str, wanted: str) -> bool:
    if not wanted or not session_ws:
        return True
    left = session_ws.replace("\\", "/").rstrip("/").lower()
    right = wanted.replace("\\", "/").rstrip("/").lower()
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


def _format_work(who: str, name: str, args: dict[str, Any], content: str) -> str:
    if content.lstrip().startswith("ERROR"):
        return ""
    path = str(args.get("path") or args.get("file_path") or args.get("target_file") or "")
    base = path.replace("\\", "/").split("/")[-1] or path or "文件"
    if name == "str_replace":
        old = _one_line(args.get("old_string") or args.get("old") or "", 28)
        new = _one_line(args.get("new_string") or args.get("new") or "", 28)
        change = f"把「{old}」改成「{new}」" if old and new else "修改了内容"
        return _one_line(f"{who} 编辑了 {base}：{change}", 96)
    if name in {"write_file", "apply_patch"}:
        snippet = _one_line(args.get("content") or args.get("patch") or content, 36)
        return _one_line(f"{who} 写入了 {base}：{snippet}", 96)
    if name == "delete_file":
        return _one_line(f"{who} 删除了 {base}", 96)
    if name == "list_dir":
        return _one_line(f"{who} 查看了目录 {path or base}：{content}", 96)
    if name == "read_file":
        return _one_line(f"{who} 读了 {base}", 96)
    if name == "create_scheduled_task" and "已创建" in content:
        return _one_line(f"{who} 创建了定时任务：{content}", 96)
    return ""


def _lines_in_file(path: Path, workspace: str) -> tuple[list[str], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if not _same_workspace(str(meta.get("workspace") or ""), workspace):
        return [], []
    who = _actor(data)
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    edits: list[str] = []
    looks: list[str] = []
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                tool = str(fn.get("name") or "")
                if tool not in _EDIT_TOOLS and tool not in _LOOK_TOOLS and tool != "create_scheduled_task":
                    continue
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                call_id = str(call.get("id") or "")
                if call_id:
                    calls[call_id] = (tool, args)
        if msg.get("role") != "tool":
            continue
        tool_name = str(msg.get("name") or "")
        args: dict[str, Any] = {}
        paired = calls.get(str(msg.get("tool_call_id") or ""))
        if paired:
            tool_name, args = paired
        if tool_name not in _EDIT_TOOLS and tool_name not in _LOOK_TOOLS and tool_name != "create_scheduled_task":
            continue
        line = _format_work(who, tool_name, args, str(msg.get("content") or ""))
        if not line:
            continue
        if tool_name in _EDIT_TOOLS:
            edits.append(line)
        else:
            looks.append(line)
    return edits, looks


def recent_work_lines(paths: list[Path], workspace: str = "", limit: int = 6) -> list[str]:
    """Newest file edits first, then a couple of directory reads. Capped."""
    ordered = sorted(paths, key=lambda item: item.stat().st_mtime if item.exists() else 0)
    edits: list[str] = []
    looks: list[str] = []
    for path in ordered[-12:]:
        file_edits, file_looks = _lines_in_file(path, workspace)
        edits.extend(file_edits)
        looks.extend(file_looks)
    picked: list[str] = []
    for line in edits[-4:] + looks[-2:]:
        if line not in picked:
            picked.append(line)
    return picked[:limit]
