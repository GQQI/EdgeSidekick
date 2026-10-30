"""Parse XML-style tool calls that models dump into assistant text.

MiniMax / Qwen / some vLLM setups emit markup instead of OpenAI tool_calls
when --tool-call-parser is missing or tool_choice=auto was stripped.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ...core.textutil import strip_dangling_colon

_FUNC_BLOCK = re.compile(
    r"<function\s*=\s*([^>\s]+)\s*>(.*?)</function>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_EQ = re.compile(
    r"<parameter\s*=\s*([^>\s]+)\s*>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_SLASH_EQ = re.compile(
    r"</parameter\s*=\s*([^>\s]+)\s*>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_BROKEN_EQ = re.compile(
    r"</parameter>\s*([A-Za-z_][A-Za-z0-9_]*)\s*>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_TAG = re.compile(r"</?parameter[^>]*>", re.IGNORECASE)
_INVOKE_BLOCK = re.compile(
    r"<invoke\s+name=([^>]+)>(.*?)</invoke>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_NAME = re.compile(
    r"<parameter\s+name=([^>]+)>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_JSON_TOOL = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
_FLAT_FUNC = re.compile(
    r"<tool_call>\s*(?:<tool_call>\s*)*function\s*=\s*([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
# MiniMax sometimes omits '>' : <function=skill_foo </function>
_SLOPPY_FUNC = re.compile(
    r"<function\s*=\s*([A-Za-z0-9_.-]+)\s*</function>",
    re.IGNORECASE,
)
_FUNC_OPEN_ANY = re.compile(
    r"<function\s*=\s*([A-Za-z0-9_.-]+)\s*>?",
    re.IGNORECASE,
)
_WRAP_TAGS = re.compile(r"</?(?:minimax:)?tool_call>", re.IGNORECASE)
_CLOSE_TAGS = re.compile(r"</(?:function|invoke|(?:minimax:)?tool_call)>", re.IGNORECASE)
_BARE_FUNC_EQ = re.compile(r"(?:^|\s)function\s*=\s*[A-Za-z0-9_.-]+")
_OPENERS = (
    "<tool_call",
    "<minimax:tool_call",
    "<function=",
    "<invoke name=",
    "<parameter=",
    "</parameter",
)


def _strip_quotes(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _coerce_param(raw: str) -> Any:
    text = (raw or "").strip()
    if text.startswith("\n"):
        text = text[1:]
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


_PARAM_EVENT = re.compile(
    r"</parameter\s*=\s*(?P<broken>[^\s>]+)\s*>|"
    r"</parameter>\s*(?P<after>[A-Za-z_][A-Za-z0-9_]*)\s*>|"
    r"</parameter>|"
    r"<parameter\s*=\s*(?P<eq>[^\s>]+)\s*>|"
    r"<parameter\s+name=(?P<named>[^\s>]+)\s*>",
    re.IGNORECASE,
)


def _iter_xml_params(content: str) -> dict[str, Any]:
    """Walk XML parameter tags, including unclosed / nested openers.

    Models often emit::

        <parameter=limit>200
        <parameter=offset>
        1</parameter>

    A naive ``<parameter=limit>...(first </parameter>)`` regex then stores
    ``limit='200\\n<parameter=offset>\\n1'``, which later crashes ``int()``.
    """
    args: dict[str, Any] = {}
    current = ""
    start = 0
    for match in _PARAM_EVENT.finditer(content or ""):
        token = match.group(0)
        low = token.lower()
        key = _strip_quotes(
            match.group("eq")
            or match.group("named")
            or match.group("broken")
            or match.group("after")
            or ""
        )
        is_plain_close = low == "</parameter>"
        if current:
            if is_plain_close or key:
                raw = content[start : match.start()]
                if current not in args:
                    args[current] = _coerce_param(raw)
                current = ""
            if is_plain_close:
                continue
        if key:
            current = key
            start = match.end()
    if current and current not in args:
        args[current] = _coerce_param(content[start:])
    if args:
        return args
    # Fallback for fully-closed classic markup.
    for rx in (_PARAM_EQ, _PARAM_SLASH_EQ, _PARAM_BROKEN_EQ, _PARAM_NAME):
        for match in rx.finditer(content or ""):
            key = _strip_quotes(match.group(1))
            if key and key not in args:
                args[key] = _coerce_param(match.group(2))
    return args


def _loose_xml_params(content: str) -> dict[str, Any]:
    return _iter_xml_params(content)


def _infer_tool_name(args: dict[str, Any]) -> str:
    keys = {str(k).lower() for k in args}
    if "tasks" in keys or "goal" in keys:
        return "delegate_task"
    if "speakers" in keys:
        return "delegate_dialogue"
    if "topic" in keys:
        return "delegate_dialogue"
    if "path" in keys and "content" in keys:
        return "write_file"
    if "path" in keys and ("old_string" in keys or "old_str" in keys):
        return "str_replace"
    if "query" in keys or "pattern" in keys or "needle" in keys:
        return "search_text"
    if "path" in keys and ("offset" in keys or "limit" in keys):
        return "read_file"
    return ""


# Tools with zero REQUIRED params — safe to *execute* from bare "<function=name>"
# markup that never got any <parameter> children. Everything else (write_file,
# str_replace, run_shell, delegate_*, read_file, git_commit, ...) has required
# args; a bare mention of those is either a schema echo or a generation cut off
# before params streamed in — executing it with {} raised
# "missing N required positional arguments" (write_file/read_file bug).
_NO_ARG_SAFE_NAMES = {
    "git_status",
    "git_branch",
    "git_log",
    "memory_list",
    "list_dir",
    "codebase_overview",
}


def _xml_call_executable_without_args(name: str) -> bool:
    """Used when deciding whether to *execute* a bare/param-less call.

    Strict allowlist: only tools that truly need no arguments may run with
    an empty {} payload. Everything else must have real parsed params.
    """
    n = (name or "").strip().lower()
    if n.startswith("skill_"):
        return True
    return n in _NO_ARG_SAFE_NAMES


def _call(name: str, args: dict[str, Any], idx: int, id_prefix: str = "call_xml") -> dict[str, Any]:
    return {
        "id": f"{id_prefix}_{idx}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def extract_text_tool_calls(
    content: str, *, id_prefix: str = "call_xml"
) -> tuple[str, list[dict[str, Any]]]:
    """Return (visible_text, openai-style tool_calls) parsed from XML markup."""
    if not content:
        return content, []
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []

    def add(name: str, args: dict[str, Any], start: int, end: int) -> None:
        cleaned = _strip_quotes(name)
        if not cleaned:
            return
        if not args and not _xml_call_executable_without_args(cleaned):
            return
        calls.append(_call(cleaned, args, len(calls), id_prefix))
        spans.append((start, end))

    for match in _FUNC_BLOCK.finditer(content):
        add(match.group(1), _iter_xml_params(match.group(2)), match.start(), match.end())

    sloppy_hits = list(_SLOPPY_FUNC.finditer(content))
    if sloppy_hits:
        has_params = bool(_PARAM_EQ.search(content) or _PARAM_NAME.search(content))
        if has_params or len(sloppy_hits) <= 2:
            for match in sloppy_hits:
                name = match.group(1)
                if not _xml_call_executable_without_args(name):
                    continue
                add(name, {}, match.start(), match.end())

    for match in _INVOKE_BLOCK.finditer(content):
        args = {
            _strip_quotes(pm.group(1)): _coerce_param(pm.group(2))
            for pm in _PARAM_NAME.finditer(match.group(2))
        }
        add(match.group(1), args, match.start(), match.end())

    for match in _JSON_TOOL.finditer(content):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name") or (data.get("function") or {}).get("name")
        args = data.get("arguments") or data.get("parameters") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        if name and isinstance(args, dict):
            add(str(name), args, match.start(), match.end())

    if not calls:
        dangling = re.search(
            r"<function\s*=\s*([^>\s]+)\s*>(.*)$",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if dangling:
            body = dangling.group(2)
            args = _iter_xml_params(body)
            if args:
                add(dangling.group(1), args, dangling.start(), len(content))

    flat_hits = list(_FLAT_FUNC.finditer(content))
    if flat_hits and not calls:
        # Many bare names with no parameters = schema echo, not real calls.
        has_params = bool(_PARAM_EQ.search(content) or _PARAM_NAME.search(content))
        if has_params or len(flat_hits) <= 2:
            for match in flat_hits:
                name = match.group(1)
                # A bare "function=name" with no params of its own is only
                # ever executed for tools that truly take zero arguments —
                # never for write_file/read_file/str_replace/etc, even when
                # *some* loose <parameter> tags exist elsewhere in the blob
                # (those get a chance below via _loose_xml_params instead).
                if not _xml_call_executable_without_args(name):
                    continue
                add(name, {}, match.start(), match.end())

    if not calls:
        loose = _loose_xml_params(content)
        name = _infer_tool_name(loose)
        if name and loose:
            start = len(content)
            low = content.lower()
            for token in ("<parameter", "</parameter"):
                idx = low.find(token)
                if idx != -1:
                    start = min(start, idx)
            add(name, loose, start, len(content))

    if not calls:
        return _strip_failed_markup(content), []

    visible = content
    for start, end in sorted(spans, reverse=True):
        visible = visible[:start] + visible[end:]
    # 去掉工具标记前残留的引导冒号，避免助手消息以“：”收尾。
    return strip_dangling_colon(_strip_failed_markup(visible)), calls


def _strip_failed_markup(text: str) -> str:
    """Drop leftover XML / function= dumps so they never reach the chat bubble."""
    if not text:
        return text
    low = text.lower()
    if (
        "<tool_call" not in low
        and "<function=" not in low
        and "<invoke " not in low
        and "<parameter" not in low
    ):
        return text
    visible = _WRAP_TAGS.sub(" ", text)
    visible = _FUNC_BLOCK.sub(" ", visible)
    visible = _SLOPPY_FUNC.sub(" ", visible)
    visible = _INVOKE_BLOCK.sub(" ", visible)
    visible = _PARAM_EQ.sub(" ", visible)
    visible = _PARAM_SLASH_EQ.sub(" ", visible)
    visible = _PARAM_BROKEN_EQ.sub(" ", visible)
    visible = _PARAM_NAME.sub(" ", visible)
    visible = _FLAT_FUNC.sub(" ", visible)
    visible = _FUNC_OPEN_ANY.sub(" ", visible)
    visible = _CLOSE_TAGS.sub(" ", visible)
    visible = _PARAM_TAG.sub(" ", visible)
    visible = _BARE_FUNC_EQ.sub(" ", visible)
    return re.sub(r"\s+", " ", visible).strip()


def split_before_tool_markup(text: str) -> tuple[str, str]:
    """Split visible prose from XML tool markup (including a partial opener)."""
    if not text:
        return "", ""
    low = text.lower()
    cut = -1
    for opener in _OPENERS:
        idx = low.find(opener)
        if idx != -1 and (cut == -1 or idx < cut):
            cut = idx
    if cut != -1:
        # 工具标记前若只剩“：”，这段文字不是完整句子，不能原样上屏。
        return strip_dangling_colon(text[:cut]), text[cut:]
    last = text.rfind("<")
    if last != -1:
        tail = text[last:].lower()
        if any(opener.startswith(tail) for opener in _OPENERS):
            return strip_dangling_colon(text[:last]), text[last:]
    return text, ""


_FUNC_OPEN = re.compile(r"<function\s*=\s*([^>\s]+)\s*>", re.IGNORECASE)
_INVOKE_OPEN = re.compile(
    r"<invoke\s+name=(['\"]?)([^>]+?)\1\s*>",
    re.IGNORECASE,
)
_PARAM_EQ_OPEN = re.compile(
    r"<parameter\s*=\s*([^>\s]+)\s*>(.*)$",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_NAME_OPEN = re.compile(
    r"<parameter\s+name=(['\"]?)([^>]+?)\1\s*>(.*)$",
    re.DOTALL | re.IGNORECASE,
)
_JSON_OPEN = re.compile(r"<tool_call>\s*(\{)", re.IGNORECASE)


def _params_from_eq(body: str, *, tail: bool) -> dict[str, Any]:
    args = _iter_xml_params(body)
    if tail and not args:
        open_p = _PARAM_EQ_OPEN.search(body)
        if open_p:
            args[_strip_quotes(open_p.group(1))] = open_p.group(2)
    return args


def _params_from_name(body: str, *, tail: bool) -> dict[str, Any]:
    args: dict[str, Any] = {}
    last = 0
    for pm in _PARAM_NAME.finditer(body):
        args[_strip_quotes(pm.group(1))] = _coerce_param(pm.group(2))
        last = pm.end()
    if tail:
        open_p = _PARAM_NAME_OPEN.search(body[last:])
        if open_p:
            args[_strip_quotes(open_p.group(2))] = open_p.group(3)
    return args


def _partial_json_tool(blob: str) -> tuple[str, str] | None:
    name_m = re.search(r'"name"\s*:\s*"((?:\\.|[^"\\])*)"', blob)
    if not name_m:
        fn = re.search(
            r'"function"\s*:\s*\{[^}]*?"name"\s*:\s*"((?:\\.|[^"\\])*)"',
            blob,
            re.DOTALL,
        )
        if not fn:
            return None
        name = fn.group(1)
    else:
        name = name_m.group(1)
    args_m = re.search(r'"arguments"\s*:', blob)
    params_m = re.search(r'"parameters"\s*:', blob) if not args_m else None
    marker = args_m or params_m
    if not marker:
        return name, "{}"
    raw = blob[marker.end() :].strip()
    if raw.startswith("{"):
        return name, raw
    if raw.startswith('"'):
        return name, raw
    return name, raw or "{}"


def _parse_open_call(rest: str) -> tuple[str, dict[str, Any] | str] | None:
    sloppy = _SLOPPY_FUNC.search(rest)
    if sloppy:
        name = _strip_quotes(sloppy.group(1))
        if _xml_call_executable_without_args(name):
            return name, {}
    fn = _FUNC_OPEN.search(rest)
    if fn:
        name = _strip_quotes(fn.group(1))
        args = _params_from_eq(rest[fn.end() :], tail=True)
        if args or _xml_call_executable_without_args(name):
            return name, args
    inv = _INVOKE_OPEN.search(rest)
    if inv:
        return _strip_quotes(inv.group(2)), _params_from_name(rest[inv.end() :], tail=True)
    json_open = _JSON_OPEN.search(rest)
    if json_open:
        parsed = _partial_json_tool(rest[json_open.start(1) :])
        if parsed:
            return parsed[0], parsed[1]
    fn_tail = re.search(r"<function\s*=\s*([^\s<>]+)\s*$", rest, re.IGNORECASE)
    if fn_tail:
        name = _strip_quotes(fn_tail.group(1))
        if _xml_call_executable_without_args(name):
            return name, {}
    loose = _loose_xml_params(rest)
    name = _infer_tool_name(loose)
    if name and loose:
        return name, loose
    return None


def snapshot_xml_tool_calls(text: str) -> list[tuple[str, str]]:
    """Name + JSON arguments, including an in-progress trailing call."""
    if not (text or "").strip():
        return []
    spans: list[tuple[int, int, str, dict[str, Any] | str]] = []
    for match in _FUNC_BLOCK.finditer(text):
        spans.append(
            (
                match.start(),
                match.end(),
                _strip_quotes(match.group(1)),
                _params_from_eq(match.group(2), tail=False),
            )
        )
    for match in _SLOPPY_FUNC.finditer(text):
        name = _strip_quotes(match.group(1))
        if _xml_call_executable_without_args(name):
            spans.append((match.start(), match.end(), name, {}))
    for match in _INVOKE_BLOCK.finditer(text):
        spans.append(
            (
                match.start(),
                match.end(),
                _strip_quotes(match.group(1)),
                _params_from_name(match.group(2), tail=False),
            )
        )
    for match in _JSON_TOOL.finditer(text):
        parsed = _partial_json_tool(match.group(1))
        if parsed:
            spans.append((match.start(), match.end(), parsed[0], parsed[1]))
    if not spans:
        loose = _loose_xml_params(text)
        name = _infer_tool_name(loose)
        if name and loose:
            spans.append((0, len(text), name, loose))
    last_end = max((s[1] for s in spans), default=0)
    open_call = _parse_open_call(text[last_end:])
    if open_call and open_call[0]:
        spans.append((last_end, len(text), open_call[0], open_call[1]))
    spans.sort(key=lambda s: s[0])
    out: list[tuple[str, str]] = []
    for _start, _end, name, args in spans:
        if not name:
            continue
        if isinstance(args, str):
            arg_s = args.strip() or "{}"
        else:
            arg_s = json.dumps(args, ensure_ascii=False)
        out.append((name, arg_s))
    return out


class XmlToolStream:
    """Turn growing XML markup into OpenAI-style tool_delta payloads."""

    def __init__(self) -> None:
        self.prefix = f"call_xml_{uuid.uuid4().hex[:10]}"
        self._last: dict[int, tuple[str, str]] = {}

    def feed(self, held: str) -> list[dict[str, Any]]:
        snaps = snapshot_xml_tool_calls(held)
        out: list[dict[str, Any]] = []
        for i, (name, arguments) in enumerate(snaps):
            prev = self._last.get(i)
            if prev == (name, arguments):
                continue
            prev_args = prev[1] if prev else ""
            delta = (
                arguments[len(prev_args) :]
                if arguments.startswith(prev_args)
                else arguments
            )
            self._last[i] = (name, arguments)
            out.append(
                {
                    "index": i,
                    "id": f"{self.prefix}_{i}",
                    "name": name,
                    "arguments_delta": delta,
                    "arguments": arguments,
                }
            )
        return out


def merge_text_tool_calls(
    message: dict[str, Any], *, id_prefix: str = "call_xml"
) -> dict[str, Any]:
    visible, calls = extract_text_tool_calls(
        str(message.get("content") or ""), id_prefix=id_prefix
    )
    if calls and not message.get("tool_calls"):
        message["tool_calls"] = calls
    message["content"] = visible
    return message
