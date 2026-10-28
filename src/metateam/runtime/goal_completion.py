"""Decide whether a tool-using turn has actually met its goal.

A tool-less assistant reply is the model's own stopping decision, but it can
also be an early stop after an intermediate observation. This module asks the
cheap model for one structured verdict over the active obligation and the tool
evidence, and the loop follows that verdict.

Verdicts are model judgments, not keyword lists over the user's task. A
continuation is allowed when the verdict says a requested action is still
missing, but only while the extra round produces new tool evidence; a round
that only restates the same answer ends the turn instead of polishing or
repeating forever.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..core.textutil import safe_clip

# Late-friendly import kept local in helpers when needed; mark is tiny/stable.
from .context import COMPACTION_MARK, obligation_from_compaction

# Short delta only — full stop/continue rules live once in the system prompt.
GOAL_CONTINUE_NUDGE = (
    # Internal developer/system instruction only — never framed as a user request.
    "Goal unfinished. Next concrete action now: native tool call, or final "
    "answer from existing evidence. Do not repeat a successful step; do not "
    "only plan/promise. If done or blocked on the user, stop."
)

# Models sometimes narrate "I will use tool X" instead of emitting native
# tool_calls. One controlled continuation recovers that; more would loop.
TOOL_INTENT_CONTINUE_LIMIT = 2

TOOL_INTENT_NUDGE = (
    # Internal developer/system instruction — not a user message.
    "Emit native tool_calls now; do not only describe intent. "
    "Match plugin_/mcp_ prefix when the user named a plugin. "
    "If already done from evidence, answer and stop."
)

TOOL_INTENT_SOFT_NUDGE = (
    "Prior reply described a tool/plugin/MCP with no tool_calls. "
    "Call the matching function now, or say unavailable and stop. "
    "Do not repeat the plan."
)

# Conservative: future/intent language about using tools or plugins.
# No product-specific tool names — plugin_*/mcp_*/skill_* / generic 工具|插件
# all share this path. Models often name a concrete tool id instead of the
# words 工具/插件 (e.g. 「我需要使用plugin_foo_bar来搜索」); that must match too.
_TOOL_ID_TOKEN = r"(?:plugin_|mcp_|skill_|builtin_)[A-Za-z0-9][A-Za-z0-9_.-]{2,}"

_TOOL_INTENT_PATTERN = "".join(
    [
        r"(?:",
        # Intent verb + use + generic tool words
        r"(?:我需要|我将|我会|让我|接下来|准备|打算)[^。\n!！?？]{0,48}",
        r"(?:使用|调用|借助|通过).{0,36}(?:工具|插件|函数|MCP|mcp)",
        r"|",
        # Bare: 使用/调用 + … + 插件/MCP/工具 (「使用X插件」「使用演示插件」)
        r"(?:使用|调用|借助|通过).{0,40}(?:插件|MCP|mcp|工具)",
        r"|",
        # Intent verb + use + concrete tool id
        r"(?:我需要|我将|我会|让我|接下来|准备|打算)[^。\n!！?？]{0,48}",
        r"(?:使用|调用|借助|通过)\s*",
        _TOOL_ID_TOKEN,
        r"|",
        # use + tool word + action
        r"(?:使用|调用|借助).{0,24}(?:工具|插件).{0,12}(?:来|去|进行|搜索|读取|查询|获取)",
        r"|",
        # use + concrete tool id + action
        r"(?:使用|调用|借助)\s*",
        _TOOL_ID_TOKEN,
        r".{0,24}(?:来|去|进行|搜索|读取|查询|获取|列出|发送)",
        r"|",
        r"需要通过.{0,24}(?:工具|插件|函数调用|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        # Planning-to-act without tool id: 「让我先搜索…」
        r"(?:让我先|接下来|开始|先去|先来|现在就).{0,12}"
        r"(?:搜索|查询|读取|打开|执行|调用|列出|获取|发送|检查)",
        r"|",
        # Bare tool id with nearby intent
        r"(?:需要|将要|将会|准备|打算).{0,24}",
        _TOOL_ID_TOKEN,
        r"|",
        r"\b(?:i\s+need\s+to|i\s+will|i'll|i\s+am\s+going\s+to|i'm\s+going\s+to|let\s+me)\b",
        r".{0,48}\b(?:use|call|invoke)\b.{0,36}",
        r"(?:\b(?:tool|plugin|function|mcp)\b|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        r"\b(?:going\s+to|about\s+to)\s+(?:use|call|invoke)\b.{0,36}",
        r"(?:\b(?:tool|plugin|function)\b|",
        _TOOL_ID_TOKEN,
        r")",
        r"|",
        r"\blet\s+me\s+(?:first\s+)?(?:search|query|read|list|fetch|open|call)\b",
        r")",
    ]
)

_TOOL_INTENT_RE = re.compile(_TOOL_INTENT_PATTERN, re.IGNORECASE | re.DOTALL)

_NAMED_TOOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(" + _TOOL_ID_TOKEN + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)



# Past-tense / completed action — do not nudge (already did the work in prose).
_TOOL_DONE_RE = re.compile(
    r"(?:"
    r"(?:已经|已成功|刚完成|刚才|刚刚).{0,16}(?:使用|调用|执行)"
    r"|"
    r"\b(?:i\s+(?:used|called|invoked|ran)|already\s+(?:used|called)|here\s+(?:are|is)\s+the)\b"
    r")",
    re.IGNORECASE,
)


def looks_like_tool_intent_only(text: str) -> bool:
    """True when assistant prose promises a tool use instead of calling one.

    Conservative on purpose: plain chat, finished answers, and past-tense
    reports must not reopen the turn.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 1200:
        return False
    if _TOOL_DONE_RE.search(raw):
        return False
    return bool(_TOOL_INTENT_RE.search(raw))


def intent_probe_text(content: str | None = None, reasoning: str | None = None) -> str:
    """Text used for tool-intent detection.

    Reasoning models often put the plan in `reasoning` and leave `content`
    empty (or duplicate the same plan into both). Probe both, de-duplicated.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for piece in (content, reasoning):
        raw = (piece or "").strip()
        if not raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(raw)
    joined = "\n".join(parts).strip()
    return joined[:1200] if len(joined) > 1200 else joined


def mentions_extension_tools(text: str) -> bool:
    """True when prose refers to a plugin/MCP/tool category (not a product name)."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(
        re.search(
            r"(?:插件|MCP|mcp|工具|plugin|function\s*call|tool_calls?)",
            raw,
            re.IGNORECASE,
        )
    )


def resolve_display_named_plugin_tools(
    text: str,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Map human plugin display names/ids in prose to installed tool ids.

    Generic over the plugin registry (manifest `name` / `id` / tool names).
    Never hardcodes a product brand.
    """
    raw = text or ""
    if not raw or not plugin_summaries:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for plugin in plugin_summaries:
        if not isinstance(plugin, dict):
            continue
        labels = [
            str(plugin.get("name") or "").strip(),
            str(plugin.get("id") or "").strip(),
        ]
        labels = [x for x in labels if x]
        if not labels:
            continue
        hit = False
        for label in labels:
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(label) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                hit = True
                break
            if re.search(re.escape(label) + r"\s*插件", raw, re.IGNORECASE):
                hit = True
                break
        if not hit:
            continue
        for tool in plugin.get("tools") or []:
            if isinstance(tool, dict):
                name = str(tool.get("name") or "").strip()
            else:
                name = str(tool or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(name)
    return found












def extract_named_tool_ids(text: str) -> list[str]:
    """Return plugin_*/mcp_*/skill_*/builtin_* ids mentioned in prose.

    Order-preserving, de-duplicated. Used to validate intent against the
    live tool schema (generic — not product-specific).
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _NAMED_TOOL_RE.finditer(text or ""):
        name = match.group(1)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out



def tool_leaf_aliases(
    full_name: str,
    *,
    known_plugin_ids: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Short aliases for a registry tool id (generic over plugin_/mcp_/skill_).

    ``plugin_offline_docker_generate_dockerfile`` → also
    ``generate_dockerfile``. Used so prose can name the manifest tool leaf
    without the session prefix. Never invents names outside the full id.
    """
    name = str(full_name or "").strip()
    if not name:
        return []
    out: list[str] = [name]
    low = name.lower()
    body = ""
    if low.startswith("plugin_"):
        body = name[7:]
    elif low.startswith("mcp_"):
        body = name[4:]
    elif low.startswith("skill_"):
        body = name[6:]
    elif low.startswith("builtin_"):
        body = name[8:]
    if not body:
        return out
    known = sorted(
        {
            str(x).strip().replace("-", "_").lower()
            for x in (known_plugin_ids or [])
            if str(x).strip()
        },
        key=len,
        reverse=True,
    )
    stripped = False
    body_low = body.lower()
    for kid in known:
        prefix = kid + "_"
        if body_low == kid:
            stripped = True
            break
        if body_low.startswith(prefix):
            leaf = body[len(prefix) :]
            if leaf and leaf.lower() not in {a.lower() for a in out}:
                out.append(leaf)
            stripped = True
            break
    if not stripped and "_" in body:
        # Fallback: drop the first path segment (source id guess).
        leaf = body.split("_", 1)[1]
        if leaf and leaf.lower() not in {a.lower() for a in out}:
            out.append(leaf)
    return out


def resolve_mentioned_tools(
    text: str,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    *,
    known_plugin_ids: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Tool ids named in prose that exist in the current schema/registry.

    Prefers prefixed ids (plugin_/mcp_/skill_/builtin_), then any exact
    known_tool_names hit, then short leaf aliases derived from those names.
    Never invents names outside the provided registry.
    """
    raw = text or ""
    known = {str(n) for n in (known_tool_names or []) if str(n).strip()}
    known_lower = {n.lower(): n for n in known}
    found: list[str] = []
    seen: set[str] = set()

    for name in extract_named_tool_ids(raw):
        key = name.lower()
        if key in seen:
            continue
        # Prefixed ids are candidates; if a registry is provided, require membership.
        if known and key not in known_lower:
            continue
        seen.add(key)
        found.append(known_lower.get(key, name))

    if known:
        # Exact registry names as whole tokens.
        for key, canon in known_lower.items():
            if key in seen:
                continue
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                seen.add(key)
                found.append(canon)
        # Leaf aliases: prose says generate_dockerfile → full plugin_* id.
        # Prefer unique leaves; if several tools share a leaf, include all
        # matches so schema pinning can surface them without guessing one.
        leaf_map: dict[str, list[str]] = {}
        for canon in known:
            for alias in tool_leaf_aliases(canon, known_plugin_ids=known_plugin_ids):
                if alias.lower() == canon.lower():
                    continue
                leaf_map.setdefault(alias.lower(), []).append(canon)
        for leaf, canons in leaf_map.items():
            if not re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(leaf) + r"(?![A-Za-z0-9_])",
                raw,
                re.IGNORECASE,
            ):
                continue
            for canon in canons:
                key = canon.lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(canon)
    return found


def tool_intent_unavailable_message(
    text: str,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> str | None:
    """When prose needs tools absent from the live schema, explain generically."""
    named = extract_named_tool_ids(text or "")
    mapped = resolve_display_named_plugin_tools(text or "", plugin_summaries)
    known_list = [str(n) for n in (known_tool_names or []) if str(n).strip()]
    known = {n.lower() for n in known_list}
    plugin_tools_in_schema = [n for n in known_list if n.lower().startswith("plugin_")]

    if named:
        if known:
            missing = [n for n in named if n.lower() not in known]
            if not missing:
                return None
            shown = ", ".join(missing[:6])
            return (
                f"当前会话的工具列表中没有这些工具：{shown}。"
                "请在设置中启用对应插件/MCP，并确认已加入本会话后再试。"
            )
        shown = ", ".join(named[:6])
        return (
            f"当前会话没有可用工具，无法调用：{shown}。"
            "请确认 plugins_in_chat / mcp_in_chat 已开启，且工具已注册到本会话。"
        )

    if mapped and known and not plugin_tools_in_schema:
        missing = [n for n in mapped if n.lower() not in known]
        if missing:
            shown = ", ".join(missing[:6])
            return (
                f"当前会话未注入对应插件工具（例如 {shown}）。"
                "请确认插件已安装、凭据已配置，且 plugins_in_chat 已开启并绑定到本会话。"
            )

    if mentions_extension_tools(text or "") and not known_list:
        return (
            "当前会话没有可用工具，无法执行插件/MCP/工具调用。"
            "请确认 plugins_in_chat / mcp_in_chat 已开启，且工具已注册到本会话。"
        )
    if mentions_extension_tools(text or "") and not plugin_tools_in_schema and (
        "插件" in (text or "") or re.search(r"\bplugin\b", text or "", re.IGNORECASE)
    ):
        return (
            "当前会话的工具列表里没有插件工具（plugin_*）。"
            "请确认插件已安装、凭据已配置，且已绑定到本会话后再试。"
        )
    return None


def should_continue_for_tool_intent(
    text: str,
    *,
    tools_available: bool,
    had_tool_results: bool,
    nudges: int,
    limit: int = TOOL_INTENT_CONTINUE_LIMIT,
    known_tool_names: set[str] | frozenset[str] | list[str] | None = None,
    plugin_summaries: list[dict[str, Any]] | None = None,
) -> bool:
    """Auxiliary signal: narrated tool use without a native call.

    The agent loop must NOT use this alone to reopen a turn. Goal completion
    (`should_continue_for_goal` / assess_goal_state) owns stop vs continue.
    This helper only informs optional force_tool_choice after the goal judge
    already returned continue.

    Matching is generic: intent prose / planning-to-act / tool ids / registry
    display names — never product-specific hardcodes.
    """
    if not tools_available or had_tool_results:
        return False
    if nudges >= limit:
        return False
    if looks_like_tool_intent_only(text):
        return True
    plugin_ids = [
        str(p.get("id") or "").strip()
        for p in (plugin_summaries or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    ]
    mentioned = resolve_mentioned_tools(
        text, known_tool_names, known_plugin_ids=plugin_ids
    )
    if not mentioned and plugin_summaries:
        mapped = resolve_display_named_plugin_tools(text, plugin_summaries)
        if known_tool_names:
            known_lower = {str(n).lower() for n in known_tool_names}
            mentioned = [n for n in mapped if n.lower() in known_lower]
        else:
            mentioned = mapped
    if not mentioned:
        return False
    raw = (text or "").strip()
    if not raw or _TOOL_DONE_RE.search(raw):
        return False
    return bool(
        re.search(
            r"(?:需要|将要|将会|准备|打算|使用|调用|借助|通过|"
            r"\b(?:need|will|going\s+to|let\s+me|use|call|invoke)\b)",
            raw,
            re.IGNORECASE,
        )
    )


_GOAL_STATE_SYSTEM = """You judge whether a ReAct turn has finished the active goal.

The agent loop is Think → (optional) Act/tools → Observe → Think again.
A tool-less candidate is a proposed stop. You decide if that stop is valid.

Reply with ONLY valid JSON (no markdown fences):
{"state": "done"|"continue"|"blocked", "reason": "one short sentence"}

# Hard rule — narrated intent is never completion
If Evidence present is no, and the Active goal asked for an action, lookup,
change, generation, or verification, and the Candidate only plans, promises,
or narrates calling a tool/plugin/MCP (e.g. "让我调用…工具", "I will use…",
"接下来调用…") without already delivering the outcome — state MUST be
continue. Describing a future call does not satisfy the goal.

# Scope
Judge only what the user asked for. Do not invent follow-up work, refactoring,
tests, documentation, or polish. Do not continue merely because tools exist
in the schema.

Earlier assistant replies this turn count as part of the answer: if a prior
reply already fully met the goal and evidence supports it, state=done even
when the latest candidate only says "done" / "no further action".

state=done when ANY of these hold:
- Pure conversation (greeting, thanks, acknowledgement) already answered, or
  a question already fully answered in the candidate / earlier replies.
- Tool evidence shows every requested outcome is already met.
- An earlier reply this turn already delivered the requested answer and
  evidence shows the supporting tool work happened.

state=blocked when remaining work needs a choice, secret, or action only the
user can provide, and the candidate asks for that.

state=continue when ANY of these hold:
- Evidence present is no for an action/lookup/change/verify request
  (including narrated tool intent with no results).
- The latest check failed, or the reply is only a status/partial plan with no
  earlier complete answer this turn.
- Multiple requested actions exist and only some have successful evidence.

When unsure: unanswered action + no successful tool evidence → continue.
Completed exchange with no pending action → done. Once the requested outcome
is present, do not keep polishing or re-checking.
"""

_STATES = frozenset({"done", "continue", "blocked"})
# Internal user turns that are not the work obligation.
_OBLIGATION_KINDS = frozenset({"plan_step"})


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def parse_goal_verdict(raw: str) -> dict[str, Any]:
    """Parse ``{state, reason}`` from a judge reply.

    ``state`` is done|continue|blocked, or None when unusable. ``reason`` is
    the model's short explanation (may be empty).
    """
    data = _parse_json_object(raw)
    reason = str(data.get("reason") or data.get("why") or "").strip()
    state = str(data.get("state") or "").strip().lower()
    if state not in _STATES:
        if "satisfied" in data:
            state = "done" if bool(data.get("satisfied")) else "continue"
        else:
            low = (raw or "").strip().lower()
            state = ""
            if low:
                matched = re.search(r'"state"\s*:\s*"(done|continue|blocked)"', low)
                if matched:
                    state = matched.group(1)
                elif re.search(r'"satisfied"\s*:\s*true', low):
                    state = "done"
                elif re.search(r'"satisfied"\s*:\s*false', low):
                    state = "continue"
            if not reason:
                rm = re.search(r'"reason"\s*:\s*"([^"]{1,200})"', raw or "", re.I)
                if rm:
                    reason = rm.group(1).strip()
    if state not in _STATES:
        return {"state": None, "reason": reason}
    return {"state": state, "reason": reason}


def parse_goal_state(raw: str) -> Optional[str]:
    """Return done|continue|blocked, or None when the reply is unusable."""
    return parse_goal_verdict(raw).get("state")  # type: ignore[return-value]


def _clip_goal(text: str, limit: int = 3500) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    head = limit // 2
    tail = max(0, limit - head - 1)
    return raw[:head].rstrip() + "\n…\n" + raw[-tail:].lstrip()


def _message_kind(msg: dict[str, Any]) -> str:
    meta = msg.get("sidekick")
    if isinstance(meta, dict):
        return str(meta.get("kind") or "")
    return ""


def is_control_user_message(msg: dict[str, Any]) -> bool:
    """True for runtime nudges. Plan-step prompts stay visible as the obligation.

    Goal-continue / tool-intent prompts are injected as internal system (or
    developer) instructions — never as fake user requests. Older builds used
    role=user; both shapes are treated as control so they never become the
    active obligation.
    """
    role = str(msg.get("role") or "")
    kind = _message_kind(msg)
    meta = msg.get("sidekick")
    internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
    if isinstance(meta, dict) and meta.get("internal"):
        internal = True
    if kind in {"goal_continue", "tool_intent"} and role in {"user", "system", "developer"}:
        return True
    if role != "user":
        return False
    if kind == "goal_continue":
        return True
    if kind == "compaction":
        return True
    content = str(msg.get("content") or "")
    if content.startswith(COMPACTION_MARK):
        return True
    if internal and kind not in _OBLIGATION_KINDS:
        return True
    return False


def active_obligation(messages: list[dict[str, Any]]) -> str:
    """Latest user obligation, including the current plan step.

    Compaction stubs are internal working memory (often the only place the
    original ask survives after a compress). Never treat the raw
    ``[CONTEXT COMPACTION]`` blob as the goal — that made the model think the
    user reported a truncated reply and stop. Prefer pinned user requests.
    """
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "").strip()
        if text.startswith(COMPACTION_MARK):
            extracted = obligation_from_compaction(text)
            if extracted:
                return _clip_goal(extracted)
            continue
        if is_control_user_message(msg):
            continue
        if text:
            return _clip_goal(text)
    return ""


_EVIDENCE_MUTATION_TOOLS = frozenset({"write_file", "str_replace", "delete_file"})


def tool_evidence(
    messages: list[dict[str, Any]],
    *,
    start: int = 0,
    rows: list[tuple[str, str]] | None = None,
    limit_items: int = 8,
    item_chars: int = 500,
    digest_chars: int = 140,
    digest_items: int = 12,
) -> str:
    """Everything this turn did, at a bounded size.

    The judge used to see only the last ``limit_items`` tool rows. A long turn
    that mutated a file early and then kept browsing for verification looked as
    if it had produced no result, so the judge answered "continue" after the job
    was already done. A per-tool digest keeps every action visible; recent rows
    stay in full, and successful filesystem mutations are still force-included.

    ``rows`` lets the caller pass an execution-time ledger. Context management
    replaces large payloads with ``[cleared tool result: …]`` stubs, so reading
    the transcript alone can make completed verification look like no evidence
    at all. The ledger keeps the substance the judge needs.
    """
    if rows is None:
        rows = []
        for msg in (messages or [])[max(0, start) :]:
            if msg.get("role") != "tool":
                continue
            name = str(msg.get("name") or "tool")
            content = str(msg.get("content") or "").strip()
            rows.append((name, content))
    else:
        rows = [
            (str(name or "tool"), str(content or "").strip())
            for name, content in rows
        ]
    if not rows:
        return ""

    recent = rows[-limit_items:] if len(rows) > limit_items else list(rows)
    mutations = [
        (name, content)
        for name, content in rows
        if name in _EVIDENCE_MUTATION_TOOLS
        and content
        and not content.upper().startswith("ERROR")
    ]

    by_name: dict[str, list[str]] = {}
    last_seen: dict[str, int] = {}
    for i, (name, content) in enumerate(rows):
        by_name.setdefault(name, []).append(content)
        last_seen[name] = i
    # Bound the digest by the most recently used tools; an unconfigured plugin
    # registry can expose dozens of names in one turn.
    digest_names = sorted(by_name, key=lambda n: last_seen[n])[-max(1, digest_items) :]
    digest: list[str] = []
    for name in digest_names:
        results = by_name[name]
        last = results[-1]
        state = "error" if last.upper().startswith("ERROR") else "ok"
        digest.append(
            f"{name} x{len(results)} (last {state}): "
            f"{safe_clip(last, digest_chars)}"
        )

    lines = ["Actions this turn:", *digest, "Recent results:"]
    seen: set[tuple[str, str]] = set()
    for name, content in mutations + recent:
        key = (name, content)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{name}: {safe_clip(content, item_chars)}")
    return "\n".join(lines)


def coalesce_internal_nudge(
    messages: list[dict[str, Any]],
    *,
    kind: str,
    content: str,
    role: str = "system",
) -> None:
    """Append an internal nudge, or replace prior same-kind ones.

    Prevents stacking duplicate goal_continue / stall essays each loop — a
    major amplifier of LLM tape-recorder repetition.
    """
    kind_s = str(kind or "").strip()
    body = str(content or "").strip()
    if not kind_s or not body:
        return
    kept: list[dict[str, Any]] = []
    for msg in messages:
        meta = msg.get("sidekick") if isinstance(msg.get("sidekick"), dict) else {}
        mk = str((meta or {}).get("kind") or "")
        internal = bool(msg.get("sidekick_internal") or msg.get("internal"))
        if isinstance(meta, dict) and meta.get("internal"):
            internal = True
        if internal and mk == kind_s:
            continue
        kept.append(msg)
    messages[:] = kept
    messages.append(
        {
            "role": role,
            "content": body,
            "sidekick_internal": True,
            "sidekick": {"internal": True, "kind": kind_s},
        }
    )


def prior_assistant_substance(
    messages: list[dict[str, Any]],
    *,
    start: int = 0,
    limit_chars: int = 3500,
    max_replies: int = 3,
) -> str:
    """Earlier tool-less assistant replies this turn (for the goal judge).

    When the model already delivered a full answer and a later round only
    says "done", the judge must still see the substance — otherwise it keeps
    answering continue and the loop never settles.
    """
    parts: list[str] = []
    for msg in (messages or [])[max(0, start) :]:
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        parts.append(text)
    if len(parts) <= 1:
        # Only the current candidate exists; caller already passes it separately.
        return ""
    # Drop the latest message (the candidate under review); keep prior substance.
    chosen = parts[:-1][-max(1, max_replies) :]
    joined = "\n---\n".join(chosen)
    return safe_clip(joined, limit_chars)


def assess_goal_verdict(
    llm: Any,
    *,
    goal: str,
    evidence: str,
    candidate: str,
    prior_replies: str = "",
) -> dict[str, Any]:
    """Ask the model for ``{state, reason, evidence_present}``.

    Callers should pass the same LLM used for ReAct Think. ``continue`` sends
    the loop back into Think; ``done``/``blocked`` settle the turn. The reason
    is surfaced to the UI/logs so stop/continue is auditable — not a silent
    heuristic.
    """
    obligation = (goal or "").strip()
    if not obligation:
        return {
            "state": None,
            "reason": "",
            "evidence_present": False,
        }
    evidence_text = (evidence or "").strip()
    has_evidence = bool(evidence_text)
    prior = (prior_replies or "").strip()
    prior_block = (
        f"Earlier assistant replies this turn:\n{prior}\n\n" if prior else ""
    )
    evidence_block = (
        evidence_text
        if has_evidence
        else "(none — zero tool results this turn)"
    )
    payload = (
        f"Active goal:\n{obligation}\n\n"
        f"Tool evidence this turn:\n{evidence_block}\n\n"
        f"Evidence present: {'yes' if has_evidence else 'no'}\n\n"
        f"{prior_block}"
        f"Candidate reply (this message has no native tool_calls):\n"
        f"{safe_clip((candidate or '').strip(), 2500) or '(empty)'}\n\n"
        "JSON:"
    )
    try:
        raw = llm.complete_text(_GOAL_STATE_SYSTEM, payload, temperature=0.0)
    except Exception:
        return {
            "state": None,
            "reason": "judge unavailable",
            "evidence_present": has_evidence,
        }
    verdict = parse_goal_verdict(str(raw or ""))
    return {
        "state": verdict.get("state"),
        "reason": str(verdict.get("reason") or "").strip(),
        "evidence_present": has_evidence,
    }


def assess_goal_state(
    llm: Any,
    *,
    goal: str,
    evidence: str,
    candidate: str,
    prior_replies: str = "",
) -> Optional[str]:
    """Compatibility wrapper: return only the state from ``assess_goal_verdict``."""
    return assess_goal_verdict(
        llm,
        goal=goal,
        evidence=evidence,
        candidate=candidate,
        prior_replies=prior_replies,
    ).get("state")  # type: ignore[return-value]


def goal_continue_nudge(reason: str = "") -> str:
    """System delta that sends the loop back into ReAct Think."""
    body = GOAL_CONTINUE_NUDGE
    why = safe_clip((reason or "").strip(), 160)
    if why:
        return f"{body}\nJudge reason: {why}"
    return body


def status_for_goal_verdict(
    state: Optional[str],
    *,
    reason: str = "",
    stalled_no_progress: bool = False,
) -> str:
    """Short user-visible status line for the ReAct gate (zh)."""
    why = safe_clip((reason or "").strip(), 72)
    if stalled_no_progress:
        return "继续执行没有带来新结果，已停止。" + (f"（{why}）" if why else "")
    if state == "continue":
        return f"未完成，继续执行…{(' ' + why) if why else ''}".rstrip()
    if state == "done":
        return f"目标已完成{(' · ' + why) if why else ''}"
    if state == "blocked":
        return f"等待你的决定{(' · ' + why) if why else ''}"
    return "目标判定不可用，本轮结束。"


def should_continue_for_goal(state: Optional[str]) -> bool:
    """Whether a tool-less reply should be rejected so the turn keeps working.

    Only an explicit ``continue`` verdict reopens the turn. ``done`` and
    ``blocked`` stop. An unreadable verdict also stops: spending another
    model round on a guess is what made finished turns keep "improving".

    There is deliberately no fixed continuation count here. A fixed count is
    both too small (a long task stops while it is still progressing) and too
    large (a stuck model repeats the same answer three times). The loop ends a
    continuation only when it produced no new tool evidence; that is the
    observable difference between progress and tape-recorder repetition.
    """
    return state == "continue"


def should_run_next_plan_step(state: Optional[str]) -> bool:
    """Whether the remaining plan steps still have work to do.

    A plan is drafted before execution, so an early step can already satisfy
    the user's actual goal; later steps are then unrequested polish. Stop only
    on a positive ``done``/``blocked`` verdict. An unreadable verdict keeps the
    approved plan running — skipping approved work is worse than one extra
    step, and the per-step turn gate still guards each step's own stop.
    """
    return state not in ("done", "blocked")
