"Plan prep / plan-only / planned-agent execution."

from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Callable, Optional

from .ask import (
    AskGate,
    MIN_ASK_OPTIONS,
    build_ask_options,
    normalize_option_labels,
    should_keep_as_markdown,
    try_parse_inline_ask,
)
from ..core.config import Settings, get_settings
from .context import (
    COMPACTION_MARK,
    cheap_compact,
    context_budget_tokens,
    current_turn_start,
    debug_dump_budget,
    ensure_fit,
    messages_tokens,
    park_overflow,
    schemas_tokens,
    squeeze_fresh_tool_results,
)
from ..core.events import EventBus, emit, new_id
from ..core.guardrails import Guardrails, repeating_tool_sequence, tool_call_signature
from .llm import LLM, parse_tool_args, streaming_text_looped
from .plan import (
    PLAN_PREP_HINT,
    PLAN_PREP_MAX_ROUNDS,
    PLAN_PREP_RETRY_HINT,
    PlanGate,
    apply_confirmed_plan,
    digest_plan_prep,
    extract_plan_goal,
    format_plan_markdown,
    generate_plan,
    goal_ready_to_plan,
    is_multi_agent_request,
    needs_plan,
    snapshot_plan_tasks,
)
from .prompts import (
    apply_response_language,
    build_system_prompt,
    language_directive,
    language_pin_messages,
)
from .coherence import inject_contract_into_goal, shape_contract_from_plan
from .review import run_review
from ..services.skills import Skill, invalidate_skills_cache, load_skills
from .tool_registry import drop_delegate_schemas, filter_readonly_schemas, skill_tool_name
from .tools import ToolRegistry, build_registry
from .tools.support import _skill_as_tool
from .agent_execute import drop_unready_tool_calls, file_tool_results_blocked

class AgentPlanMixin:
    def _run_plan_prep(self, user_text: str) -> tuple[str, int, bool, bool]:
        """Ask / inspect until the goal is concrete enough to draft tasks."""
        prep_start = len(self.messages)
        prep_schemas = self.registry.schemas(allow_mutating=False)
        prev_mutating = self._allow_mutating_tools
        self._allow_mutating_tools = False
        self.guard.set_explore_only(True)
        briefing = ""
        prep_iters = 0
        prep_cancelled = False
        prep_compressed = False
        gathered = ""
        cap = int(self.settings.max_iterations or 0)
        round_iters = 0 if cap <= 0 else max(4, cap // 6)
        try:
            for round_i in range(PLAN_PREP_MAX_ROUNDS):
                self.guard.begin_plan_step()
                hint = PLAN_PREP_HINT if round_i == 0 else PLAN_PREP_RETRY_HINT
                self._emit(
                    "assistant_status",
                    {
                        "text": (
                            "正在了解需求（必要时会先提问或查看工作区）…"
                            if round_i == 0
                            else "需求仍不明确，继续澄清…"
                        ),
                        "tools": [],
                    },
                )
                self.messages.append(
                    {
                        "role": "user",
                        "content": hint,
                        "sidekick_internal": True,
                        "sidekick": {"internal": True, "kind": "plan_prep"},
                    }
                )
                briefing, iters, prep_cancelled, compressed = self._run_agent_loop(
                    round_iters,
                    tools=prep_schemas,
                    emit_assistant_text=False,
                    hold_for_goal=False,
                )
                prep_iters += iters
                prep_compressed = prep_compressed or compressed
                if prep_cancelled or self.cancelled():
                    break
                gathered = digest_plan_prep(
                    self.messages, start=prep_start, briefing=briefing
                )
                if goal_ready_to_plan(self.compress_llm, user_text, gathered):
                    break
        finally:
            self._allow_mutating_tools = prev_mutating
            self.guard.set_explore_only(False)
        return gathered, prep_iters, prep_cancelled, prep_compressed

    def _run_plan_only(self, user_text: str) -> tuple[str, int, bool, bool]:
        """Gather with tools if needed, then confirm a plan, then execute."""
        gathered, prep_iters, prep_cancelled, prep_compressed = self._run_plan_prep(
            user_text
        )
        if prep_cancelled or self.cancelled():
            text = "已停止"
            return text, prep_iters, True, prep_compressed
        self._emit(
            "assistant_status",
            {"text": "正在生成方案（反堆砌形态合同）…", "tools": []},
        )
        plan = generate_plan(self.compress_llm, user_text, gathered=gathered)
        plan_id = str(plan["plan_id"])
        tasks: list[dict[str, Any]] = list(plan.get("tasks") or [])
        summary = str(plan.get("summary") or "执行计划")
        shape_contract = shape_contract_from_plan(plan)
        self._emit(
            "plan_created",
            {
                "plan_id": plan_id,
                "session_id": self.session_id or "",
                "summary": summary,
                "tasks": snapshot_plan_tasks(tasks),
                "shape_contract": shape_contract,
                "mode": "plan",
                "awaiting_confirm": True,
            },
        )
        self._emit(
            "plan_confirm_request",
            {
                "plan_id": plan_id,
                "session_id": self.session_id or "",
                "summary": summary,
                "tasks": snapshot_plan_tasks(tasks),
                "shape_contract": shape_contract,
                "message": f"等待确认方案：{summary[:80]}",
            },
        )
        approved, applied = self.plan_gate.request(
            plan_id, summary=summary, tasks=tasks
        )
        self._emit(
            "plan_confirm_resolved",
            {
                "plan_id": plan_id,
                "approved": approved,
                "message": "方案已确认，开始执行" if approved else "方案已取消",
            },
        )
        if not approved or self.cancelled():
            md = format_plan_markdown(plan, awaiting_confirm=False)
            self._emit("assistant_delta", {"chunk": "", "reset": True, "discard": True})
            self._emit("assistant_delta", {"chunk": md})
            self.messages.append({"role": "assistant", "content": md, "ts": time.time()})
            self._emit(
                "plan_done",
                {
                    "plan_id": plan_id,
                    "message": "方案未执行",
                    "cancelled": True,
                },
            )
            return md, prep_iters, self.cancelled(), prep_compressed

        apply_confirmed_plan(
            plan,
            summary=str(applied.get("summary") or ""),
            tasks=applied.get("tasks") if isinstance(applied.get("tasks"), list) else None,
        )
        final, turned, cancelled, compressed = self._execute_plan(plan, user_text)
        return final, turned + prep_iters, cancelled, compressed or prep_compressed

    def _run_planned_agent(self, user_text: str) -> tuple[str, int, bool, bool]:
        plan = generate_plan(self.compress_llm, user_text)
        return self._execute_plan(plan, user_text)

    def _execute_plan(self, plan: dict[str, Any], user_text: str) -> tuple[str, int, bool, bool]:
        plan_id = str(plan["plan_id"])
        tasks: list[dict[str, Any]] = list(plan.get("tasks") or [])
        shape_contract = shape_contract_from_plan(plan)
        summary = str(plan.get("summary") or "")
        self._active_plan = {
            "plan_id": plan_id,
            "summary": summary,
            "shape_contract": shape_contract,
            "tasks": tasks,
        }
        try:
            return self._execute_plan_steps(plan_id, tasks, shape_contract, summary)
        finally:
            self._active_plan = None

    def _execute_plan_steps(
        self,
        plan_id: str,
        tasks: list[dict[str, Any]],
        shape_contract: dict[str, Any],
        summary: str,
    ) -> tuple[str, int, bool, bool]:
        from .coherence import format_shape_contract_markdown

        self._emit(
            "plan_created",
            {
                "plan_id": plan_id,
                "summary": summary,
                "tasks": snapshot_plan_tasks(tasks),
                "shape_contract": shape_contract,
                "mode": "agent",
                "awaiting_confirm": False,
            },
        )
        intro = f"## {summary or '执行计划'}\n\n"
        if any(shape_contract.values()):
            intro += format_shape_contract_markdown(shape_contract) + "\n\n"
        intro += "将按任务列表逐步执行…\n"
        self._emit("assistant_delta", {"chunk": "", "reset": True})
        self._emit("assistant_delta", {"chunk": intro})

        cap = int(self.settings.max_iterations or 0)
        per_step = 0 if cap <= 0 else max(8, cap // 3)
        turned = 0
        was_cancelled = False
        compressed = bool(getattr(self, "_last_compressed", False))
        step_notes: list[str] = []

        for i, task in enumerate(tasks):
            if self.cancelled():
                was_cancelled = True
                break
            tid = str(task.get("id") or new_id("task"))
            task["id"] = tid
            title = str(task.get("title") or f"步骤 {i + 1}")
            task["status"] = "running"
            self._emit(
                "plan_step",
                {
                    "plan_id": plan_id,
                    "task_id": tid,
                    "index": i,
                    "status": "running",
                    "title": title,
                    "summary": summary,
                    "tasks": snapshot_plan_tasks(tasks),
                },
            )
            self.guard.begin_plan_step()
            prior = "\n".join(f"- {s}" for s in step_notes) if step_notes else ""
            reuse = (
                "This is a continuation of the same turn, not a new request. "
                "Tool results already in this conversation remain valid — "
                "do not re-fetch files or listings that were already returned."
            )
            if i == 0:
                step_body = (
                    f"[Plan step 1/{len(tasks)}] {title}\n"
                    f"{task.get('detail') or ''}\n\n"
                    f"{reuse}\n"
                    "Complete only this step's scope. Reply with a brief summary "
                    "of what this step changed."
                )
            else:
                step_body = (
                    f"[Plan step {i + 1}/{len(tasks)}] {title}\n"
                    f"{task.get('detail') or ''}\n\n"
                    f"Already done:\n{prior}\n\n"
                    f"{reuse}\n"
                    "Complete only this step. Do not redo prior steps or pull in later steps."
                )
            # Full shape contract only on step 1; later steps get a short reminder.
            if i == 0:
                step_msg = inject_contract_into_goal(step_body, shape_contract)
            elif any(shape_contract.values()):
                step_msg = (
                    f"{step_body}\n\n"
                    "Keep following the plan's shape contract from step 1 "
                    "(reuse existing assets; no parallel reimplementation)."
                )
            else:
                step_msg = step_body
            self.messages.append(
                {
                    "role": "user",
                    "content": step_msg,
                    "sidekick_internal": True,
                    "sidekick": {"internal": True, "kind": "plan_step", "index": i},
                }
            )
            step_final, step_iters, step_cancelled, step_compressed = self._run_agent_loop(
                per_step
            )
            turned += step_iters
            if step_compressed:
                compressed = True
            if step_cancelled:
                was_cancelled = True
            from ..core.textutil import safe_clip

            status = "done"
            if step_cancelled:
                status = "cancelled"
            elif (step_final or "").startswith("ERROR"):
                status = "error"
            elif getattr(self, "_goal_satisfied", None) is False:
                # The step stopped while its own obligation was still open.
                status = "error"
            task["status"] = status
            icon = {"done": "✅", "error": "❌", "cancelled": "⏹"}.get(status, "•")
            note = f"{icon} {title}: {safe_clip((step_final or '').strip(), 400)}"
            step_notes.append(note)
            # Snapshot the full list so the UI does not depend on id/index matching.
            self._emit(
                "plan_step",
                {
                    "plan_id": plan_id,
                    "task_id": tid,
                    "index": i,
                    "status": status,
                    "title": title,
                    "summary": summary,
                    "tasks": snapshot_plan_tasks(tasks),
                },
            )
            if was_cancelled:
                break

        all_done = all(str(t.get("status") or "") == "done" for t in tasks)
        self._emit(
            "plan_done",
            {
                "plan_id": plan_id,
                "cancelled": was_cancelled,
                "message": (
                    "计划已停止"
                    if was_cancelled
                    else ("计划执行完成" if all_done else "计划尚未全部完成")
                ),
            },
        )
        if was_cancelled:
            heading = "### 执行结果（已停止）"
        elif all_done:
            heading = "### 执行结果"
        else:
            heading = "### 执行结果（尚未全部完成）"
        lines = [intro, heading, ""]
        for note in step_notes:
            lines.append(f"- {note}")
        remaining = [
            str(t.get("title") or "") for t in tasks if t.get("status") in (None, "pending")
        ]
        if was_cancelled and remaining:
            lines.append("")
            lines.append("未执行：" + "、".join(r for r in remaining if r))
        final = "\n".join(lines)
        self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
        self._emit("assistant_delta", {"chunk": "", "reset": True})
        self._emit("assistant_delta", {"chunk": final})
        return final, turned, was_cancelled, compressed

