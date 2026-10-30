"""Plan task coalescing, generation, and formatting."""

from __future__ import annotations

import json
import re
from typing import Any, Optional, TYPE_CHECKING

from ....core.events import new_id
from .prep import (
    _PLAN_SYSTEM,
    _parse_plan_json,
    _plan_host_hint,
    _salvage_plan_fields,
)

if TYPE_CHECKING:
    from ...llm import LLM

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

