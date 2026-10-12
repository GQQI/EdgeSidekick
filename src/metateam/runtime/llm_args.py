"""Tool-call argument parsing and message-chain alignment."""

from __future__ import annotations

from ..core.logutil import get_logger

_log = get_logger("metateam.llm")

import json
import re
from typing import Any

from .llm_extract import extract_content_text, extract_message_text, extract_reasoning_text, _PARTIAL_ARG_KEYS

def _unescape_json_fragment(text: str) -> str:
    return (
        (text or "")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _closed_string_fields(raw: str) -> dict[str, str]:
    """Copy only fully closed JSON string fields — never invent a closer."""
    out: dict[str, str] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if not closed:
            continue
        body = closed.group(1)
        try:
            out[key] = json.loads(f'"{body}"', strict=False)
        except json.JSONDecodeError:
            out[key] = _unescape_json_fragment(body)
    return out


def decode_tool_args_json(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse tool ``arguments`` without fabricating closers.

    Returns ``(object, reason)``. ``reason`` is one of:
    ``ok``, ``control_chars`` (raw newlines/tabs inside strings — models do
    this for write_file content), ``concatenated`` (two complete objects
    glued by snapshot+delta mix-up), ``truncated``, ``invalid``.
    """
    if isinstance(raw, dict):
        return dict(raw), "ok"
    if raw is None:
        return {}, "ok"
    if isinstance(raw, (list, int, float, bool)):
        return None, "invalid"
    text = str(raw).strip()
    if not text:
        return {}, "ok"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, "ok"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    try:
        data = json.loads(text, strict=False)
        if isinstance(data, dict):
            return data, "control_chars"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder(strict=False)
    found: list[dict[str, Any]] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            data, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if end <= idx:
            break
        if isinstance(data, dict):
            found.append(data)
        idx = end
    if found and idx >= n:
        return found[-1], "concatenated" if len(found) > 1 else "ok"
    if found:
        return None, "truncated"
    return None, "truncated"


def canonical_tool_arguments(raw: Any) -> str:
    """Wire-format tool arguments: always a JSON object string.

    Gateways (vLLM / MiniMax / OpenAI-compatible) ``json.loads`` this field
    on the *next* request. An unterminated write_file ``content`` string is
    exactly ``Unterminated string starting at: line 1 column 60``.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return text
            except json.JSONDecodeError:
                pass
        else:
            return "{}"
    elif raw is None:
        return "{}"
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False)
    text = raw if isinstance(raw, str) else ""
    placeholder: dict[str, Any] = {"_incomplete": True}
    for key, value in _closed_string_fields(text).items():
        if key in {"path", "command", "goal"}:
            placeholder[key] = value
    _log.warning(
        "tool arguments are not valid JSON (%s, %d chars, prefix=%r); "
        "sending a placeholder instead of the raw fragment",
        reason,
        len(text),
        text[:96],
    )
    return json.dumps(placeholder, ensure_ascii=False)


def canonicalize_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn_in = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    fn = {
        "name": str((fn_in or {}).get("name") or ""),
        "arguments": canonical_tool_arguments((fn_in or {}).get("arguments")),
    }
    out = {k: v for k, v in tc.items() if k != "function"}
    out["type"] = out.get("type") or "function"
    out["function"] = fn
    return out


def _loads_partial_object(raw: str) -> dict[str, Any] | None:
    """Best-effort field extract for *error reporting only*.

    Do not use this to decide a write_file payload is complete — appending
    ``"}`` would hide truncation and still leave illegal JSON in history.
    """
    text = (raw or "").strip()
    if not text:
        return None
    obj, reason = decode_tool_args_json(text)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    return None


def _salvage_tool_args(raw: str) -> dict[str, Any]:
    repaired = _loads_partial_object(raw)
    if repaired:
        return repaired
    out: dict[str, Any] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if closed:
            out[key] = _unescape_json_fragment(closed.group(1))
            continue
        opened = re.search(rf'"{key}"\s*:\s*"(.*)$', raw or "", re.DOTALL)
        if opened:
            out[key] = _unescape_json_fragment(opened.group(1).rstrip('"'))
    return out if out else {"_raw": raw}


def parse_tool_args(raw: Any) -> dict[str, Any]:
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    text = raw if isinstance(raw, str) else str(raw or "")
    salvaged = dict(_salvage_tool_args(text) or {})
    salvaged["_incomplete"] = True
    if not any(k != "_incomplete" for k in salvaged):
        salvaged["_raw"] = text
    return salvaged


def align_tool_chains(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool results the provider would reject.

    A ``tool`` message is only valid as the reply to the assistant message that
    issued its id. A compacted transcript can append an older result again, or
    repeat a whole assistant/tool span. Either one makes the next request 400
    with "tool must be a response to a preceding message with tool_calls".
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if isinstance(calls, list) and calls:
            ids = [
                str(tc.get("id") or "")
                for tc in calls
                if isinstance(tc, dict) and str(tc.get("id") or "")
            ]
            j = i + 1
            found: dict[str, dict[str, Any]] = {}
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid and tid in ids and tid not in found and tid not in seen:
                    found[tid] = messages[j]
                j += 1
            if ids and all(tid in seen for tid in ids):
                i = j
                continue
            if not ids or j == i + 1:
                # No results followed. Keep a trailing call so argument repair
                # still runs; an interrupted call is text-only.
                if j < n:
                    kept = dict(m)
                    kept.pop("tool_calls", None)
                    out.append(kept)
                else:
                    out.append(m)
                i = j
                continue
            if all(tid in found for tid in ids):
                out.append(m)
                for tid in ids:
                    out.append(found[tid])
                    seen.add(tid)
            else:
                kept = dict(m)
                kept.pop("tool_calls", None)
                out.append(kept)
            i = j
            continue
        if m.get("role") == "tool":
            i += 1
            continue
        out.append(m)
        i += 1
    return out


def _strip_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in align_tool_chains(messages):
        item = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        calls = item.get("tool_calls")
        if isinstance(calls, list) and calls:
            item["tool_calls"] = [
                canonicalize_tool_call(tc)
                for tc in calls
                if isinstance(tc, dict)
            ]
        # Providers reject tool_calls: [] — omit the field when nothing was called.
        if not item.get("tool_calls"):
            item.pop("tool_calls", None)
        if item.get("role") == "assistant" and not item.get("content") and not item.get("tool_calls"):
            item["content"] = ""
        out.append(item)
    return out


def _demo_chat(
    messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]
) -> dict[str, Any]:
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = str(m.get("content") or "")
            break

    if messages and messages[-1].get("role") == "tool":
        content = str(messages[-1].get("content") or "")
        if "Function skill:" in content or content.lstrip().startswith("# Function skill"):
            return {
                "role": "assistant",
                "content": (
                    "【Demo】当前是 Demo 模式，不会真正按 Skill 执行写文件/改代码。\n\n"
                    "Skill 工具只返回了流程说明；请到「设置 → 模型」配置可用的 API Key，"
                    "确认顶栏不是 Demo，然后新建会话再执行 `/skill`。"
                ),
            }
        return {
            "role": "assistant",
            "content": (
                "【Demo】工具结果已收到（Demo 模式不会继续真实执行）。\n\n"
                f"{content[:280]}"
            ),
        }

    tool_names = set()
    if tools:
        for t in tools:
            fn = t.get("function") or {}
            tool_names.add(fn.get("name"))

    low = last_user.lower()

    # Prefer skill_* function tools
    skill_tools = [n for n in tool_names if n and str(n).startswith("skill_")]
    skill_asked = (
        "skill" in low
        or "技能" in last_user
        or "/skill" in low
        or "【skill" in low
        or any(str(n) in last_user for n in skill_tools)
    )
    if skill_asked and skill_tools:
        # Match the requested skill_* name from the user text when possible.
        name = next((n for n in skill_tools if n and str(n) in last_user), None)
        if not name:
            # /skill html-deck-editorial → skill_html_deck_editorial
            m = re.search(r"/skill\s+([A-Za-z0-9._-]+)", last_user, re.I)
            if m:
                want = "skill_" + re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_")
                name = next((n for n in skill_tools if n == want or want in str(n)), None)
        if not name:
            name = (
                "skill_hello_workspace"
                if "skill_hello_workspace" in skill_tools
                else skill_tools[0]
            )
        # Keep task short — never pass the whole SKILL injection blob as task.
        task = last_user.strip()
        if "用户本次附加指令" in task:
            m = re.search(r"用户本次附加指令：\s*\n(.+?)(?:\n\n请按该 Skill|\Z)", task, re.S)
            task = (m.group(1).strip() if m else "") or "按该 Skill 的标准流程执行"
        elif len(task) > 240:
            task = task[:240]
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_sk_1",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps({"task": task}, ensure_ascii=False),
                    },
                }
            ],
        }

    if "并行" in last_user or "delegate" in low:
        if "delegate_task" in tool_names:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "demo_del_1",
                        "type": "function",
                        "function": {
                            "name": "delegate_task",
                            "arguments": json.dumps(
                                {
                                    "tasks": [
                                        {"goal": "List workspace", "context": "list_dir ."},
                                        {
                                            "goal": "Use hello skill",
                                            "context": "call skill_hello_workspace",
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }

    if any(k in last_user for k in ("列出", "list", "目录", "workspace")) and "list_dir" in tool_names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_ls_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})},
                }
            ],
        }

    if ("写" in last_user or "write" in low or "hello" in low) and "write_file" in tool_names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_w_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "path": "notes/demo.md",
                                "content": "# Demo\n\nHello from EdgeSidekick.\n",
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    return {
        "role": "assistant",
        "content": (
            "【Demo 模式】配置模型 API Key 后可使用真实推理。\n"
            "可试：列出 workspace / 调用 hello skill / 并行委派。"
        ),
    }
