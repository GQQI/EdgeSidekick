"""Agent mixin: plan_run."""
from __future__ import annotations

from typing import Any, Optional

from . import helpers as _h
from .helpers import *  # noqa: F403

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
                    # Show prep thinking in the chat; hide draft reply text until
                    # the confirmable plan is ready.
                    emit_reasoning=True,
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
            # Seal prep thinking into a finished bubble before the plan dialog.
            self._emit("assistant_reasoning_delta", {"chunk": "", "reset": True})
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
            return self._execute_plan_steps(
                plan_id, tasks, shape_contract, summary, user_text
            )
        finally:
            self._active_plan = None

    def _execute_plan_steps(
        self,
        plan_id: str,
        tasks: list[dict[str, Any]],
        shape_contract: dict[str, Any],
        summary: str,
        user_text: str = "",
    ) -> tuple[str, int, bool, bool]:
        from ..coherence import format_shape_contract_markdown

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
        # Original ask + every action so far. The cross-step gate judges the
        # user's goal, not the plan's own step list.
        turn_start = current_turn_start(self.messages)
        # Execution-time results for the cross-step gate; the transcript rows
        # may already be squeezed by the time a later step is judged.
        ledger = _evidence_ledger(self)
        turn_evidence_start = ledger.mark()
        goal = (user_text or "").strip() or active_obligation(self.messages)

        for i, task in enumerate(tasks):
            if self.cancelled():
                was_cancelled = True
                break
            tid = str(task.get("id") or new_id("task"))
            task["id"] = tid
            title = str(task.get("title") or f"步骤 {i + 1}")
            task["status"] = "running"
            snap_running = snapshot_plan_tasks(tasks)
            self._emit(
                "plan_step",
                {
                    "plan_id": plan_id,
                    "task_id": tid,
                    "index": i,
                    "status": "running",
                    "title": title,
                    "summary": summary,
                    "tasks": snap_running,
                    "done_count": sum(
                        1
                        for t in snap_running
                        if str(t.get("status") or "") in ("done", "skipped", "cancelled")
                    ),
                    "total_count": len(snap_running),
                },
            )
            self._emit(
                "assistant_status",
                {
                    "text": f"方案进度 {i + 1}/{len(tasks)} · 进行中 · {title}",
                    "tools": [],
                    "plan_id": plan_id,
                },
            )
            self.guard.begin_plan_step()
            # Keep the briefing short: replaying the full cumulative note list
            # every step is itself a source of restatement. The final report
            # below still lists every step.
            prior = "\n".join(f"- {s}" for s in step_notes[-4:]) if step_notes else ""
            outline = "\n".join(
                f"{'→' if j == i else ('✓' if j < i else '·')} "
                f"{j + 1}. {str(t.get('title') or f'步骤 {j + 1}')}"
                for j, t in enumerate(tasks)
            )
            reuse = (
                "This is a continuation of the same turn, not a new request. "
                "Tool results already in this conversation remain valid — "
                "do not re-fetch files or listings that were already returned."
            )
            step_guard = (
                f"You are executing ONLY plan step {i + 1}/{len(tasks)}: {title}\n"
                f"Full plan (→ current):\n{outline}\n"
                "Do not start, rewrite, or complete any other step. "
                "If later work is tempting, stop after this step's scope."
            )
            if i == 0:
                step_body = (
                    f"{step_guard}\n\n"
                    f"{task.get('detail') or ''}\n\n"
                    f"{reuse}\n"
                    "Complete only this step's scope. Reply with a brief summary "
                    "of what this step changed."
                )
            else:
                step_body = (
                    f"{step_guard}\n\n"
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
            from ...core.textutil import safe_clip

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
            stop_plan = False
            if not was_cancelled:
                # A plan is drafted before execution. If this step already met
                # the user's goal, the rest is unrequested polish — ask the same
                # judge that owns turn completion instead of forcing every step.
                remaining_tasks = tasks[i + 1 :]
                if remaining_tasks and goal:
                    judge_llm = getattr(self, "llm", None)
                    if judge_llm is None or not callable(
                        getattr(judge_llm, "complete_text", None)
                    ):
                        judge_llm = getattr(self, "compress_llm", None)
                    verdict = assess_goal_state(
                        judge_llm,
                        goal=goal,
                        evidence=tool_evidence(
                            self.messages,
                            start=turn_start,
                            rows=ledger.rows(start=turn_evidence_start)
                            or None,
                        ),
                        candidate=step_final or "",
                    )
                    if not should_run_next_plan_step(verdict):
                        for rest in remaining_tasks:
                            rest["status"] = "skipped"
                        stop_plan = True

            # Snapshot the full list so the UI does not depend on id/index matching.
            snap = snapshot_plan_tasks(tasks)
            self._emit(
                "plan_step",
                {
                    "plan_id": plan_id,
                    "task_id": tid,
                    "index": i,
                    "status": status,
                    "title": title,
                    "summary": summary,
                    "tasks": snap,
                    "done_count": sum(
                        1
                        for t in snap
                        if str(t.get("status") or "") in ("done", "skipped", "cancelled")
                    ),
                    "total_count": len(snap),
                },
            )
            self._emit(
                "assistant_status",
                {
                    "text": (
                        f"方案进度 {i + 1}/{len(tasks)} · "
                        f"{'完成' if status == 'done' else status} · {title}"
                    ),
                    "tools": [],
                    "plan_id": plan_id,
                },
            )
            if was_cancelled or stop_plan:
                break

        all_done = all(
            str(t.get("status") or "") in ("done", "skipped") for t in tasks
        )
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
        skipped = [
            str(t.get("title") or "") for t in tasks if t.get("status") == "skipped"
        ]
        if was_cancelled and remaining:
            lines.append("")
            lines.append("未执行：" + "、".join(r for r in remaining if r))
        if skipped:
            lines.append("")
            lines.append("目标已满足，无需执行：" + "、".join(s for s in skipped if s))
        final = "\n".join(lines)
        self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
        self._emit("assistant_delta", {"chunk": "", "reset": True})
        self._emit("assistant_delta", {"chunk": final})
        return final, turned, was_cancelled, compressed

    def _reject_early_stop(
        self,
        candidate: str,
        *,
        loop_start: int,
        hold_for_goal: bool,
        goal_nudges: int,
        emit_assistant_text: bool,
        evidence_start: int = 0,
    ) -> tuple[bool, int]:
        """Goal-completion end condition: continue only when it can progress.

        Tools in the schema are never a reason to reopen the turn. A
        conversational reply that already meets the user obligation is ``done``
        even with zero tool results; an action request without evidence is
        ``continue`` even when the model only narrated a plan. ``blocked`` and
        an unreadable verdict end the turn rather than looping on a guess.

        A ``continue`` verdict is only honored while the previous continuation
        produced new tool evidence. Without new evidence, the next completion
        can only restate the same answer — that is exactly the tape-recorder
        loop users see. The turn ends as *unresolved* (not "done") in that
        case, so the UI does not pretend the goal was met.
        """
        if not hold_for_goal or getattr(self, "_disable_tools", False):
            return False, goal_nudges
        ledger = _evidence_ledger(self)
        worker = bool(getattr(self, "is_subagent", False)) and not bool(
            getattr(self, "full_agent", False)
        )
        # A leaf's user turn is the bootstrap line, not its task. Judge the
        # assigned goal + scope it actually received, otherwise "done" is
        # unanswerable and the worker is pushed to keep acting.
        goal = active_obligation(
            self.messages,
            assigned_goal=str(getattr(self, "goal", "") or "") if worker else "",
            assigned_context=str(getattr(self, "context", "") or "") if worker else "",
        )
        if not (goal or "").strip():
            self._goal_satisfied = True
            self._emit(
                "goal_settled",
                {
                    "satisfied": True,
                    "blocked": False,
                    "state": "done",
                    "reason": "no active goal",
                },
            )
            return False, goal_nudges
        evidence = tool_evidence(
            self.messages,
            start=loop_start,
            rows=ledger.rows(start=evidence_start) or None,
        )
        prior = prior_assistant_substance(self.messages, start=loop_start)
        # Prefer the ReAct Think model for stop/continue. Fall back to
        # compress_llm only when Think has no complete_text (test fakes /
        # miswired clients). continue → another Think round.
        judge_llm = getattr(self, "llm", None)
        if judge_llm is None or not callable(getattr(judge_llm, "complete_text", None)):
            judge_llm = getattr(self, "compress_llm", None)
        verdict = assess_goal_verdict(
            judge_llm,
            goal=goal,
            evidence=evidence,
            candidate=candidate,
            prior_replies=prior,
            contract=getattr(self, "task_contract", None) if worker else None,
        )
        state = verdict.get("state")
        reason = str(verdict.get("reason") or "").strip()
        evidence_present = bool(verdict.get("evidence_present"))
        self._emit(
            "goal_judge",
            {
                "state": state,
                "reason": reason,
                "evidence_present": evidence_present,
                "nudges": int(goal_nudges or 0),
                "phase": "think_gate",
            },
        )
        if should_continue_for_goal(state):
            count = ledger.count()
            last_count = int(getattr(self, "_goal_evidence_count", 0) or 0)
            if goal_nudges > 0 and count <= last_count:
                # The LLM asked to continue, but this Think round produced no
                # new observation — another nudge can only restate the same
                # reply. Stop the loop; do not invent a "satisfied" outcome.
                self._goal_satisfied = False
                status = status_for_goal_verdict(
                    state, reason=reason, stalled_no_progress=True
                )
                self._emit("assistant_status", {"text": status, "tools": []})
                self._emit(
                    "goal_settled",
                    {
                        "satisfied": False,
                        "blocked": False,
                        "state": "continue",
                        "reason": reason or "no new evidence after continue",
                        "stalled": True,
                    },
                )
                return False, goal_nudges
            self._goal_evidence_count = count
            status = status_for_goal_verdict(state, reason=reason)
            self._emit(
                "assistant_status",
                {"text": status, "tools": [], "goal_state": state, "reason": reason},
            )
            if emit_assistant_text:
                self._emit("assistant_delta", {"chunk": "", "reset": True})
            # Back into ReAct Think with a short system delta — never a fake
            # user message that looks like the user asked to continue a loop.
            coalesce_internal_nudge(
                self.messages,
                kind="goal_continue",
                content=goal_continue_nudge(reason),
                role="system",
            )
            return True, goal_nudges + 1
        # Only an explicit done verdict closes the goal. An unreadable verdict
        # stops (a judge failure must not become an endless repair loop) but is
        # not reported as satisfied. A blocked goal stays open for the user.
        satisfied = None if state is None else state == "done"
        self._goal_satisfied = satisfied
        status = status_for_goal_verdict(state if isinstance(state, str) else None, reason=reason)
        self._emit("assistant_status", {"text": status, "tools": [], "goal_state": state})
        self._emit(
            "goal_settled",
            {
                "satisfied": satisfied,
                "blocked": state == "blocked",
                "state": state,
                "reason": reason,
            },
        )
        return False, goal_nudges
