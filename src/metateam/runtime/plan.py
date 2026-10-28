"""Task planning — break complex goals into steps (plan vs agent execution)."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from ..core.events import new_id

if TYPE_CHECKING:
    from .llm import LLM


@dataclass
class PlanConfirmRequest:
    id: str
    summary: str
    tasks: list[dict[str, Any]]
    created_at: float = field(default_factory=time.time)


class PlanGate:
    """Blocks until the UI confirms or rejects a generated plan."""

    def __init__(self, timeout_sec: float = 600.0) -> None:
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._pending: dict[str, PlanConfirmRequest] = {}

    def request(
        self,
        plan_id: str,
        *,
        summary: str,
        tasks: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]]:
        """Wait for UI. Returns (approved, applied plan fields)."""
        ev = threading.Event()
        req = PlanConfirmRequest(id=plan_id, summary=summary, tasks=list(tasks))
        with self._lock:
            self._events[plan_id] = ev
            self._pending[plan_id] = req
            self._decisions.pop(plan_id, None)
        ok = ev.wait(timeout=self.timeout_sec)
        with self._lock:
            approved = self._decisions.pop(plan_id, False) if ok else False
            self._events.pop(plan_id, None)
            held = self._pending.pop(plan_id, req)
        applied = {
            "summary": held.summary if held else summary,
            "tasks": list(held.tasks) if held else list(tasks),
        }
        if not ok:
            return False, applied
        return bool(approved), applied

    def decide(
        self,
        plan_id: str,
        approved: bool,
        *,
        summary: Optional[str] = None,
        tasks: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        with self._lock:
            ev = self._events.get(plan_id)
            if not ev:
                return True  # already resolved
            self._decisions[plan_id] = bool(approved)
            req = self._pending.get(plan_id)
            if req and approved:
                if summary and str(summary).strip():
                    req.summary = str(summary).strip()
                if isinstance(tasks, list) and tasks:
                    req.tasks = list(tasks)
            ev.set()
            return True

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._events.keys())
        for plan_id in ids:
            self.decide(plan_id, False)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": r.id,
                    "summary": r.summary,
                    "tasks": r.tasks,
                    "created_at": r.created_at,
                }
                for r in self._pending.values()
            ]

_PLAN_SYSTEM = """You break user goals into clear, sequential tasks for a coding agent.

Output ONLY valid JSON (no markdown fences). Keep every string short and JSON-safe:
{
  "summary": "one-line overview",
  "shape_contract": {
    "reuse": "existing files/symbols to extend, or 'none yet'",
    "create_only_if": "when new files are allowed (only if nothing reusable)",
    "config_placement": "where variable rules/copy/lists should live (not hardcoded)",
    "control_flow": "how to keep branches/loops small (map/table/reuse helpers)",
    "why_not_smaller": "why this is already the minimal shape",
    "verify_command": ""
  },
  "tasks": [
    {"title": "short step title", "detail": "what to do in this step"}
  ]
}

Rules:
- 2–8 tasks, ordered, each actionable with file/shell tools.
- Match the user's language (Chinese if they wrote Chinese).
- shape_contract is mandatory: fight overlay (parallel reimplementation), hardcoding,
  and if/loop piling — prefer extending existing workspace assets.
- NEVER add tasks whose only job is asking the user, confirming requirements,
  clarifying the goal, or inspecting the workspace. That happens before this
  JSON is written. If "Already gathered" is present, treat those choices as final.
- Tasks must be implementation or verification work only.
- Scope must be mutually exclusive: each task owns a distinct deliverable
  (file/symbol/phase). Adjacent steps must NOT restate or partially redo the same work.
- title = short action (verb + object); detail = ONLY this step's in-scope work.
  Explicitly omit anything that belongs to another step (no overlapping wording).
- Forbidden splits: "implement X" then "improve/complete/polish X", or repeating the
  same files/outcome under different titles. Merge those into one task.
- A new program is written complete, then checked once. Each implementation task
  writes one finished file (one write of the whole file). Do not split a new file
  into a skeleton step plus later steps that edit that same file, and do not call
  those layers "independently verifiable". why_not_smaller must not justify
  keeping a new file unfinished so a later step can patch it.
- If the outcome is a single new file, emit two tasks: write that file complete,
  then one verification task. Do not pad extra steps.
- Verification is the last task, after every file in the plan already exists.
  Do not insert a run/browser check between unfinished layers, and do not add a
  repair task until that check has failed.
- Prefer fewer sharper steps over many overlapping ones.
- verify_command MUST be empty OR a short one-liner under 80 chars with NO nested
  quotes and NO `powershell -Command ...` wrapping (shell is already PowerShell).
  Good: "Test-Path .\\\\index.html" or "npm test" or "". Bad: long regex / escaped quotes.
"""


PLAN_PREP_MAX_ROUNDS = 3

PLAN_PREP_HINT = """[Plan prep]
You are gathering what a later planner needs to write implementation tasks.
Do not implement. Do not write or edit files. Do not output a numbered task list.

A request is ready to plan only when you can name a concrete outcome (what will
be different when done) and enough scope to list implementation steps.
Clarifying a missing object, target, or success condition is required — that is
not a meta-question.

Use tools when they reduce uncertainty:
- ask_user when the outcome, target, or a decision would change the task list
  (allow_custom=true; put choices in the options array)
- read / list_dir / search_text / codebase_* when the workspace must ground the plan

If the request is already specific enough, reply with a short briefing (constraints,
relevant files, choices already made) and stop. If it is not, call tools first —
do not invent a scope, and do not stop on a generic briefing.
"""

PLAN_PREP_RETRY_HINT = """[Plan prep — still not ready]
The request is still not concrete enough to draft implementation tasks.
Call ask_user (and inspect the workspace if that would narrow the target).
Do not write a task list. Do not invent files, features, or a product scope.
When the user has answered, stop with a short briefing of the now-known outcome.
"""


def digest_plan_prep(
    messages: list[dict[str, Any]],
    *,
    start: int,
    briefing: str = "",
) -> str:
    """Fold this-turn tool results and user replies into planner context."""
    from ..core.textutil import safe_clip

    parts: list[str] = []
    for i, msg in enumerate(messages[max(0, start) :], start=max(0, start)):
        role = msg.get("role")
        if role == "tool":
            name = str(msg.get("name") or "tool")
            content = str(msg.get("content") or "").strip()
            if not content or content.startswith("ERROR"):
                continue
            parts.append(f"{name}: {safe_clip(content, 900)}")
            continue
        if role != "user":
            continue
        meta = msg.get("sidekick") if isinstance(msg.get("sidekick"), dict) else {}
        if msg.get("sidekick_internal") or (isinstance(meta, dict) and meta.get("internal")):
            continue
        content = str(msg.get("content") or "").strip()
        if i == start or not content:
            continue
        parts.append(f"User reply: {safe_clip(content, 600)}")
    brief = (briefing or "").strip()
    if brief and brief not in {"（本轮已完成）"}:
        parts.append(f"Agent briefing: {safe_clip(brief, 1500)}")
    return "\n".join(parts)


def extract_plan_goal(user_text: str) -> str:
    """Prefer the user's real ask over Skill-injection scaffolding for planning."""
    t = (user_text or "").strip()
    if not t:
        return ""
    if "【Skill 已注入】" in t or "----- SKILL START -----" in t:
        m = re.search(
            r"用户本次附加指令：\s*\n(.+?)(?:\n\n请按该 Skill|\Z)",
            t,
            re.S,
        )
        if m:
            return m.group(1).strip()
        # Skill only, no task — do not treat the template as a planning goal
        return ""
    return t


_NEEDS_PLAN_SYSTEM = """You decide whether a coding agent must show a multi-step PLAN
for the user to confirm BEFORE acting. Default to acting immediately.

Reply with ONLY valid JSON (no markdown fences):
{"plan": true|false, "reason": "one short sentence"}

plan=true ONLY for genuinely complex work where a wrong approach wastes a lot of effort:
- multi-file / multi-module features or large refactors
- architecture or approach choices with several valid designs that need user buy-in
- multi-phase deliverables (e.g. new subsystem, migration, deploy pipeline)
- highly ambiguous goals where the first move could go in very different directions

plan=false for ordinary work — even if it takes a few tool calls:
- edit / fix / tweak one file (HTML image swaps, CSS, copy, small bugfix)
- clear single-outcome asks ("改这六个图", "加个按钮", "修报错")
- questions, explanations, lookups
- spawning / running multiple agents or parallel workers (delegate_task)
- anything a competent agent can finish without needing the user to approve a roadmap

When the user wants parallel agents or a live multi-agent session, choose
plan=false so work starts immediately (Plan confirm is for implementation
roadmaps, not for spawning workers).

When unsure, choose plan=false.
"""

def _parse_loose_json_object(raw: str) -> dict[str, Any]:
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


def parse_needs_plan_reply(raw: str) -> Optional[bool]:
    """Parse model reply into True/False; None if unparseable."""
    data = _parse_loose_json_object(raw)
    if data:
        if "plan" in data:
            return bool(data["plan"])
        if "needs_plan" in data:
            return bool(data["needs_plan"])
    low = (raw or "").strip().lower()
    if not low:
        return None
    if re.search(r'"plan"\s*:\s*true', low) or re.search(
        r'"needs_plan"\s*:\s*true', low
    ):
        return True
    if re.search(r'"plan"\s*:\s*false', low) or re.search(
        r'"needs_plan"\s*:\s*false', low
    ):
        return False
    if low in ("true", "yes", "plan"):
        return True
    if low in ("false", "no", "agent"):
        return False
    return None


def _json_unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return (
            value.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )


def _extract_json_string_field(raw: str, key: str) -> str:
    m = re.search(
        rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"',
        raw or "",
        re.S,
    )
    if not m:
        return ""
    return _json_unescape(m.group(1)).strip()


def _salvage_plan_fields(raw: str) -> dict[str, Any]:
    """Best-effort field extraction when full JSON parse fails (bad escapes, truncation)."""
    text = (raw or "").strip()
    if not text:
        return {}
    out: dict[str, Any] = {}
    summary = _extract_json_string_field(text, "summary")
    if summary:
        out["summary"] = summary

    contract: dict[str, str] = {}
    for key in (
        "reuse",
        "create_only_if",
        "config_placement",
        "control_flow",
        "why_not_smaller",
        "verify_command",
    ):
        val = _extract_json_string_field(text, key)
        if val:
            # Drop broken / overlong verify commands that usually break JSON.
            if key == "verify_command" and (
                len(val) > 120 or "powershell -Command" in val or '"' in val
            ):
                val = ""
            contract[key] = val
    if contract:
        out["shape_contract"] = contract

    tasks: list[dict[str, str]] = []
    for m in re.finditer(
        r'\{\s*"title"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"detail"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}',
        text,
        re.S,
    ):
        title = _json_unescape(m.group(1)).strip()
        detail = _json_unescape(m.group(2)).strip()
        if title:
            tasks.append({"title": title, "detail": detail})
    if not tasks:
        for m in re.finditer(
            r'"title"\s*:\s*"((?:\\.|[^"\\])*)"',
            text,
        ):
            title = _json_unescape(m.group(1)).strip()
            if title and title not in {t["title"] for t in tasks}:
                tasks.append({"title": title, "detail": ""})
    if tasks:
        out["tasks"] = tasks[:8]
    return out


def _looks_like_raw_json_blob(text: str) -> bool:
    t = (text or "").strip()
    return t.startswith("{") and (
        '"shape_contract"' in t or '"tasks"' in t or '"summary"' in t
    )


def _parse_plan_json(raw: str) -> dict[str, Any]:
    data = _parse_loose_json_object(raw)
    if data and not _looks_like_raw_json_blob(str(data.get("summary") or "")):
        # Soft-clean verify_command even on successful parse
        sc = data.get("shape_contract")
        if isinstance(sc, dict):
            vc = str(sc.get("verify_command") or "")
            if len(vc) > 120 or "powershell -Command" in vc:
                sc = {**sc, "verify_command": ""}
                data = {**data, "shape_contract": sc}
        return data
    salvaged = _salvage_plan_fields(raw)
    if salvaged:
        return salvaged
    # Never surface the raw JSON blob as the human-readable summary.
    return {"summary": "", "tasks": []}


def _plan_host_hint() -> str:
    """Brief host shell note so verify_command / shell steps match the machine."""
    from ..core.hostinfo import get_host_info

    info = get_host_info()
    net = "online" if info.online else "OFFLINE — do not plan web_search"
    if info.os_family == "windows":
        return (
            f"Host OS: {info.label} — shell is PowerShell. Network: {net}. "
            "verify_command must be empty or a SHORT PowerShell one-liner "
            "(no nested quotes, no powershell -Command wrapping)."
        )
    sh = info.shell or "bash"
    return (
        f"Host OS: {info.label} — shell is {sh}. Network: {net}. "
        "verify_command must be empty or a short portable POSIX command. "
        "Do not use Windows drive letters or PowerShell."
    )


_GOAL_READY_SYSTEM = """You decide whether a planner can draft implementation tasks.

Reply with ONLY valid JSON (no markdown fences):
{"ready": true|false, "reason": "one short sentence"}

ready=true only when the user request plus gathered notes already name a
concrete outcome (what will be different when done) and enough scope to list
implementation steps (files, area, or an explicit feature).

ready=false when the request is still an underspecified intent: a fragment,
a verb without an object, or a missing choice that would change the whole
task list. Workspace inspection does not make a vague intent ready unless it
confirmed a single target the user already implied.

When unsure, ready=false.
"""


def parse_goal_ready_reply(raw: str) -> Optional[bool]:
    """Parse readiness JSON; None if unparseable."""
    data = _parse_loose_json_object(raw)
    if data and "ready" in data:
        return bool(data["ready"])
    low = (raw or "").strip().lower()
    if not low:
        return None
    if re.search(r'"ready"\s*:\s*true', low):
        return True
    if re.search(r'"ready"\s*:\s*false', low):
        return False
    if low in ("true", "ready", "yes"):
        return True
    if low in ("false", "no", "not ready"):
        return False
    return None


def goal_ready_to_plan(llm: "LLM", goal: str, gathered: str = "") -> bool:
    """True when gathered context is enough to write implementation tasks.

    Defaults to False on empty input or model failure so Plan mode keeps
    gathering instead of inventing a scope.
    """
    g = (goal or "").strip()
    if not g:
        return False
    notes = (gathered or "").strip()
    payload = f"User request:\n{g[:4000]}"
    if notes:
        payload += f"\n\nAlready gathered:\n{notes[:6000]}"
    payload += "\n\nJSON:"
    try:
        raw = llm.complete_text(_GOAL_READY_SYSTEM, payload, temperature=0.0)
    except Exception:
        return bool(notes)
    decided = parse_goal_ready_reply(raw)
    if decided is None:
        return bool(notes)
    return decided


def needs_plan(llm: "LLM", user_text: str) -> bool:
    """Ask the model whether this turn should open Plan-confirm first.

    Biased toward false: only complex work should pause for a plan. Skill-only
    injections with no user task skip the call. On model failure, default False.
    The model decides (including multi-agent → plan=false); no keyword lists.
    """
    goal = extract_plan_goal(user_text).strip()
    if not goal:
        return False
    try:
        raw = llm.complete_text(
            _NEEDS_PLAN_SYSTEM,
            f"User request:\n{goal[:4000]}\n\nJSON:",
            temperature=0.0,
        )
    except Exception:
        return False
    decided = parse_needs_plan_reply(raw)
    if decided is None:
        return False
    return decided


_FILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"((?:[A-Za-z]:[\\/])?(?:[\w.-]+[\\/])*[\w.-]+\."
    r"(?:html?|js|mjs|cjs|css|py|ts|tsx|jsx|vue|json|md|go|rs|java|cs|cpp|h|hpp|rb|php|toml|yml|yaml|xml|sql))"
)
_VERIFY_RE = re.compile(r"测试|验证|验收|\bverify\b|\btest\b", re.IGNORECASE)
_BUILD_RE = re.compile(
    r"创建|写入|实现|搭建|新增|编写|定义|生成|write|implement|create|scaffold",
    re.IGNORECASE,
)


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def _same_file(a: str, b: str) -> bool:
    aa, bb = _norm_path(a), _norm_path(b)
    return aa == bb or aa.endswith("/" + bb) or bb.endswith("/" + aa)


def _unique_files(paths: list[str]) -> list[str]:
    out: list[str] = []
    for path in paths:
        for i, prev in enumerate(out):
            if _same_file(path, prev):
                if len(path) > len(prev):
                    out[i] = path
                break
        else:
            out.append(path)
    return out


def _mentioned_files(*parts: str) -> list[str]:
    found: list[str] = []
    for part in parts:
        for match in _FILE_RE.findall(part or ""):
            # Skip versioned CDN paths such as 0.160.0/build/three.module.js.
            if match[0].isdigit():
                continue
            found.append(match)
    return _unique_files(found)


def _is_verify_task(title: str, detail: str, files: list[str]) -> bool:
    blob = f"{title}\n{detail}"
    if not _VERIFY_RE.search(blob):
        return False
    if files and _BUILD_RE.search(blob):
        return False
    return True


def _merge_write_tasks(group: list[dict[str, Any]], path_hint: str) -> dict[str, Any]:
    first = group[0]
    if len(group) == 1:
        return dict(first)
    name = path_hint or (_mentioned_files(first.get("title", ""), first.get("detail", "")) or [""])[0]
    base = name.replace("\\", "/").rsplit("/", 1)[-1] if name else "该文件"
    parts = []
    for i, task in enumerate(group, 1):
        title = str(task.get("title") or "").strip()
        detail = str(task.get("detail") or "").strip()
        parts.append(f"{i}. {title}" + (f": {detail}" if detail else ""))
    return {
        "id": first.get("id") or new_id("task"),
        "title": f"写完 {base}",
        "detail": (
            f"Write {name or base} once as a finished file (one write_file). "
            "Include every behavior below in that single write. "
            "Do not leave a skeleton, and do not patch this file in a later step.\n"
            + "\n".join(parts)
        ),
        "status": str(first.get("status") or "pending"),
    }


def coalesce_layered_writes(
    tasks: list[dict[str, Any]],
    shape_contract: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Collapse skeleton-then-patch steps on one new file into one write, then verify.

    Distinct files stay separate. A verification step stays after the write.
    """
    contract = shape_contract or {}
    contract_files = _mentioned_files(
        str(contract.get("reuse") or ""),
        str(contract.get("create_only_if") or ""),
        str(contract.get("why_not_smaller") or ""),
    )
    sole = contract_files[0] if len(contract_files) == 1 else ""

    classed: list[tuple[dict[str, Any], list[str], bool]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or "")
        detail = str(task.get("detail") or "")
        files = _mentioned_files(title, detail)
        classed.append((task, files, _is_verify_task(title, detail, files)))

    if sole:
        impl = [item for item in classed if not item[2]]
        verifies = [item for item in classed if item[2]]
        owned = all(
            (not files) or all(_same_file(path, sole) for path in files)
            for _task, files, _verify in impl
        )
        if owned and len(impl) >= 2:
            merged = [_merge_write_tasks([item[0] for item in impl], sole)]
            if len(verifies) == 1:
                merged.append(dict(verifies[0][0]))
            elif len(verifies) > 1:
                head = dict(verifies[0][0])
                head["detail"] = "\n".join(
                    (
                        f"{i}. {str(task.get('title') or '').strip()}: "
                        f"{str(task.get('detail') or '').strip()}"
                    ).rstrip(": ")
                    for i, (task, _files, _verify) in enumerate(verifies, 1)
                )
                merged.append(head)
            return merged

    out: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []
    buffer_key = ""

    def flush() -> None:
        nonlocal buffer, buffer_key
        if not buffer:
            return
        out.append(_merge_write_tasks(buffer, buffer_key) if len(buffer) >= 2 else dict(buffer[0]))
        buffer = []
        buffer_key = ""

    for task, files, verify in classed:
        if verify or len(files) != 1:
            flush()
            out.append(dict(task))
            continue
        key = _norm_path(files[0])
        if buffer and buffer_key and not _same_file(buffer_key, key):
            flush()
        if not buffer:
            buffer_key = key
        buffer.append(task)
    flush()
    return out


def generate_plan(llm: "LLM", goal: str, *, gathered: str = "") -> dict[str, Any]:
    """Return {plan_id, summary, shape_contract, tasks: [...]}."""
    from .coherence import normalize_shape_contract

    chunks = [f"User goal:\n{(goal or '').strip()}"]
    extra = (gathered or "").strip()
    if extra:
        chunks.append(
            "Already gathered (do not re-ask; fold into the plan; "
            "no 'confirm requirements' tasks):\n" + extra[:8000]
        )
    chunks.append(_plan_host_hint())
    chunks.append("JSON plan:")
    raw = llm.complete_text(_PLAN_SYSTEM, "\n\n".join(chunks))
    data = _parse_plan_json(raw)
    summary = str(data.get("summary") or "").strip()
    if _looks_like_raw_json_blob(summary):
        summary = _extract_json_string_field(summary, "summary") or ""
    shape_contract = normalize_shape_contract(data.get("shape_contract"))
    vc = str(shape_contract.get("verify_command") or "")
    if len(vc) > 120 or "powershell -Command" in vc:
        shape_contract["verify_command"] = ""
    raw_tasks = data.get("tasks") or []
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for item in raw_tasks[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            tasks.append(
                {
                    "id": new_id("task"),
                    "title": title,
                    "detail": str(item.get("detail") or item.get("description") or "").strip(),
                    "status": "pending",
                }
            )
    if not tasks:
        tasks = [
            {
                "id": new_id("task"),
                "title": "分析需求并执行",
                "detail": goal.strip()[:500],
                "status": "pending",
            }
        ]
    else:
        tasks = coalesce_layered_writes(tasks, shape_contract)
    # Prefer a short human summary derived from the goal if parsing failed.
    if not summary or _looks_like_raw_json_blob(summary):
        summary = (goal.strip().splitlines()[0].strip()[:40] if goal.strip() else "执行计划")
    return {
        "plan_id": new_id("plan"),
        "summary": summary,
        "shape_contract": shape_contract,
        "tasks": tasks,
    }


def snapshot_plan_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy task dicts so later status mutations cannot rewrite queued SSE events."""
    import copy

    out: list[dict[str, Any]] = []
    for item in tasks:
        if isinstance(item, dict):
            out.append(copy.deepcopy(item))
    return out


def apply_confirmed_plan(
    plan: dict[str, Any],
    *,
    summary: str = "",
    tasks: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """Merge UI-edited summary/tasks into the in-flight plan before execute."""
    if summary and str(summary).strip():
        plan["summary"] = str(summary).strip()
    if not isinstance(tasks, list) or not tasks:
        return plan
    original = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    out: list[dict[str, Any]] = []
    for i, item in enumerate(tasks[:8]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        orig = original[i] if i < len(original) else {}
        tid = str(item.get("id") or orig.get("id") or new_id("task")).strip()
        out.append(
            {
                "id": tid,
                "title": title,
                "detail": str(
                    item.get("detail")
                    or item.get("description")
                    or orig.get("detail")
                    or ""
                ).strip(),
                "status": "pending",
            }
        )
    if out:
        plan["tasks"] = out
    return plan


def format_plan_markdown(plan: dict[str, Any], *, awaiting_confirm: bool = False) -> str:
    from .coherence import format_shape_contract_markdown, normalize_shape_contract

    lines = [f"## {plan.get('summary') or '计划'}", ""]
    contract = normalize_shape_contract(plan.get("shape_contract"))
    if any(contract.values()):
        lines.append(format_shape_contract_markdown(contract))
        lines.append("")
    for i, task in enumerate(plan.get("tasks") or [], 1):
        title = task.get("title") or f"步骤 {i}"
        detail = task.get("detail") or ""
        lines.append(f"{i}. **{title}**")
        if detail:
            lines.append(f"   {detail}")
    lines.append("")
    if awaiting_confirm:
        lines.append("_请在下方确认方案后执行，或取消。_")
    else:
        lines.append("_方案已取消，未执行。可切换到 Agent 模式后重试。_")
    return "\n".join(lines)
