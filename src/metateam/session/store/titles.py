"""Session title helpers."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...runtime.agent import Agent
from ...core.config import Settings, get_settings, reload_settings
from ...core.events import EventBus, new_id
from ...core.logutil import get_logger, log_exception
from ..session import load_session, load_session_bundle, save_session, sessions_dir, workspace_matches

_log = get_logger("metateam.store")

from .model import ChatSession

_UNTITLED_TITLES = frozenset({"新会话", "New chat", "Untitled", ""})

_TITLE_SYSTEM = """你是会话标题助手。根据用户第一条指令，写一个简短标题。
规则：中文 6–18 字，或英文 3–8 个词；不要引号；不要句号；只输出标题本身。"""


def is_untitled_session(title: str) -> bool:
    return (title or "").strip() in _UNTITLED_TITLES


def _is_internal_message(m: dict[str, Any]) -> bool:
    """Hide Sidekick-injected prompts (plan steps, etc.) from the UI transcript."""
    if m.get("sidekick_internal") or m.get("internal"):
        return True
    meta = m.get("sidekick")
    if isinstance(meta, dict) and meta.get("internal"):
        return True
    content = str(m.get("content") or "").lstrip()
    return content.startswith("[Plan step ") or content.startswith("[sidekick:")


def _full_transcript(agent: Any) -> list[dict[str, Any]]:
    """Complete history for an agent, falling back to `.messages` for test doubles."""
    fn = getattr(agent, "full_transcript", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            pass
    return list(getattr(agent, "messages", None) or [])


def _user_turn_count(messages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for m in messages
        if m.get("role") == "user" and not _is_internal_message(m)
    )


# Prompts the social shell (会话版) sends for its one-shot member/DM calls.
_SOCIAL_TURN_PREFIXES = (
    "[社交私聊模式]",
    "[社交群聊模式",
    "你是人设文案助手。根据用户提供的智能体信息",
)


def _single_social_turn(messages: list[dict[str, Any]]) -> bool:
    """Legacy social one-shot junk: a session whose only real user turn is a
    synthetic social prompt. Older builds persisted every social member/wave
    call as its own session, which then polluted the workbench history — hide
    those retroactively. Real workbench turns never use these markers, and a
    lead-DM session bridged to the workbench accumulates many turns."""
    turns = [
        m for m in messages if m.get("role") == "user" and not _is_internal_message(m)
    ]
    if len(turns) != 1:
        return False
    return str(turns[0].get("content") or "").lstrip().startswith(_SOCIAL_TURN_PREFIXES)


def _message_display_text(m: dict[str, Any]) -> str:
    """Prefer UI display text (e.g. /skill name) over the raw model prompt."""
    meta = m.get("sidekick")
    if isinstance(meta, dict):
        display = str(meta.get("display") or "").strip()
        if display:
            return display
    return str(m.get("content") or "")


def _extract_skill_title_source(content: str) -> str:
    """Pull a human-readable task out of skill invocation prompts."""
    text = (content or "").strip()
    if not text:
        return ""
    # /skill name [task]
    m = re.match(r"^/skill\s+(\S+)(?:\s+(.+))?$", text, re.I | re.S)
    if m:
        name, task = m.group(1), (m.group(2) or "").strip()
        return task or f"Skill · {name}"
    # New short tool-call instruction
    m = re.search(
        r"请立即调用函数工具\s*`?(skill_[A-Za-z0-9_]+)`?"
        r"(?:，task 参数为：(.+?)(?:。|$))?",
        text,
        re.S,
    )
    if m:
        task = (m.group(2) or "").strip()
        if task and "可省略 task" not in task:
            return task
        tool = m.group(1) or "skill"
        return f"Skill · {tool.removeprefix('skill_').replace('_', '-')}"
    # Legacy inject blob
    if "【Skill 已注入】" in text or "----- SKILL START -----" in text:
        m = re.search(r"【Skill 已注入】\s*([^\n]+)", text)
        name = (m.group(1).strip() if m else "skill")
        m = re.search(
            r"用户本次附加指令：\s*\n(.+?)(?:\n\n请按该 Skill|\Z)",
            text,
            re.S,
        )
        if m and m.group(1).strip():
            return m.group(1).strip()
        return f"Skill · {name}"
    return ""


def _is_skill_or_command_message(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    if text.startswith("【Skill 已注入】") or "----- SKILL START -----" in text:
        return True
    if text.startswith("请立即调用函数工具"):
        return True
    if re.match(r"^/skill\b", text, re.I):
        return True
    if text.startswith("/") or text.startswith("／"):
        return True
    return False


def _title_source_text(content: str) -> str:
    """Normalize user text into something suitable for a chat title."""
    skill = _extract_skill_title_source(content)
    if skill:
        return skill
    text = (content or "").strip()
    if not text or _is_skill_or_command_message(text):
        return ""
    return text


def _summarize_title(content: str, limit: int = 20) -> str:
    """Heuristic first-line summary of the first real user turn."""
    text = _title_source_text(content)
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    line = " ".join(line.split())
    if len(line) > limit:
        return line[:limit]
    return line


def generate_session_title(llm: Any, content: str, *, display: str = "") -> str:
    """LLM title from the first user instruction; falls back to heuristic."""
    source = _title_source_text(display or content) or _title_source_text(content)
    if not source:
        return ""
    # Short enough already — skip an extra model call.
    compact = " ".join(source.split())
    if len(compact) <= 18:
        return compact
    try:
        raw = llm.complete_text(
            _TITLE_SYSTEM,
            f"用户第一条指令：\n{source[:800]}\n\n标题：",
            temperature=0.2,
        )
    except Exception as exc:
        log_exception(_log, "generate_session_title LLM failed", exc)
        return _summarize_title(source, limit=18)
    title = (raw or "").strip().strip("\"'“”‘’").splitlines()[0].strip()
    title = re.sub(r"^[标题Title：:\s]+", "", title)
    if not title or is_untitled_session(title):
        return _summarize_title(source, limit=18)
    if len(title) > 24:
        title = title[:24]
    return title


def _title_from_messages(messages: list[dict[str, Any]], fallback: str = "New chat") -> str:
    for m in messages:
        if m.get("role") != "user" or _is_internal_message(m):
            continue
        title = _summarize_title(_message_display_text(m))
        if title:
            return title
    return fallback


def _prefer_title(*candidates: str, fallback: str = "New chat") -> str:
    for c in candidates:
        t = (c or "").strip()
        if t and not is_untitled_session(t):
            return t
    return fallback
