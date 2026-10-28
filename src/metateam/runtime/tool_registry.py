"""Tool descriptor, registry, and parallel-batch planning."""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    parallel_safe: bool = False
    requires_approval: bool = False
    # Observation-only tool outside the builtin READONLY_TOOLS list (e.g. a plugin read).
    readonly: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # When set, schemas() only exposes these names (execution still uses _tools).
        self._schema_focus: Optional[set[str]] = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(str(name or ""), None)

    def set_schema_focus(self, names: Optional[set[str]]) -> None:
        self._schema_focus = None if names is None else set(names)

    def expand_schema_focus(self, names: Iterable[str]) -> None:
        if self._schema_focus is None:
            return
        for n in names or []:
            s = str(n or "").strip()
            if s:
                self._schema_focus.add(s)

    def drop_skill_tools(self) -> None:
        for name in list(self._tools):
            if name.startswith("skill_") and name != "skill_save":
                self._tools.pop(name, None)

    def drop_prefix(self, prefix: str) -> None:
        for name in list(self._tools):
            if name.startswith(prefix):
                self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self, *, allow_mutating: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # Coordination tools stay available during plan-prep: they do not mutate
        # the workspace themselves (child agents have their own approval gates).
        _COORDINATION = frozenset({"delegate_task", "delegate_dialogue", "ask_agent", "ask_user"})
        for tool in self._tools.values():
            if self._schema_focus is not None and tool.name not in self._schema_focus:
                continue
            if (
                not allow_mutating
                and tool.requires_approval
                and tool.name not in _COORDINATION
            ):
                continue
            out.append(tool.openai_schema())
        return out

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def readonly_names(self) -> frozenset[str]:
        extra = {t.name for t in self._tools.values() if t.readonly}
        return READONLY_TOOLS | extra if extra else READONLY_TOOLS


# Observation-only tools kept when a turn runs with disable_tools (e.g. the
# 会话版 group decision pass). Everything that mutates the workspace, runs
# shell, delegates, or asks the user stays excluded so the pass cannot act.
READONLY_TOOLS = frozenset(
    {
        # workspace reads
        "read_file",
        "list_dir",
        "search_text",
        # codebase structure
        "codebase_overview",
        "codebase_find_similar",
        "codebase_impact",
        "coherence_checklist",
        # repo state
        "git_status",
        "git_diff",
        "git_log",
        "git_branch",
        # web / browser observation. browser_navigate is not observation:
        # it opens the in-app browser, so a read-only pass must not have it.
        "web_search",
        "browser_observe",
        "browser_snapshot",
        "browser_scroll",
        "browser_get_page_content",
        "browser_extract_content",
        "browser_wait",
        "browser_diagnostics",
        # memory / schedule visibility (reads only)
        "memory_list",
        "memory_read",
        "list_scheduled_tasks",
    }
)


DELEGATE_TOOL_NAMES = frozenset({"delegate_task", "delegate_dialogue", "ask_agent"})


def drop_delegate_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove nested-agent tools. The group harness is already the orchestrator."""
    out: list[dict[str, Any]] = []
    for schema in schemas or []:
        name = str(((schema or {}).get("function") or {}).get("name") or "")
        if name not in DELEGATE_TOOL_NAMES:
            out.append(schema)
    return out


def filter_readonly_schemas(
    schemas: list[dict[str, Any]],
    allowed: Optional[frozenset[str]] = None,
) -> list[dict[str, Any]]:
    """Keep only read-only entries (READONLY_TOOLS unless ``allowed`` is given)."""
    keep = allowed if allowed is not None else READONLY_TOOLS
    out: list[dict[str, Any]] = []
    for schema in schemas or []:
        name = str(((schema or {}).get("function") or {}).get("name") or "")
        if name in keep:
            out.append(schema)
    return out


_ARG_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "search_text": {
        "query": ("query", "pattern", "text", "search", "q", "needle", "keyword"),
        "path": ("path", "file", "filepath", "file_path", "filename", "dir", "directory"),
        "glob": ("glob", "include", "file_glob"),
    },
    "read_file": {
        "path": (
            "path",
            "file",
            "filepath",
            "file_path",
            "filename",
            "file_name",
            "target",
        ),
        "offset": ("offset", "start", "start_line"),
        "limit": ("limit", "count", "max_lines"),
        "encoding": ("encoding", "charset", "codec"),
    },
    "write_file": {
        "path": (
            "path",
            "file",
            "filepath",
            "file_path",
            "filename",
            "file_name",
            "target",
            "dest",
            "destination",
        ),
        "content": (
            "content",
            "contents",
            "text",
            "body",
            "data",
            "source",
            "source_code",
            "code",
            "markdown",
            "html",
        ),
        "encoding": ("encoding", "charset", "codec"),
    },
    "str_replace": {
        "path": (
            "path",
            "file",
            "filepath",
            "file_path",
            "filename",
            "file_name",
            "target",
        ),
        "old_string": ("old_string", "old_str", "oldString", "search"),
        "new_string": ("new_string", "new_str", "newString", "replace"),
        "encoding": ("encoding", "charset", "codec"),
    },
    "delete_file": {
        "path": (
            "path",
            "file",
            "filepath",
            "file_path",
            "filename",
            "file_name",
            "target",
        ),
    },
    "list_dir": {
        "path": ("path", "dir", "directory", "folder", "filepath", "file_path"),
    },
    "skill_save": {
        "from_path": ("from_path", "path", "folder", "source", "src", "skill_path"),
        "content": ("content", "contents", "body", "text"),
        "description": ("description", "desc", "summary"),
        "name": ("name", "skill", "skill_name", "title"),
    },
    "ask_user": {
        "question": ("question", "prompt", "ask", "query", "message"),
        "options": (
            "options",
            "choices",
            "choice",
            "answers",
            "labels",
            "items",
            "option_list",
            "candidates",
            "alternatives",
        ),
        "allow_custom": ("allow_custom", "custom", "allow_other"),
        "custom_label": ("custom_label", "other_label"),
    },
}

_NESTED_ARG_KEYS = ("file", "payload", "params", "arguments", "input", "data")


def flatten_tool_args(
    args: dict[str, Any] | None, *, _depth: int = 0
) -> dict[str, Any]:
    """Lift nested {file: {path, content}} style payloads onto the top level."""
    if not isinstance(args, dict):
        return {}
    raw = dict(args)
    out = dict(raw)
    if _depth > 3:
        return out
    for nest_key in _NESTED_ARG_KEYS:
        inner = raw.get(nest_key)
        if not isinstance(inner, dict):
            continue
        lifted = flatten_tool_args(inner, _depth=_depth + 1)
        for key, value in lifted.items():
            if str(key).startswith("_"):
                continue
            present = out.get(key)
            if present in (None, ""):
                out[key] = value
    return out


def alias_tool_args(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Map common model aliases (pattern→query) onto the handler's real names."""
    raw = flatten_tool_args(args)
    mapping = _ARG_ALIASES.get((name or "").strip(), {})
    out = dict(raw)
    for dest, sources in mapping.items():
        present = out.get(dest)
        if present not in (None, ""):
            continue
        for src in sources:
            if src == dest:
                continue
            value = raw.get(src)
            if isinstance(value, dict):
                continue
            if value not in (None, ""):
                out[dest] = value
                break
    return out


def coerce_bound_args(handler: Callable[..., Any], args: dict[str, Any]) -> dict[str, Any]:
    """Coerce ints/bools so XML bleed like '200\\n<parameter=offset>' does not crash."""
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return args
    out = dict(args)
    for key, param in sig.parameters.items():
        if key not in out:
            continue
        default = param.default if param.default is not inspect.Parameter.empty else None
        ann = param.annotation
        origin = getattr(ann, "__origin__", None)
        if origin is not None:
            continue
        if ann is int or isinstance(default, int):
            fallback = default if isinstance(default, int) else 0
            out[key] = _as_int(out[key], fallback)
        elif ann is bool or isinstance(default, bool):
            out[key] = _as_bool(out[key], bool(default) if isinstance(default, bool) else False)
    return out


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    match = re.match(r"-?\d+", text)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return default
    return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def missing_required_args(handler: Callable[..., Any], args: dict[str, Any] | None) -> list[str]:
    """Names the handler requires that are absent (None/omitted), after aliasing."""
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return []
    bound = dict(args or {})
    missing: list[str] = []
    for key, param in sig.parameters.items():
        if key in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        if key not in bound or bound[key] is None:
            missing.append(key)
    return missing


def _coerce_option_list(raw: Any) -> list[str]:
    """Normalize ask_user option payloads (list / JSON string / newline list)."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
                return _coerce_option_list(parsed)
            except Exception:
                pass
        lines = [ln.strip() for ln in re.split(r"[\n\r;|]+", text) if ln.strip()]
        return lines
    if isinstance(raw, dict):
        out: list[str] = []
        for value in raw.values():
            if isinstance(value, dict):
                label = str(
                    value.get("label") or value.get("text") or value.get("value") or ""
                ).strip()
            else:
                label = str(value or "").strip()
            if label:
                out.append(label)
        return out
    if isinstance(raw, (list, tuple)):
        out = []
        for item in raw:
            if isinstance(item, dict):
                label = str(
                    item.get("label") or item.get("text") or item.get("value") or ""
                ).strip()
            else:
                label = str(item or "").strip()
            if label:
                out.append(label)
        return out
    return [str(raw).strip()] if str(raw).strip() else []


def _options_from_prose(*texts: str) -> list[str]:
    """Pull numbered / lettered choices out of question/why when options omitted."""
    line_re = re.compile(r"^\s*(?:\d+|[A-Za-z])[\.\)、:：]\s*(.+?)\s*$", re.MULTILINE)
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        raw = str(text or "")
        if not raw.strip():
            continue
        for label in line_re.findall(raw):
            item = str(label or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            found.append(item)
    return found


def normalize_ask_user_args(args: dict[str, Any]) -> dict[str, Any]:
    """Fill ask_user.options from aliases or prose when the model omits it."""
    out = dict(args)
    options = _coerce_option_list(out.get("options"))
    if len(options) < 2:
        options = _options_from_prose(
            str(out.get("question") or ""),
            str(out.get("why") or ""),
            str(out.get("constraints") or ""),
            str(out.get("stop") or ""),
        )
    if len(options) < 2 and str(out.get("how") or "").strip().lower() in (
        "options",
        "choice",
        "choose",
        "",
    ):
        # Last-resort binary fork so the gate opens instead of hard-failing.
        options = ["是 / 继续", "否 / 取消"]
    if options:
        out["options"] = options
    return out


def prepare_tool_args(
    name: str, handler: Callable[..., Any], args: dict[str, Any] | None
) -> dict[str, Any]:
    aliased = alias_tool_args(name, args)
    if (name or "").strip() == "ask_user":
        aliased = normalize_ask_user_args(aliased)
    bound = bind_tool_args(handler, aliased)
    return coerce_bound_args(handler, bound)


def bind_tool_args(handler: Callable[..., Any], args: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown kwargs so models can pass extra fields like description."""
    cleaned = {
        str(k): v for k, v in (args or {}).items() if not str(k).startswith("_")
    }
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return cleaned
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return cleaned
    allowed = {
        name
        for name, p in sig.parameters.items()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and name not in ("self", "cls")
    }
    return {k: v for k, v in cleaned.items() if k in allowed}


def skill_tool_name(name: str) -> str:
    # Normalize to [a-z0-9_] for broad provider compatibility
    safe = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return f"skill_{safe}"


def plan_parallel_batches(
    tool_calls: list[dict[str, Any]], registry: ToolRegistry
) -> list[list[dict[str, Any]]]:
    """Group consecutive parallel-safe calls; isolate mutating / sequential tools."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for tc in tool_calls:
        name = (tc.get("function") or {}).get("name", "")
        tool = registry.get(name)
        safe = bool(tool and tool.parallel_safe)
        if safe:
            current.append(tc)
        else:
            if current:
                batches.append(current)
                current = []
            batches.append([tc])
    if current:
        batches.append(current)
    return batches
