"""ReAct agent with events, guardrails, compression, nested delegation."""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .agent_execute import AgentExecuteMixin, drop_unready_tool_calls, file_tool_results_blocked
from .agent_grounding import AgentGroundingMixin
from .agent_history import AgentHistoryMixin, prune_stale_internal_nudges
from .approval import ApprovalGate
from .ask import (
    AskGate,
    MIN_ASK_OPTIONS,
    build_ask_options,
    normalize_option_labels,
    should_keep_as_markdown,
    try_parse_inline_ask,
)
from ..core.config import Settings, get_settings
from ..core.textutil import split_dangling_colon, strip_dangling_colon
from .context import (
    COMPACTION_MARK,
    cheap_compact,
    context_budget_tokens,
    current_turn_start,
    debug_dump_budget,
    dedupe_repeated_context,
    ensure_fit,
    messages_tokens,
    park_overflow,
    repair_incomplete_tool_chains,
    schemas_tokens,
    squeeze_fresh_tool_results,
)
from ..core.events import EventBus, emit, new_id
from ..core.guardrails import Guardrails, repeating_tool_sequence, tool_call_signature
from .llm import (
    LLM,
    fold_reasoning_text,
    parse_tool_args,
    streaming_text_looped,
)
from .evidence import EvidenceLedger
from .reasoning_gate import ReasoningRoundGate, dedupe_reasoning_round
from .tool_repeats import (
    STOP as TOOL_REPEAT_STOP,
    ToolRepeatTracker,
    call_signature_set,
    result_history,
    stalled_tool_cycle,
)
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
    needs_plan,
    snapshot_plan_tasks,
)
from .prompts import (
    apply_extensions_block,
    apply_response_language,
    build_system_prompt,
    language_pin_messages,
)
from .coherence import inject_contract_into_goal, shape_contract_from_plan
from .goal_completion import (
    active_obligation,
    assess_goal_state,
    assess_goal_verdict,
    coalesce_internal_nudge,
    extract_named_tool_ids,
    goal_continue_nudge,
    intent_probe_text,
    looks_like_tool_intent_only,
    prior_assistant_substance,
    resolve_mentioned_tools,
    should_continue_for_goal,
    should_continue_for_tool_intent,
    should_run_next_plan_step,
    status_for_goal_verdict,
    tool_evidence,
    tool_intent_unavailable_message,
)
from .review import run_review
from ..services.skills import Skill, invalidate_skills_cache, load_skills
from .tool_registry import drop_delegate_schemas, filter_readonly_schemas, skill_tool_name
from .tools import ToolRegistry, build_registry
from .tools.build import register_mcp_safely, register_plugins_safely
from .tools.support import _skill_as_tool

PrintFn = Callable[[str], None]


def _subagent_snapshot_item(
    child: Agent,
    party: str = "",
    *,
    turn: int = 0,
) -> dict[str, Any]:
    kind = "party" if child.full_agent else ("talk" if child.talk_only else "task")
    label = party or (child.goal or "").strip().split("\n")[0][:80]
    transcript: list[dict[str, Any]] = []
    for m in child.messages:
        role = m.get("role")
        if role == "assistant":
            text = str(m.get("content") or "").strip()
            reasoning = str(m.get("reasoning") or "").strip()
            try:
                from .text_tool_calls import extract_text_tool_calls

                text, xml_calls = extract_text_tool_calls(text)
            except Exception:
                xml_calls = []
            if text or reasoning:
                item: dict[str, Any] = {"kind": "assistant", "text": text}
                if reasoning:
                    item["reasoning"] = reasoning
                transcript.append(item)
            if not m.get("tool_calls"):
                for tc in xml_calls:
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or "").strip()
                    if name:
                        transcript.append({"kind": "tool", "name": name, "result": ""})
        elif role == "tool":
            transcript.append(
                {
                    "kind": "tool",
                    "name": str(m.get("name") or "tool"),
                    "result": str(m.get("content") or "")[:2000],
                }
            )
    return {
        "child_id": child.agent_id,
        "goal": child.goal or label,
        "role": child.role,
        "kind": kind,
        "party": party,
        "label": label,
        "parent_id": child.parent_id,
        "turn": turn,
        "replay": True,
        "transcript": transcript[-30:],
        "status": "running",
        "activity": "运行中…",
        "message": f"resume {child.role}: {label}",
    }


@dataclass
class AgentResult:
    text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    compressed: bool = False
    agent_id: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


def _evidence_ledger(agent: Any) -> EvidenceLedger:
    """The turn's evidence ledger, created on first use.

    Also covers test doubles and agents restored from a pickle that predates
    the attribute, so every entry point can call it unconditionally.
    """
    ledger = getattr(agent, "_evidence", None)
    if ledger is None:
        ledger = EvidenceLedger()
        agent._evidence = ledger
    return ledger


_STALL_NUDGE = (
    "Previous generation stalled. Continue now: call a tool or answer briefly. "
    "Do not restart a long silent thinking pass."
)
_FILE_RETRY_NUDGE = (
    "Previous file tool returned ERROR/WARNING — not success. "
    "Retry with complete args, look up a missing path, or change encoding=. "
    "Do not invent a file body or repeat the same missing path."
)
_BROWSER_NAV_RECOVER_NUDGE = (
    "browser_navigate did not load the page (connection refused / chrome-error / "
    "load failure). Do NOT stop the task and do NOT emit a final answer that says "
    "the page did not open or that retries have stopped. Do NOT call browser_navigate "
    "again with the identical URL/args. Recover toward the user goal: if the target "
    "is localhost/127.0.0.1, start or restart the static/dev server for that workspace "
    "file first (bind 127.0.0.1), confirm the listening port, then navigate; for a "
    "static .html prefer the workspace-relative path; try localhost vs 127.0.0.1 when "
    "one refused; diagnose with run_shell / read_file as needed. Keep working."
)
_POST_COMPRESS_CONTINUE_NUDGE = (
    "Context was compacted into working memory. "
    "Your previous assistant output was NOT truncated — do not apologize about "
    "truncation or stop. Continue the same user goal now: call a tool or give "
    "the final answer from existing evidence."
)
_EMPTY_ASSISTANT_RECOVER_NUDGE = (
    "Previous model round returned no message (often after a large tool result "
    "or context compaction). Continue the same user goal: call a tool or answer "
    "briefly. Do not apologize about truncation."
)
class Agent(AgentHistoryMixin, AgentExecuteMixin, AgentGroundingMixin):
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        is_subagent: bool = False,
        role: str = "leaf",
        goal: str = "",
        context: str = "",
        depth: int = 0,
        parent_id: str = "",
        agent_id: Optional[str] = None,
        bus: Optional[EventBus] = None,
        on_event: Optional[PrintFn] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        turn_counter: int = 0,
        approval: Optional[ApprovalGate] = None,
        ask: Optional[AskGate] = None,
        plan_gate: Optional[PlanGate] = None,
        talk_only: bool = False,
        full_agent: bool = False,
        social: bool = False,
    ):
        self.settings = (settings or get_settings()).clone()
        self.is_subagent = is_subagent
        if is_subagent:
            self._apply_subagent_context_budget()
        self.talk_only = bool(talk_only)
        self.full_agent = bool(full_agent) and not self.talk_only
        self.social = bool(social)
        self.role = role if role in ("leaf", "orchestrator") else "leaf"
        self.goal = goal
        self.context = context
        self.depth = depth
        self.parent_id = parent_id
        self.agent_id = agent_id or new_id("agent")
        self.session_id: Optional[str] = None
        self.bus = bus or EventBus()
        self.on_event = on_event
        self.turn_counter = turn_counter
        self.guard = Guardrails(same_call_fail_limit=self.settings.same_call_fail_limit)
        if is_subagent:
            # Research / paper-reading children legitimately open many files.
            self.guard.max_explore_streak = max(self.guard.max_explore_streak, 64)
        self._cancel = threading.Event()
        self._abandoned = False
        self._last_compressed = False
        self._compress_lock = threading.Lock()
        self._compress_job: Optional[dict[str, Any]] = None
        self._archived_messages: list[dict[str, Any]] = []
        # Execution-time tool results for the goal judge, kept out of the
        # context window so squeezing cannot erase the evidence.
        self._evidence = EvidenceLedger()
        self._mid_seq: int = 0
        # The plan currently being executed by _execute_plan (None when idle).
        # Single source of truth for the pinned plan panel on reattach.
        self._active_plan: Optional[dict[str, Any]] = None
        self._browser_same_runs = 0
        self._browser_fail_streak = 0
        self._incomplete_write_streak = 0
        self.approval = approval or ApprovalGate()
        self.ask = ask or AskGate()
        self.plan_gate = plan_gate or PlanGate()
        self._children: list[Agent] = []
        self._party_agents: dict[str, Agent] = {}
        self._canvas_index: dict[str, dict[str, Any]] = {}
        self._ask_edges: list[dict[str, Any]] = []
        self._canvas_turn = 0
        self._child_lock = threading.Lock()
        self._allow_mutating_tools = not self.talk_only

        self.skills: list[Skill] = [] if self.talk_only else load_skills(self.settings.skills_dir)
        self.rebuild_llms()

        # Teams are intentionally one level deep: workers execute scoped work
        # and report to the lead; they never create invisible grandchild teams.
        can_delegate = (not self.talk_only) and not is_subagent
        self.registry: ToolRegistry = build_registry(
            self.settings,
            skills=self.skills,
            allow_delegate=can_delegate,
            run_child=self._run_child if can_delegate else None,
            ask_user_fn=None if self.talk_only or (is_subagent and not self.full_agent) else self._ask_user,
            talk_only=self.talk_only,
            end_party_session=self._end_party_session if can_delegate else None,
            note_canvas_tasks=self._note_canvas_tasks if can_delegate else None,
            note_ask_edge=self._note_ask_edge if can_delegate else None,
        )
        # Sticky facts from tools (list_dir / codebase_*) so later turns don't invent paths.
        self.workspace_facts: list[str] = []
        self._turn_policy = None
        self._turn_mutated = False
        self._turn_verified = False

        if messages is not None:
            self.messages = messages
        else:
            if self.social:
                from .prompts import build_social_system_prompt

                system = build_social_system_prompt(workspace=self.settings.workspace)
            else:
                system = build_system_prompt(
                workspace=self.settings.workspace,
                skills=self.skills,
                memory_file=self.settings.memory_file,
                skills_dir=self.settings.skills_dir,
                is_subagent=is_subagent,
                role=self.role,
                goal=goal,
                context=context,
                depth=depth,
                max_depth=self.settings.max_spawn_depth,
                talk_only=self.talk_only,
                full_agent=self.full_agent,
            )
            self.messages = [{"role": "system", "content": system}]
        if (not is_subagent) or self.full_agent:
            self._refresh_workspace_grounding()

    def rebuild_llms(self) -> None:
        """(Re)construct self.llm / self.compress_llm from self.settings.

        Called at __init__ and whenever settings.model/subagent_model/
        compress_model change after construction — e.g. a settings refresh,
        or an "Auto" model pick resolved for the current turn.
        """
        llm_cap = int(getattr(self.settings, "max_tokens", 0) or 0) or None
        if self.is_subagent:
            self.llm = LLM(
                self.settings,
                model=self.settings.subagent_model,
                api_key=getattr(self.settings, "subagent_api_key", None) or self.settings.api_key,
                base_url=getattr(self.settings, "subagent_base_url", None) or self.settings.base_url,
                max_tokens=llm_cap,
            )
        else:
            self.llm = LLM(self.settings, model=self.settings.model, max_tokens=llm_cap)
        self.compress_llm = LLM(
            self.settings,
            model=self.settings.compress_model,
            api_key=getattr(self.settings, "compress_api_key", None) or self.settings.api_key,
            base_url=getattr(self.settings, "compress_base_url", None) or self.settings.base_url,
            max_tokens=int(getattr(self.settings, "compress_max_tokens", 0) or 0) or None,
        )

    def _refresh_skills(self) -> None:
        """Pick up skills imported after this session started."""
        if self.talk_only:
            return
        invalidate_skills_cache(self.settings.skills_dir)
        fresh = load_skills(self.settings.skills_dir)
        self.skills[:] = fresh
        self.registry.drop_skill_tools()
        for sk in self.skills:
            self.registry.register(_skill_as_tool(sk))
        register_mcp_safely(self.registry, settings=self.settings)
        register_plugins_safely(self.registry, settings=self.settings)
        from .tools.build import apply_extension_budget_safely

        recent = list(getattr(self.settings, "recent_tool_successes", None) or [])
        mention = str(getattr(self, "_budget_mention_text", "") or "") or str(
            getattr(self.settings, "budget_mention_text", "") or ""
        )
        apply_extension_budget_safely(
            self.registry,
            settings=self.settings,
            recent=recent,
            mention_text=mention or None,
        )

    def _requested_skill_tool(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        m = re.search(r"请立即调用函数工具\s*`?(skill_[A-Za-z0-9_]+)`?", raw)
        if m:
            name = m.group(1)
            return name if self.registry.get(name) else ""
        m = re.search(r"/skill\s+([A-Za-z0-9._-]+)", raw, re.I)
        if not m:
            return ""
        want = skill_tool_name(m.group(1))
        if self.registry.get(want):
            return want
        token = re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_")
        for name in self.registry.names():
            if name.startswith("skill_") and token and token in name:
                return name
        return ""

    def _autoload_requested_skill(self, user_text: str) -> None:
        name = self._requested_skill_tool(user_text)
        if not name:
            return
        task = ""
        m = re.search(r"task 参数为：(.+?)(?:。|$)", user_text, re.S)
        if m:
            task = m.group(1).strip()
        if not task or "可省略 task" in task:
            task = "按该 Skill 的标准流程执行"
        call_id = "skill_autoload_1"
        tool_calls = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps({"task": task}, ensure_ascii=False),
                },
            }
        ]
        self.messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        self._emit("assistant_status", {"text": f"调用工具：{name}", "tools": [name]})
        batch_start = len(self.messages)
        for tr in self._execute_tools(tool_calls):
            self.messages.append(tr)
        self._squeeze_tool_batch(batch_start)

    def _apply_subagent_context_budget(self) -> None:
        """Each child gets its own smaller window so a long debate cannot exhaust max_tokens."""
        s = self.settings
        parent_limit = max(4000, int(s.context_limit or 48000))
        explicit = int(getattr(s, "subagent_context_limit", 0) or 0)
        child_limit = explicit if explicit > 0 else int(parent_limit * 0.4)
        child_limit = max(8000, min(child_limit, 24000, parent_limit))
        s.context_limit = child_limit
        keep = int(s.keep_recent_tokens or 12000)
        s.keep_recent_tokens = min(keep, max(3000, child_limit // 3))
        ratio = float(s.compress_trigger_ratio or 0.72)
        s.compress_trigger_ratio = min(ratio, 0.55)
        cap = int(getattr(s, "tool_result_cap", 18000) or 18000)
        s.tool_result_cap = min(cap, 12000)
        parent_out = int(getattr(s, "max_tokens", 0) or 0)
        explicit = int(getattr(s, "subagent_max_tokens", 0) or 0)
        # 2048 used to cut tool JSON mid-stream → empty {} / truncated summaries.
        if explicit > 0:
            out_cap = explicit
        elif parent_out > 0:
            out_cap = parent_out
        else:
            out_cap = 8192
        if hasattr(s, "max_tokens"):
            s.max_tokens = out_cap
        if hasattr(s, "subagent_max_tokens"):
            s.subagent_max_tokens = out_cap

    def stop_generation(self) -> None:
        """Stop this agent tree's LLM streams without aborting parent asks/approvals."""
        self._cancel.set()
        try:
            self.llm.close_active_stream()
        except Exception as exc:
            from ..core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), "close_active_stream failed", exc)
        for child in list(self._children):
            try:
                child.stop_generation()
            except Exception as exc:
                from ..core.logutil import get_logger, log_exception

                log_exception(get_logger("metateam.agent"), "child cancel failed", exc)

    def request_cancel(self) -> None:
        self.stop_generation()
        # Unblock any waiting approvals / asks / plan confirms as rejected
        self.approval.cancel_all()
        self.ask.cancel_all()
        self.plan_gate.cancel_all()

    def clear_cancel(self) -> None:
        self._cancel.clear()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _log(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def _emit(self, type_: str, data: Optional[dict[str, Any]] = None) -> None:
        if self._abandoned:
            return
        emit(self.bus, type_, data, agent_id=self.agent_id, parent_id=self.parent_id)
        if data and "message" in (data or {}):
            self._log(str(data["message"]))

    def _wait_child_run(self, child: "Agent", user_text: str) -> AgentResult:
        timeout = float(getattr(self.settings, "subagent_timeout", 0) or 0)
        if timeout <= 0:
            return child.run(user_text, do_review=False)
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(lambda: child.run(user_text, do_review=False))
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            child.stop_generation()
            try:
                result = fut.result(timeout=12)
                text = (result.text or "").strip() or (
                    f"ERROR: subagent timed out after {int(timeout)}s "
                    "(stuck thinking). Parent should continue with other results."
                )
                return AgentResult(
                    text=text,
                    messages=result.messages,
                    iterations=result.iterations,
                    cancelled=False,
                    agent_id=child.agent_id,
                )
            except FuturesTimeout:
                child._abandoned = True
                return AgentResult(
                    text=(
                        f"ERROR: subagent timed out after {int(timeout)}s "
                        "(stuck thinking). Parent should continue with other results."
                    ),
                    cancelled=False,
                    agent_id=child.agent_id,
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _run_child(
        self,
        *,
        goal: str,
        context: str = "",
        role: str = "leaf",
        kind: str = "task",
        persist_key: str = "",
        child_id: str = "",
    ) -> str:
        child_role = role if role in ("leaf", "orchestrator") else "leaf"
        next_depth = self.depth + 1
        if child_role == "orchestrator" and next_depth >= self.settings.max_spawn_depth:
            child_role = "leaf"
        kind_s = (kind or "task").strip().lower()
        talk_only = kind_s == "talk"
        full_agent = kind_s in ("party", "full")
        key = (persist_key or "").strip()
        resumed = bool(key and key in self._party_agents)
        preset_id = (child_id or "").strip()

        if resumed:
            child = self._party_agents[key]
            child_id = child.agent_id
        else:
            child_id = preset_id or new_id("agent")

        event_kind = "party" if full_agent else ("talk" if talk_only else "task")
        label = key or (goal or context or "").strip().split("\n")[0][:80]
        self._emit(
            "subagent_start",
            {
                "child_id": child_id,
                "goal": goal,
                "role": child_role,
                "depth": next_depth,
                "kind": event_kind,
                "party": key,
                "label": label,
                "resumed": resumed,
                "message": f"{'resume' if resumed else 'spawn'} {child_role}: {label}",
            },
        )
        if not resumed:
            # Minimal grounding for task workers: they skip the workspace
            # grounding refresh, so hand them the root path + top-level list
            # or their first read/search guesses at imaginary layouts.
            if not full_agent and not talk_only:
                from .agent_grounding import minimal_grounding

                block = minimal_grounding(self.settings.workspace)
                context = (block + "\n\n" + (context or "").strip()).strip()
            child = Agent(
                self.settings,
                is_subagent=True,
                role=child_role,
                goal=goal,
                context="" if (full_agent or key) else context,
                depth=next_depth,
                parent_id=self.agent_id,
                agent_id=child_id,
                bus=self.bus,
                on_event=self.on_event,
                approval=self.approval,
                ask=self.ask,
                plan_gate=self.plan_gate,
                talk_only=talk_only,
                full_agent=full_agent,
            )
            with self._child_lock:
                self._children.append(child)
                if key:
                    self._party_agents[key] = child
        with self._child_lock:
            self._remember_canvas_child(child, party=key)
        self._emit_canvas_sync()
        if self.cancelled():
            child.request_cancel()
        child._inherited_language = str(getattr(self, "_response_language_src", "") or "")
        if resumed or full_agent:
            user_text = (context or "").strip() or (goal or "").strip() or "Your turn."
        elif talk_only:
            user_text = (
                "Speak your turn now. Do not write code or files. "
                "Output only in-character dialogue."
            )
        else:
            user_text = "Begin now. Use tools, then summarize."
        result_text = "(empty summary)"
        iterations = 0
        cancelled = False
        try:
            result = self._wait_child_run(child, user_text)
            result_text = result.text or "(empty summary)"
            iterations = result.iterations
            cancelled = bool(result.cancelled)
        except Exception as exc:  # noqa: BLE001
            from ..core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), f"child {child_id} failed", exc)
            result_text = f"ERROR: child failed: {exc}"
            cancelled = True
        finally:
            with self._child_lock:
                self._ingest_canvas_subtree(
                    child,
                    party=key,
                    status="error" if cancelled or str(result_text).startswith("ERROR") else "done",
                )
                if not key:
                    self._children = [c for c in self._children if c is not child]
            self._emit_canvas_sync()
            self._emit(
                "subagent_end",
                {
                    "child_id": child_id,
                    "goal": goal,
                    "summary": (result_text or "")[:12000],
                    "iterations": iterations,
                    "cancelled": cancelled,
                    "message": f"done: {(goal or context)[:60]}",
                },
            )
        return result_text

    def _end_party_session(self) -> None:
        for name, child in list(self._party_agents.items()):
            self._ingest_canvas_subtree(child, party=name)
        for child in list(self._party_agents.values()):
            self._children = [c for c in self._children if c is not child]
        self._party_agents.clear()
        self._emit_canvas_sync()

    def _note_canvas_tasks(self, items: list[Any] | None) -> None:
        """Reserve a canvas slot for every queued worker, including those waiting on the pool."""
        turn = int(getattr(self, "_canvas_turn", 0) or 0)
        rows = items if isinstance(items, list) else []
        with self._child_lock:
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                cid = str(raw.get("child_id") or "").strip()
                goal = str(raw.get("goal") or "").strip()
                if not cid or not goal:
                    continue
                prev = self._canvas_index.get(cid)
                if isinstance(prev, dict) and str(prev.get("status") or "") in ("done", "error"):
                    continue
                label = goal.split("\n")[0][:80]
                self._canvas_index[cid] = {
                    "child_id": cid,
                    "parent_id": self.agent_id,
                    "goal": goal,
                    "role": str(raw.get("role") or "leaf"),
                    "kind": "task",
                    "party": "",
                    "label": label,
                    "turn": turn,
                    "replay": True,
                    "transcript": list(prev.get("transcript") or []) if isinstance(prev, dict) else [],
                    "status": "running",
                    "activity": "排队中…",
                    "message": f"queue leaf: {label}",
                    "children": [],
                }
        self._emit_canvas_sync()

    def _remember_canvas_child(self, child: Agent, party: str = "", *, status: str = "running") -> None:
        item = _subagent_snapshot_item(
            child,
            party,
            turn=int(getattr(self, "_canvas_turn", 0) or 0),
        )
        item["status"] = "error" if status == "error" else ("running" if status == "running" else "done")
        item["activity"] = "运行中…" if item["status"] == "running" else ""
        self._canvas_index[child.agent_id] = item

    def _ingest_canvas_subtree(self, child: Agent, party: str = "", *, status: str = "done") -> None:
        finished = "error" if str(status).startswith("error") else "done"
        self._remember_canvas_child(child, party, status=finished)
        kids = getattr(child, "_children", None)
        if isinstance(kids, list):
            for grand in list(kids):
                self._ingest_canvas_subtree(grand, status=finished)
        parties = getattr(child, "_party_agents", None)
        if isinstance(parties, dict):
            for name, grand in list(parties.items()):
                self._ingest_canvas_subtree(grand, party=str(name), status=finished)


    def _note_ask_edge(self, edge: dict[str, Any] | None) -> None:
        """Record an AskPacket edge for canvas (who asked whom + visibility)."""
        if not isinstance(edge, dict):
            return
        row = {
            "id": str(edge.get("id") or "").strip() or f"ask_{len(self._ask_edges)+1}",
            "from_id": str(edge.get("from_id") or self.agent_id or "").strip(),
            "to_id": str(edge.get("to_id") or "").strip(),
            "kind": str(edge.get("kind") or "consult").strip() or "consult",
            "what": str(edge.get("what") or "").strip(),
            "how": str(edge.get("how") or "").strip(),
            "who": str(edge.get("who") or "").strip(),
            "when": str(edge.get("when") or "now").strip() or "now",
            "why": str(edge.get("why") or "").strip(),
            "need": str(edge.get("need") or edge.get("question") or "").strip(),
            "goal_slice": str(edge.get("goal_slice") or "").strip(),
            "visibility": edge.get("visibility") if isinstance(edge.get("visibility"), dict) else {},
            "status": str(edge.get("status") or "sent").strip() or "sent",
            "summary": str(edge.get("summary") or "").strip()[:500],
        }
        if not row["to_id"] and not row["need"]:
            return
        with self._child_lock:
            replaced = False
            if not row["id"]:
                # match latest same to_id+need
                for i in range(len(self._ask_edges) - 1, -1, -1):
                    prev = self._ask_edges[i]
                    if str(prev.get("to_id")) == row["to_id"] and str(prev.get("need")) == row["need"]:
                        row["id"] = str(prev.get("id") or row["id"])
                        self._ask_edges[i] = {**prev, **{k: v for k, v in row.items() if v}}
                        replaced = True
                        break
            if not replaced:
                for i, prev in enumerate(self._ask_edges):
                    if row["id"] and str(prev.get("id")) == row["id"]:
                        self._ask_edges[i] = {**prev, **row}
                        replaced = True
                        break
            if not replaced:
                if not row["id"]:
                    row["id"] = f"ask_{len(self._ask_edges)+1}"
                self._ask_edges.append(row)
            if len(self._ask_edges) > 80:
                self._ask_edges = self._ask_edges[-80:]
        try:
            self._emit(
                "ask_edge",
                {"edge": row, "asks": list(self._ask_edges)},
            )
        except Exception:
            pass
        self._emit_canvas_sync()

    def _emit_canvas_sync(self) -> None:
        """Push the full spawn forest so the live canvas matches persisted history."""
        emit = getattr(self, "_emit", None)
        if not callable(emit):
            return
        # Nested leaves update the parent index via ingest; only the lead
        # (or a full party) owns the forest the UI should paint.
        if bool(getattr(self, "is_subagent", False)) and not bool(
            getattr(self, "full_agent", False)
        ):
            return
        try:
            emit(
                "canvas_sync",
                {
                    "tree": self.canvas_tree(),
                    "turn": int(getattr(self, "_canvas_turn", 0) or 0),
                    "asks": list(getattr(self, "_ask_edges", []) or []),
                },
            )
        except Exception:
            return

    def clear_canvas_forest(self) -> None:
        """Drop spawn cards / ask edges so a resend cannot revive the last team."""
        with self._child_lock:
            self._canvas_index.clear()
            self._ask_edges.clear()

    def canvas_tree(self) -> list[dict[str, Any]]:
        """Durable forest of spawned agents for the active canvas turn only."""
        turn = int(getattr(self, "_canvas_turn", 0) or 0)
        by_id: dict[str, dict[str, Any]] = {}
        for item in self._canvas_index.values():
            if not isinstance(item, dict):
                continue
            cid = str(item.get("child_id") or "").strip()
            if not cid:
                continue
            item_turn = int(item.get("turn") or 0)
            # Only the current user-turn's team belongs on the live board.
            if turn > 0 and item_turn > 0 and item_turn != turn:
                continue
            by_id[cid] = {**item, "children": []}
        for item in self.live_subagent_snapshot():
            cid = str(item.get("child_id") or "").strip()
            if cid:
                prev = by_id.get(cid) or {}
                kids = prev.get("children") or []
                by_id[cid] = {**prev, **item, "children": kids}
        roots: list[dict[str, Any]] = []
        for item in by_id.values():
            pid = str(item.get("parent_id") or "").strip()
            parent = by_id.get(pid)
            if parent is not None:
                parent.setdefault("children", []).append(item)
            else:
                roots.append(item)
        return roots

    def active_plan_snapshot(self) -> Optional[dict[str, Any]]:
        """The plan being executed right now, or None. Cleared on plan_done/cancel."""
        plan = self._active_plan
        if not plan:
            return None
        return {
            "plan_id": str(plan.get("plan_id") or ""),
            "summary": str(plan.get("summary") or ""),
            "shape_contract": plan.get("shape_contract") or {},
            "tasks": snapshot_plan_tasks(list(plan.get("tasks") or [])),
        }

    def live_subagent_snapshot(self) -> list[dict[str, Any]]:
        """Running children (including nested) so a reattached UI can restore cards."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def party_name(agent: Agent, child: Agent) -> str:
            for name, party in agent._party_agents.items():
                if party is child or party.agent_id == child.agent_id:
                    return str(name)
            return ""

        def walk(agent: Agent) -> None:
            ordered: list[tuple[str, Agent]] = []
            local: set[str] = set()
            for child in list(agent._children):
                ordered.append((party_name(agent, child), child))
                local.add(child.agent_id)
            for name, child in list(agent._party_agents.items()):
                if child.agent_id in local:
                    continue
                ordered.append((str(name), child))
            for party, child in ordered:
                if child.agent_id in seen:
                    continue
                seen.add(child.agent_id)
                out.append(
                    _subagent_snapshot_item(
                        child,
                        party,
                        turn=int(getattr(self, "_canvas_turn", 0) or 0),
                    )
                )
                walk(child)

        walk(self)
        return out

    def _ask_user(
        self,
        *,
        question: str,
        options: list[str] | None = None,
        allow_custom: bool = True,
        custom_label: str = "其他（请补充）",
        # Legacy flat args (older prompts / cached tool calls)
        option_a: str = "",
        option_b: str = "",
        option_c: str = "",
        option_d: str = "",
    ) -> str:
        q = (question or "").strip()
        if not q:
            return "ERROR: empty question"

        labels = normalize_option_labels(options)
        if len(labels) < MIN_ASK_OPTIONS:
            legacy = [
                str(option_a or "").strip(),
                str(option_b or "").strip(),
                str(option_c or "").strip(),
            ]
            labels = [x for x in legacy if x]
            if str(option_d or "").strip():
                labels.append(str(option_d).strip())

        built = build_ask_options(labels)
        if len(built) < MIN_ASK_OPTIONS:
            return (
                f"ERROR: ask_user needs at least {MIN_ASK_OPTIONS} options; "
                f"got {len(built)}"
            )
        if should_keep_as_markdown(q, labels):
            return (
                "ERROR: these options look like a summary/要点, not mutually exclusive "
                "choices. If the user must pick one next action, call ask_user again "
                "with a direct question and 2–12 option labels that each name one "
                "choice (put file lists and reasons in why/constraints, not in every "
                "label). If you are only reporting findings, write them as markdown "
                "and do not call ask_user."
            )

        allow_other = bool(allow_custom)
        other_label = str(custom_label or "其他（请补充）").strip() or "其他（请补充）"
        with self.ask.serialize_ui():
            if self.cancelled():
                return "ERROR: cancelled — proceed with a reasonable default or stop."
            ask_id = new_id("ask")
            call_id = new_id("call")
            self._emit("assistant_delta", {"chunk": "", "reset": True, "discard": True})
            self._emit(
                "ask_request",
                {
                    "ask_id": ask_id,
                    "call_id": call_id,
                    "session_id": self.session_id or "",
                    "question": q,
                    "options": built,
                    "allow_custom": allow_other,
                    "custom_label": other_label,
                    "summary": f"询问用户: {q[:120]}",
                    "message": f"等待用户选择：{q[:80]}",
                },
            )
            answer = self.ask.request(
                ask_id,
                q,
                built,
                allow_custom=allow_other,
                custom_label=other_label,
            )
            self._emit(
                "ask_resolved",
                {
                    "ask_id": ask_id,
                    "call_id": call_id,
                    "answer": answer,
                    "message": "用户已回答" if not str(answer).startswith("ERROR:") else "询问已取消或超时",
                },
            )
        return answer

    def _assistant_tool_args_incomplete(self, msg: dict[str, Any]) -> bool:
        """True when write/edit payloads were truncated to a path-only placeholder."""
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            args = parse_tool_args(str((fn or {}).get("arguments") or "{}"))
            if not isinstance(args, dict):
                continue
            if args.get("_incomplete"):
                return True
            if name in {"write_file", "str_replace"}:
                body = str(args.get("content") or args.get("new_string") or args.get("new_str") or "").strip()
                if not body:
                    return True
        return False

    @staticmethod
    def _tool_call_sig_set(msg: dict[str, Any]) -> frozenset[str]:
        """Signatures for complete tool calls only (truncated writes are path-only and would false-trip)."""
        return call_signature_set(msg)


    @staticmethod
    def _browser_nav_result_failed(content: str) -> bool:
        """True when a browser_navigate tool result did not land a usable page."""
        text = str(content or "")
        low = text.lower()
        if text.lstrip().startswith("ERROR"):
            return True
        if "chrome-error://" in low or "chromewebdata" in low:
            return True
        if (
            "err_connection_refused" in low
            or "connection refused" in low
            or "econnrefused" in low
        ):
            return True
        if "页面加载失败" in text:
            return True
        # Deferred navigate JSON with ready:false + refusal note.
        stripped = text.lstrip()
        if stripped.startswith("{") and '"ready": false' in low:
            if "refused" in low or "chrome-error" in low or "load fail" in low:
                return True
        return False

    @staticmethod
    def _live_browser_repeat(msg: dict[str, Any]) -> bool:
        """Same args on a live page are a refresh, not a stuck file/search call."""
        names: list[str] = []
        for tc in (msg or {}).get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            if name:
                names.append(name)
        return bool(names) and all(name.startswith("browser_") for name in names)

    @staticmethod
    def _prior_needs_human(text: str) -> bool:
        low = (text or "").lower()
        return any(
            key in low
            for key in ("captcha", "needs_human", "challenge", "安全验证", "人机验证")
        )

    def _find_recent_tool_content(self, name: str, *, max_chars: int = 1200) -> str:
        """Best-effort recall of a prior tool payload still sitting in messages."""
        name = str(name or "")
        for m in reversed(self.messages):
            if m.get("role") != "tool":
                continue
            if name and str(m.get("name") or "") not in {"", name}:
                continue
            text = str(m.get("content") or "").strip()
            if not text:
                continue
            if text.startswith("[cleared tool result:"):
                continue
            if text.startswith("[already in context]"):
                continue
            if len(text) > max_chars:
                return text[:max_chars].rstrip() + "\n…"
            return text
        return ""

    def _prior_results_for_tool_calls(self, assistant: dict[str, Any]) -> str:
        """Rebuild substance for repeated tool calls from digests / history."""
        parts: list[str] = []
        seen: set[str] = set()
        for tc in assistant.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            if not name:
                continue
            args = parse_tool_args(str((fn or {}).get("arguments") or "{}"))
            if not isinstance(args, dict):
                args = {}
            sig = tool_call_signature(name, args)
            if sig in seen:
                continue
            seen.add(sig)
            digest = ""
            try:
                digest = str(self.guard.digest_for_signature(sig) or "").strip()
            except Exception:
                digest = ""
            if not digest:
                digest = self._find_recent_tool_content(name)
            if digest:
                parts.append(f"### {name}\n{digest}")
            else:
                parts.append(
                    f"### {name}\n（未找到可回放的正文；请换参数继续，或基于你已得出的结论推进。）"
                )
        return "\n\n".join(parts)

    def _collapse_repeated_assistant(self, assistant: dict[str, Any]) -> bool:
        """Drop copy-pasted prose. True = a duplicate final answer ends the turn.

        Tool-call repeats are deliberately *not* decided here: the same call
        with the same args can legitimately return a new observation (a poll,
        a re-run after an edit). :class:`ToolRepeatTracker` compares results
        and owns that decision, so this helper only removes repeated narration
        and stops when an answer itself was already shown.
        """

        def _norm(text: str) -> str:
            return re.sub(r"\s+", " ", (text or "").strip())

        prev: Optional[dict[str, Any]] = None
        for m in reversed(self.messages[:-1]):
            if m.get("role") == "assistant":
                prev = m
                break
        if not prev:
            return False
        text = _norm(str(assistant.get("content") or ""))
        prev_text = _norm(str(prev.get("content") or ""))
        text_repeats = bool(text) and len(text) >= 24 and (
            text == prev_text
            or (len(prev_text) >= 40 and prev_text in text)
            or (len(text) >= 40 and text in prev_text)
        )
        if not text_repeats:
            return False
        assistant["content"] = ""
        if assistant.get("tool_calls"):
            # Keep the call; the repeat tracker decides whether it may run.
            return False
        return True

    def _previous_round_reasoning(self, start: int) -> str:
        """Thinking of the last round in this loop that said something."""
        for msg in reversed(self.messages[max(0, start) :]):
            if msg.get("role") != "assistant":
                continue
            text = str(msg.get("reasoning") or "").strip()
            if text:
                return text
        return ""

    def _compress_msg_limit(self, schemas: Optional[list[dict[str, Any]]] = None) -> int:
        schemas = schemas if schemas is not None else self.registry.schemas()
        limit = self.settings.context_limit
        gen_reserve = int(getattr(self.settings, "max_tokens", 0) or 0)
        if self.is_subagent and gen_reserve <= 0:
            gen_reserve = 2048
        return max(4000, limit - schemas_tokens(schemas) - 256 - gen_reserve)

    def _sync_read_coverage(self) -> None:
        """Drop read dedupe for lines whose bytes left the window."""
        try:
            self.guard.reconcile_read_coverage(self.messages)
        except Exception:
            pass

    def _archive_dropped(self, meta: dict[str, Any]) -> None:
        """Keep whole messages that compaction removed, for display/persistence."""
        dropped = meta.get("dropped_messages") or []
        if dropped:
            self._archived_messages.extend(dropped)
            # Bytes left the window — dedup must not tell the model that an
            # earlier read is "already in context" when it may be gone.
            self._sync_read_coverage()
        if dropped or meta.get("compressed"):
            self._write_recoverable_history()

    def _write_recoverable_history(self) -> None:
        """Cursor-style: compacted history lives as a workspace file the agent can search."""
        try:
            ws = Path(self.settings.workspace)
            out_dir = ws / ".sidekick" / "context"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "history.md"
            lines = [
                "# Session history (recover details after compaction)",
                "",
                "This file is **not** in the live model window. If the compaction summary",
                "omitted something, `search_text` or `read_file` this path, then re-read",
                "the original workspace files — do not guess.",
                "",
            ]
            for m in self.full_transcript():
                role = m.get("role")
                if role == "system":
                    continue
                text = str(m.get("content") or "").replace("\r\n", "\n")
                if role == "tool":
                    name = str(m.get("name") or "tool")
                    if len(text) > 500:
                        text = text[:500] + "\n…"
                    lines.append(f"### tool {name}")
                    lines.append(text)
                    lines.append("")
                elif role == "user":
                    if text.startswith(COMPACTION_MARK):
                        continue
                    clip = text if len(text) <= 2000 else text[:2000] + "…"
                    lines.append(f"### user")
                    lines.append(clip)
                    lines.append("")
                elif role == "assistant":
                    clip = text if len(text) <= 1500 else text[:1500] + "…"
                    if clip.strip():
                        lines.append("### assistant")
                        lines.append(clip)
                        lines.append("")
            path.write_text("\n".join(lines), encoding="utf-8")
            rel = ".sidekick/context/history.md"
            for m in self.messages:
                if m.get("role") == "user" and str(m.get("content") or "").startswith(COMPACTION_MARK):
                    body = str(m.get("content") or "")
                    if rel not in body:
                        m["content"] = (
                            body.rstrip()
                            + f"\n\n## Recover\nEarlier turns: `{rel}` — "
                            "search_text/read_file if this summary omitted a detail."
                        )
                    break
        except Exception as exc:  # noqa: BLE001
            from ..core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), "write recoverable history failed", exc)

    def full_transcript(self) -> list[dict[str, Any]]:
        """Complete chronological history for display/persistence.

        ``self.messages`` is the live LLM working set and may have been
        compacted (old turns folded into a synthetic summary). This stitches
        the archived originals back in and drops the summary stub, so the UI
        and disk history never lose turns to context compression.
        """
        if not self.messages:
            return list(self._archived_messages)
        system = self.messages[0] if self.messages[0].get("role") == "system" else None
        body = self.messages[1:] if system else self.messages
        display_body = [
            m for m in body if not str(m.get("content") or "").startswith(COMPACTION_MARK)
        ]
        out: list[dict[str, Any]] = []
        if system:
            out.append(system)
        out.extend(self._archived_messages)
        out.extend(display_body)
        from .llm import align_tool_chains

        # Archive + live can contain the same tool call twice after a compact
        # merge. Persisting both makes the next request 400.
        return align_tool_chains(out)

    def _ensure_mids(self) -> None:
        """Stamp messages so a later compact can tell copies from new turns."""
        seq = int(getattr(self, "_mid_seq", 0) or 0)
        for bucket in (getattr(self, "_archived_messages", None) or [], self.messages):
            for m in bucket:
                if not isinstance(m, dict):
                    continue
                mid = m.get("_mid")
                if not isinstance(mid, int) or mid <= 0:
                    seq += 1
                    m["_mid"] = seq
                elif mid > seq:
                    seq = mid
        self._mid_seq = seq

    def _apply_ready_compress(self) -> None:
        """Swap in a finished background compaction without blocking the loop."""
        job = self._compress_job
        if not job:
            return
        done: threading.Event = job["done"]
        if not done.is_set():
            return
        self._compress_job = None
        rebuilt, meta = job["box"].get("ok") or (None, {})
        if not rebuilt:
            return
        # Match on a stable id, not the Python object id. Compaction copies
        # messages; treating those copies as new appended the same tool
        # results a second time and the provider rejected the chain.
        self._ensure_mids()
        snap_mids = {mid for mid in (job.get("snap_mids") or []) if isinstance(mid, int)}
        have = {
            m.get("_mid")
            for m in rebuilt
            if isinstance(m, dict) and isinstance(m.get("_mid"), int)
        }
        extras = [
            m
            for m in self.messages
            if not (
                isinstance(m, dict)
                and isinstance(m.get("_mid"), int)
                and (m.get("_mid") in snap_mids or m.get("_mid") in have)
            )
        ]
        self.messages = list(rebuilt) + extras
        self._archive_dropped(meta)
        if meta.get("compressed"):
            self._last_compressed = True
            self._after_compress_continue()
        after = context_budget_tokens(self.messages, self.registry.schemas())
        self._emit(
            "compress",
            {
                "before": job.get("before") or 0,
                "after": after,
                "limit": self.settings.context_limit,
                "meta": meta,
                "phase": "done",
                "background": True,
                "blocking": False,
                "message": f"上下文已在后台整理 {job.get('before') or 0}→{after}",
            },
        )

    def _run_ensure_fit(self, snapshot: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return ensure_fit(
            snapshot,
            context_limit=self._compress_msg_limit(),
            keep_recent_tokens=self.settings.keep_recent_tokens,
            trigger_ratio=self.settings.compress_trigger_ratio,
            max_attempts=1,
            llm=self.compress_llm,
        )

    def _start_background_compress(self, before: int) -> None:
        if self._compress_job and not self._compress_job["done"].is_set():
            return
        self._ensure_mids()
        snapshot = copy.deepcopy(self.messages)
        snap_mids = [m.get("_mid") for m in self.messages if isinstance(m, dict)]
        box: dict[str, Any] = {}
        done = threading.Event()
        job = {"done": done, "box": box, "snap_mids": snap_mids, "before": before}
        self._compress_job = job

        def work() -> None:
            try:
                box["ok"] = self._run_ensure_fit(snapshot)
            except Exception as exc:  # noqa: BLE001
                box["ok"] = (None, {"error": str(exc)})
            finally:
                done.set()

        threading.Thread(target=work, name="sidekick-compress", daemon=True).start()
        self._emit(
            "compress_start",
            {
                "before": before,
                "limit": self.settings.context_limit,
                "attempt": 1,
                "max_attempts": 1,
                "phase": "background",
                "background": True,
                "blocking": False,
                "message": "后台整理上下文（对话继续）…",
            },
        )

    def _emit_context_usage(self, schemas: list[dict[str, Any]] | None = None) -> int:
        """Publish the *current* window usage; returns the estimate."""
        schemas = schemas if schemas is not None else self.registry.schemas()
        tokens = context_budget_tokens(self.messages, schemas)
        limit = self.settings.context_limit
        try:
            from ..core.config import infer_context_limit as _infer_window
            window_limit = int(_infer_window(getattr(self.settings, "model", "") or "") or 0)
        except Exception:
            window_limit = 0
        self._emit(
            "context_usage",
            {
                "tokens": tokens,
                "limit": limit,
                "window_limit": window_limit or None,
                "ratio": round(tokens / max(1, limit), 4),
                "messages_tokens": messages_tokens(self.messages),
                "schemas_tokens": schemas_tokens(schemas),
            },
        )
        return tokens

    def _squeeze_tool_batch(self, start: int) -> None:
        """Keep a fresh batch of tool results from overflowing the window.

        The LLM-backed compressor only runs at the top of the next iteration;
        without this, one parallel batch of large reads could report
        "77k / 64k" to the UI and 400 on the provider before anything reacts.
        """
        schemas = self.registry.schemas()
        limit = self.settings.context_limit
        # Leave room for the model's reply and the schemas we send every turn.
        gen_reserve = int(getattr(self.settings, "max_tokens", 0) or 0) or 2048
        target = int(limit * 0.85) - schemas_tokens(schemas) - 256 - gen_reserve
        target = max(2000, target)
        if messages_tokens(self.messages) <= target:
            return
        changed = False
        # Old history is what overflows a restored chat. Parking it keeps the
        # reads from this batch intact — trimming those reads is what makes
        # the model fetch the same lines again.
        if 0 < start < len(self.messages):
            fresh = self.messages[start:]
            prefix_budget = max(3000, target // 2)
            keep = int(self.settings.keep_recent_tokens or 0) or prefix_budget
            # Keep current-turn mutations visible: never silently archive them.
            turn_start = current_turn_start(self.messages)
            live, archived = park_overflow(
                self.messages[:start],
                context_limit=prefix_budget,
                keep_recent_tokens=min(keep, prefix_budget),
                protect_from=turn_start if turn_start < start else None,
            )
            if archived or messages_tokens(live) < messages_tokens(self.messages[:start]):
                if archived:
                    self._archived_messages.extend(archived)
                self.messages = live + fresh
                start = len(live)
                changed = True
        squeezed, did = squeeze_fresh_tool_results(
            self.messages, start, target_tokens=target
        )
        if did:
            self.messages = squeezed
            changed = True
        if changed:
            self._sync_read_coverage()
            self._last_compressed = True
            self._emit_context_usage(schemas)


    def _after_compress_continue(self) -> None:
        """Keep the agent loop on the same goal after context compaction.

        Compaction must never look like a stop signal. Repair dangling
        tool_calls (providers 400 otherwise → empty UI round) and inject an
        internal continue nudge so the model does not apologize about
        truncation and exit.
        """
        self.messages = repair_incomplete_tool_chains(self.messages)
        coalesce_internal_nudge(
            self.messages,
            kind="post_compress",
            content=_POST_COMPRESS_CONTINUE_NUDGE,
            role="system",
        )

    def _maybe_compress(self) -> None:
        schemas = self.registry.schemas()
        self._apply_ready_compress()
        before = context_budget_tokens(self.messages, schemas)
        limit = self.settings.context_limit
        trigger = int(limit * self.settings.compress_trigger_ratio)
        # Instant shrink of PREVIOUS turns' fat tool dumps — never waits on an
        # LLM, and never touches the active turn (clearing a read_file result
        # the agent is still using forces it into a re-read loop).
        if before >= int(limit * 0.55):
            compacted, cheap_did = cheap_compact(
                self.messages, protect_from=current_turn_start(self.messages)
            )
            if cheap_did:
                self.messages = compacted
                self._sync_read_coverage()
                self._last_compressed = True
                before = context_budget_tokens(self.messages, schemas)
        if before < trigger:
            self._emit_context_usage(schemas)
            return

        emergency = before >= int(limit * 0.90)
        if not emergency:
            self._emit_context_usage(schemas)
            self._start_background_compress(before)
            return

        # Over the hard line: one sync pass so the next provider call cannot 400.
        job = self._compress_job
        if job and not job["done"].is_set():
            job["done"].wait(timeout=8.0)
            self._apply_ready_compress()
            before = context_budget_tokens(self.messages, schemas)
            if before < trigger:
                self._emit_context_usage(schemas)
                return
        self._compress_job = None

        self._emit(
            "compress_start",
            {
                "before": before,
                "limit": limit,
                "attempt": 1,
                "max_attempts": 1,
                "phase": "emergency",
                "blocking": True,
                "message": "上下文接近上限，正在快速整理…",
            },
        )
        self.messages, meta = self._run_ensure_fit(self.messages)
        self.messages = repair_incomplete_tool_chains(self.messages)
        self._archive_dropped(meta)
        after = context_budget_tokens(self.messages, schemas)
        if meta.get("compressed"):
            self._last_compressed = True
            self._after_compress_continue()
        self._emit(
            "compress",
            {
                "before": before,
                "after": after,
                "limit": limit,
                "meta": meta,
                "phase": "done",
                "blocking": True,
                "message": f"上下文已整理 {before}→{after}",
            },
        )
        self._emit(
            "context_usage",
            {
                "tokens": after,
                "limit": limit,
                "ratio": round(after / max(1, limit), 4),
                "messages_tokens": messages_tokens(self.messages),
                "schemas_tokens": schemas_tokens(schemas),
            },
        )

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
        goal = active_obligation(self.messages)
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


    def _run_agent_loop(
        self,
        max_iters: int,
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        emit_assistant_text: bool = True,
        emit_reasoning: Optional[bool] = None,
        hold_for_goal: bool = True,
    ) -> tuple[str, int, bool, bool]:
        """Run tool calls, then ask the same model again with the results.

        That next reply decides whether the user's request is met. No tool
        call ends the turn. Workbench and social execution share this loop.

        ``emit_reasoning`` defaults to ``emit_assistant_text``. Plan prep sets
        text off / reasoning on so the user still sees thinking before confirm.
        """
        show_reasoning = (
            emit_assistant_text if emit_reasoning is None else bool(emit_reasoning)
        )
        compressed = False
        final = ""
        turned = 0
        was_cancelled = False
        stall_nudges = 0
        delegate_nudges = 0
        file_fail_nudges = 0
        browser_nav_fail_nudges = 0
        goal_nudges = 0
        tool_intent_nudges = 0
        force_tool_choice: Any | None = None
        tool_names: list[str] = []
        cap = int(max_iters or 0)
        i = 0
        exhausted = False
        loop_start = len(self.messages)
        ledger = _evidence_ledger(self)
        evidence_start = ledger.mark()
        # Evidence count at the last forced continuation. A round that adds no
        # tool observation cannot make the next completion different.
        self._goal_evidence_count = evidence_start
        self._goal_satisfied = None
        self._text_loop_cuts = 0
        self._browser_same_runs = 0
        self._browser_fail_streak = 0
        self._incomplete_write_streak = 0
        self._empty_assistant_recoveries = 0
        # Only consecutive repeats that returned the same result escalate.
        repeat_tracker = ToolRepeatTracker()
        # Full raw thinking of the latest round that produced any. History only
        # stores the new part of each round, so comparing against history alone
        # would forget the shared opening and let it reappear next round. Plan
        # steps are separate loops, so the last shown thought is carried over
        # as well — reopening the same status sentence per step is the same
        # repetition as reopening it per tool round.
        full_previous_reasoning = (
            getattr(self, "_last_shown_reasoning", "")
            or self._previous_round_reasoning(loop_start)
        )

        while True:
            i += 1
            if cap > 0 and i > cap:
                exhausted = True
                break
            if self.cancelled():
                was_cancelled = True
                break
            turned = i
            self._maybe_compress()
            if getattr(self, "_last_compressed", False):
                compressed = True
            if getattr(self, "_classify", False):
                # Message classification is one JSON reply. Tools here make the
                # model wander (list files, keep going) and the UI waits forever.
                schemas = []
            elif getattr(self, "_disable_tools", False):
                # disable_tools is a capability DOWNGRADE, not silence: the
                # 会话版 decision pass still grounds its statements with
                # observation-only tools (read/search/browse), while every
                # mutating / shell / delegate / ask tool stays excluded.
                schemas = filter_readonly_schemas(self.registry.schemas(), self.registry.readonly_names())
            else:
                schemas = tools if tools is not None else self.registry.schemas()
            if getattr(self, "_block_delegate", False):
                schemas = drop_delegate_schemas(schemas)
            if (
                getattr(self, "_requires_initial_delegation", False)
                and not getattr(self, "_delegation_started", False)
                and tools is None
                and not getattr(self, "_disable_tools", False)
                and not getattr(self, "_block_delegate", False)
            ):
                schemas = [
                    schema
                    for schema in schemas
                    if str((schema.get("function") or {}).get("name") or "")
                    in {"delegate_task", "delegate_dialogue"}
                ]
            self._emit(
                "llm_start",
                {
                    "turn": i,
                    "budget": json.loads(debug_dump_budget(self.messages)),
                    "tokens": context_budget_tokens(self.messages, schemas),
                    "messages_tokens": messages_tokens(self.messages),
                    "schemas_tokens": schemas_tokens(schemas),
                    "limit": self.settings.context_limit,
                },
            )
            assistant: Optional[dict[str, Any]] = None
            streamed_buf = ""
            # 暂存可见文本末尾的冒号：它可能只是“准备调用工具：”的引导符。
            lead_in_tail = ""
            duplicate_intent_cuts = 0
            stalled = False
            reasoning_streamed_live = False
            raw_round_reasoning = ""
            # Reasoning models restate the previous round's status line at the
            # start of every tool round. Hold the thought until it says
            # something new; a pure restatement is dropped and a shared opening
            # is trimmed, so neither live view nor history repeats it.
            previous_reasoning = full_previous_reasoning
            reasoning_gate = ReasoningRoundGate(previous_reasoning)
            if emit_assistant_text:
                self._emit("assistant_delta", {"chunk": "", "reset": True})
            if show_reasoning:
                # A new model round is a new message. The UI seals the previous
                # bubble on reset, then this clears live reasoning for the round.
                self._emit("assistant_reasoning_delta", {"chunk": "", "reset": True})
            try:
                for kind, payload in self.llm.stream_chat(
                    self._model_messages(),
                    tools=schemas,
                    cancel_check=self.cancelled,
                    tool_choice=force_tool_choice,
                ):
                    if self.cancelled():
                        was_cancelled = True
                        break
                    if kind == "stalled":
                        stalled = True
                        reason = ""
                        if isinstance(payload, dict):
                            reason = str(payload.get("reason") or "")
                        self._emit(
                            "assistant_status",
                            {
                                "text": "思考卡住，已自动中断并继续",
                                "stalled": True,
                                "reason": reason,
                            },
                        )
                    elif kind == "delta":
                        piece = str(payload)
                        streamed_buf += piece
                        if emit_assistant_text:
                            # 挂起的冒号只有确认后面还有正文时才重新上屏；
                            # 若下一段是工具调用或流结束，就直接丢弃。
                            if lead_in_tail:
                                piece = lead_in_tail + piece
                                lead_in_tail = ""
                            piece, tail = split_dangling_colon(piece)
                            if tail:
                                lead_in_tail = tail
                            # A provider that flushes the whole reply as one
                            # chunk would otherwise appear only after the tool.
                            if piece:
                                if len(piece) > 48:
                                    self._emit_paced_text(piece, reset=False)
                                else:
                                    self._emit("assistant_delta", {"chunk": piece})
                    elif kind == "reasoning_delta":
                        if show_reasoning:
                            reasoning_streamed_live = True
                            fresh = reasoning_gate.observe(str(payload))
                            if fresh:
                                self._emit(
                                    "assistant_reasoning_delta",
                                    {"chunk": fresh},
                                )
                    elif kind == "tool_delta" and isinstance(payload, dict):
                        self._emit("tool_call_delta", payload)
                    elif kind == "done":
                        assistant = payload  # type: ignore[assignment]
            except Exception:
                if self.cancelled():
                    was_cancelled = True
                    if streamed_buf.strip() and not final:
                        final = strip_dangling_colon(streamed_buf.strip())
                        self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
                    break
                if stalled:
                    if assistant is None and streamed_buf.strip():
                        assistant = {"role": "assistant", "content": streamed_buf.strip()}
                else:
                    assistant = self.llm.chat(
                        self._model_messages(),
                        tools=schemas,
                        tool_choice=force_tool_choice,
                        fold_repeats=True,
                    )
                    raw_round_reasoning = str(assistant.get("reasoning") or "").strip()
                    # The fallback bypasses the live gate, so dedupe before
                    # painting; otherwise the thinking bubble shows a restated
                    # round that the streaming path would have dropped.
                    fallback_reasoning = dedupe_reasoning_round(
                        previous_reasoning, str(assistant.get("reasoning") or "")
                    )
                    if fallback_reasoning:
                        assistant["reasoning"] = fallback_reasoning
                    else:
                        assistant.pop("reasoning", None)
                    if emit_assistant_text:
                        self._stream_text_to_ui(
                            strip_dangling_colon(str(assistant.get("content") or "")),
                            reasoning=fallback_reasoning if show_reasoning else "",
                        )
                    elif show_reasoning and fallback_reasoning:
                        self._emit(
                            "assistant_reasoning_delta",
                            {"chunk": "", "reset": True},
                        )
                        self._emit(
                            "assistant_reasoning_delta",
                            {"chunk": fallback_reasoning},
                        )

            if isinstance(assistant, dict):
                raw_round_reasoning = (
                    raw_round_reasoning
                    or str(assistant.get("reasoning") or "").strip()
                )
                if raw_round_reasoning:
                    # Compare the next round against the whole thought, not the
                    # trimmed copy that history keeps.
                    full_previous_reasoning = raw_round_reasoning
                    if show_reasoning:
                        # Only shown thinking is a baseline for later dedupe.
                        self._last_shown_reasoning = raw_round_reasoning

            if isinstance(assistant, dict) and assistant.get("reasoning"):
                # One storage boundary for thinking too: keep only the part
                # that the previous round did not already say.
                kept_reasoning = dedupe_reasoning_round(
                    previous_reasoning, str(assistant.get("reasoning") or "")
                )
                if kept_reasoning:
                    assistant["reasoning"] = kept_reasoning
                else:
                    assistant.pop("reasoning", None)

            if lead_in_tail:
                # 流结束时仍挂起冒号：说明它就是工具调用的引导符，丢掉；
                # 同时清理已落库的助手正文，保证历史里也不以“：”结尾。
                if isinstance(assistant, dict):
                    assistant["content"] = strip_dangling_colon(
                        str(assistant.get("content") or "")
                    )
                streamed_buf = strip_dangling_colon(streamed_buf)
                lead_in_tail = ""

            # Forced tool_choice applies to exactly one model round.
            force_tool_choice = None

            if was_cancelled:
                if not final:
                    partial = ""
                    if isinstance(assistant, dict):
                        partial = str(assistant.get("content") or "").strip()
                    if not partial:
                        partial = streamed_buf.strip()
                    if partial:
                        final = partial
                        self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
                break
            if assistant is None:
                if stalled and not self.cancelled():
                    stall_nudges += 1
                    self.messages.append(
                        {"role": "assistant", "content": streamed_buf.strip(), "ts": time.time()}
                    )
                    if stall_nudges <= 2:
                        coalesce_internal_nudge(
                            self.messages,
                            kind="stall",
                            content=_STALL_NUDGE,
                            role="system",
                        )
                        continue
                    final = strip_dangling_colon(streamed_buf.strip()) or "（思考超时，已停止本轮）"
                    break
                if self.cancelled():
                    was_cancelled = True
                    if streamed_buf.strip() and not final:
                        final = strip_dangling_colon(streamed_buf.strip())
                        self.messages.append(
                            {"role": "assistant", "content": final, "ts": time.time()}
                        )
                    break
                # Provider returned nothing (common after a huge browser_* dump or
                # a mid-turn compress that left a bad tool chain). This is NOT a
                # user cancel — repair and continue the same goal once/twice.
                recoveries = int(getattr(self, "_empty_assistant_recoveries", 0) or 0)
                if recoveries < 2:
                    self._empty_assistant_recoveries = recoveries + 1
                    self.messages = repair_incomplete_tool_chains(self.messages)
                    coalesce_internal_nudge(
                        self.messages,
                        kind="empty_assistant_recover",
                        content=_EMPTY_ASSISTANT_RECOVER_NUDGE,
                        role="system",
                    )
                    self._emit(
                        "assistant_status",
                        {"text": "模型未返回内容，正在继续同一任务…", "tools": []},
                    )
                    continue
                final = strip_dangling_colon(streamed_buf.strip()) or (
                    "（模型连续两轮未返回内容，已停止本轮。请重试或缩小上下文。）"
                )
                self.messages.append(
                    {"role": "assistant", "content": final, "ts": time.time()}
                )
                if emit_assistant_text:
                    self._emit_paced_text(final)
                break
            assistant.setdefault("ts", time.time())
            self.messages.append(assistant)

            # One storage boundary for visible text. A trailing colon is the
            # lead-in of a tool call ("先检查一下：") that the stream already
            # withholds; the non-stream fallback and aggregated responses can
            # still carry it into history. Normalize here so the tool loop, the
            # final-answer path, reloads, and the UI all see the same text.
            raw_visible = str(assistant.get("content") or "")
            clean_visible = strip_dangling_colon(raw_visible)
            if clean_visible != raw_visible:
                assistant["content"] = clean_visible
                self.messages[-1]["content"] = clean_visible

            # Past cancel seals used to splice a stop instruction into assistant
            # content; models then imitate it mid-stream. Scrub so the bubble and
            # the next prompt never treat that control text as model speech.
            from .agent_history import strip_cancel_control

            _raw_asst = str(assistant.get("content") or "")
            _clean_asst = strip_cancel_control(_raw_asst)
            if _clean_asst != _raw_asst:
                assistant["content"] = _clean_asst
                self.messages[-1]["content"] = _clean_asst
                if emit_assistant_text:
                    self._emit("assistant_delta", {"chunk": "", "reset": True})
                    if _clean_asst:
                        self._emit_paced_text(_clean_asst, reset=False)

            # Some providers only return reasoning_content on the AGGREGATED
            # response (never as incremental deltas) when the turn also
            # includes tool_calls — common for reasoning models doing
            # function calling. Without this, the "thinking" bubble is
            # correctly saved to history (so it shows up on reload) but
            # never appears live before the tool card, since no
            # reasoning_delta event ever reached the UI.
            if show_reasoning and not reasoning_streamed_live:
                backfill_reasoning = str(assistant.get("reasoning") or "").strip()
                if backfill_reasoning:
                    self._emit("assistant_reasoning_delta", {"chunk": backfill_reasoning})

            tool_calls = assistant.get("tool_calls") or []
            preamble = (assistant.get("content") or "").strip()
            reasoning_text = str(assistant.get("reasoning") or "")
            if assistant.get("sidekick_looped"):
                # The provider stream was cut after similar text kept going
                # for a while. That is not a finished goal: clip the draft and
                # ask for the next tool call.
                self.messages[-1]["tool_calls"] = []
                tool_calls = []
                # Collapse tape-recorder tails, then replace the live bubble
                # (reset alone left an empty thinking panel).
                cleaned_reason = fold_reasoning_text(reasoning_text)
                if len(cleaned_reason) > 480:
                    cleaned_reason = cleaned_reason[:480].rstrip() + "…"
                self.messages[-1]["reasoning"] = cleaned_reason
                reasoning_text = cleaned_reason
                if show_reasoning:
                    self._emit(
                        "assistant_reasoning_delta",
                        {"chunk": "", "reset": True},
                    )
                    if cleaned_reason:
                        self._emit(
                            "assistant_reasoning_delta",
                            {"chunk": cleaned_reason},
                        )
                kept = preamble[:480].rstrip() if preamble else ""
                preamble = kept
                self.messages[-1]["content"] = kept
                if emit_assistant_text:
                    self._emit("assistant_delta", {"chunk": "", "reset": True})
                    if kept:
                        self._emit("assistant_delta", {"chunk": kept})
                self._text_loop_cuts = int(getattr(self, "_text_loop_cuts", 0) or 0) + 1
                if not kept and self._text_loop_cuts <= 1:
                    # 草稿被重复检测清空时，最多再问一次；有可用草稿则直接收尾。
                    coalesce_internal_nudge(
                        self.messages,
                        kind="text_loop_cut",
                        content=(
                            "Draft repeated and was cut. Do not repeat that text. "
                            "Call the next tool, or answer if the goal is done."
                        ),
                        role="system",
                    )
                    continue
                final = kept or "（模型输出出现重复，已停止本轮。请重试或缩小上下文。）"
                if not kept:
                    self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                break

            # Repeat watchdog. A repeat is only a loop when the same call keeps
            # returning the same observation. Re-running a test after an edit,
            # polling a job, or observing a page that changed must proceed.
            if tool_calls:
                cur_sig = self._tool_call_sig_set(assistant)
                incomplete = self._assistant_tool_args_incomplete(assistant)
                if incomplete:
                    # Truncated write/edit JSON collapses to path-only signatures;
                    # allow retries instead of treating them as a stuck loop.
                    self._incomplete_write_streak += 1
                    self._browser_same_runs = 0
                    repeat = repeat_tracker.observe(None)
                else:
                    self._incomplete_write_streak = 0
                    repeat = repeat_tracker.observe(
                        cur_sig, result_history(self.messages)
                    )
            else:
                self._incomplete_write_streak = 0
                self._browser_same_runs = 0
                repeat = repeat_tracker.observe(None)

            if self._incomplete_write_streak >= 4:
                final = preamble or (
                    "写入内容多次被截断，无法完整落盘。请把文件拆成多个较小的 write_file 再继续。"
                )
                self.messages[-1]["content"] = final
                self.messages[-1]["tool_calls"] = []
                if emit_assistant_text:
                    self._emit("assistant_delta", {"chunk": "", "reset": True})
                    self._emit("assistant_delta", {"chunk": final})
                break

            if repeat.repeated and self._live_browser_repeat(assistant):
                # The page is live: the same look or the same control can be used
                # again. A changed observation (scrolling reveals more, a click
                # moves the page) resets the streak; only identical results
                # count toward the stop. Don't mention a security check unless
                # one showed up.
                if repeat.result_changed:
                    self._browser_same_runs = 0
                else:
                    self._browser_same_runs = (
                        int(getattr(self, "_browser_same_runs", 0) or 0) + 1
                    )
                if self._browser_same_runs >= 5:
                    prior = self._prior_results_for_tool_calls(assistant)
                    final = "同一个浏览器操作已经重复多次，页面没有继续变化，先停在这里。"
                    if self._prior_needs_human(prior):
                        final += "若页面出现安全验证，请在浏览器里完成后告诉我继续。"
                    self.messages[-1]["content"] = final
                    self.messages[-1]["tool_calls"] = []
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                    break
            elif repeat.repeated and repeat.should_run:
                # Same command, different observation (or the first repeat,
                # where we have too little history to call it a loop). Let the
                # call run; a poll or a re-run after an edit is real work.
                self._browser_same_runs = 0
            elif repeat.repeated:
                # Context recovery — do not hard-stop on the first repeat.
                # Put the prior tool substance back in front of the model so it
                # can advance instead of re-firing identical args.
                prior = self._prior_results_for_tool_calls(assistant)
                self.messages[-1]["tool_calls"] = []
                if repeat.action == TOOL_REPEAT_STOP:
                    # The prior observation stays in history for the model.
                    # Ask it for a status instead of splicing a canned stop;
                    # with tools removed it cannot re-fire the same call.
                    self.messages[-1]["content"] = (preamble or "").strip()
                    if emit_assistant_text:
                        coalesce_internal_nudge(
                            self.messages,
                            kind="tool_repeat_stop",
                            content=(
                                "That operation keeps returning the same result. "
                                "Do not call it again. Report what you found, what "
                                "it means for the user's goal, and what still blocks it."
                            ),
                        )
                        final = self._stream_final_reply()
                    else:
                        final = (preamble + "\n\n") if preamble else ""
                        final += "同一操作连续返回相同结果，没有新进展，先停在这里。"
                        if self._prior_needs_human(prior):
                            final += "若页面出现安全验证，请在浏览器里完成后告诉我继续。"
                        self.messages[-1]["content"] = final
                    break
                coalesce_internal_nudge(
                    self.messages,
                    kind="tool_repeat",
                    content=(
                        "That call repeated the previous one; its result is already above. "
                        "Continue from that result instead of calling it again.\n\n"
                        + (prior or "（上一次的结果已不在窗口里，请基于已有结论继续。）")
                    ),
                    role="system",
                )
                continue
            else:
                # Any non-repeated step breaks a browser repetition streak too.
                self._browser_same_runs = 0

            # 重复文本只收尾一次：删除重复副本，让本轮自然结束。
            if self._collapse_repeated_assistant(assistant):
                preamble = (assistant.get("content") or "").strip()
                tool_calls = assistant.get("tool_calls") or []
                if not tool_calls and not preamble:
                    previous = ""
                    for _m in reversed(self.messages[max(0, loop_start) : -1]):
                        if _m.get("role") == "assistant":
                            previous = str(_m.get("content") or "").strip()
                            break
                    if looks_like_tool_intent_only(previous) and duplicate_intent_cuts < 1:
                        # 复读的是“准备调用工具”的引导句：保留一次机会，
                        # 下一轮会以 required 真正发起调用。
                        duplicate_intent_cuts += 1
                        continue
                    # 复读的是已经给出的最终答复：删除重复副本并收尾，
                    # 不再追加“请继续”去逼模型再写一遍。
                    self.messages.pop()
                    break

            if not tool_calls:
                if (
                    getattr(self, "_requires_initial_delegation", False)
                    and not getattr(self, "_delegation_started", False)
                    and delegate_nudges < 2
                ):
                    delegate_nudges += 1
                    self.messages.append(
                        {
                            "role": "user",
                            "sidekick_internal": True,
                            "content": (
                                "Use a native delegation tool now. Create exactly one batch call "
                                "for the requested workers; do not merely describe the call."
                            ),
                        }
                    )
                    continue
                parsed_ask = (
                    None
                    if self.is_subagent and not self.full_agent
                    else try_parse_inline_ask(preamble)
                )
                if parsed_ask:
                    self.messages.pop()
                    self._emit("assistant_delta", {"chunk": "", "reset": True, "discard": True})
                    answer = self._ask_user(
                        question=str(parsed_ask.get("question") or ""),
                        options=list(parsed_ask.get("options") or []),
                        allow_custom=bool(parsed_ask.get("allow_custom", True)),
                    )
                    self.messages.append({"role": "user", "content": answer, "ts": time.time()})
                    continue

                if stalled:
                    stall_nudges += 1
                    if stall_nudges <= 2:
                        coalesce_internal_nudge(
                            self.messages,
                            kind="stall",
                            content=_STALL_NUDGE,
                            role="system",
                        )
                        continue
                    final = preamble or "（思考超时，已停止本轮）"
                    if not preamble:
                        self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                    break

                # Fail closed when prose needs plugin/MCP tools absent from
                # this turn's schema — do not fake a completed goal.
                had_tool_results = any(
                    msg.get("role") == "tool"
                    for msg in self.messages[max(0, loop_start) :]
                )
                schema_names = [
                    str((schema.get("function") or {}).get("name") or "")
                    for schema in (schemas or [])
                ]
                schema_names = [n for n in schema_names if n]
                probe = intent_probe_text(preamble, reasoning_text)
                # 完成度判定优先看用户可见的最终回复；只有回复为空时才回退到
                # reasoning，避免把思考里的“下一步计划”误判成未完成。
                goal_candidate = preamble or intent_probe_text("", reasoning_text)
                plugin_summaries = None
                try:
                    from ..plugins.tools import plugin_tool_summary

                    bound = getattr(self.settings, "plugin_bound_ids", None) or None
                    if isinstance(bound, (list, tuple)) and not any(
                        str(x).strip() for x in bound
                    ):
                        bound = None
                    plugin_summaries = plugin_tool_summary(plugin_ids=bound)
                except Exception:
                    plugin_summaries = None

                unavailable = tool_intent_unavailable_message(
                    probe,
                    known_tool_names=schema_names,
                    plugin_summaries=plugin_summaries,
                )
                intent_only = looks_like_tool_intent_only(probe) or bool(
                    resolve_mentioned_tools(
                        probe,
                        schema_names,
                        known_plugin_ids=[
                            str(p.get("id") or "").strip()
                            for p in (plugin_summaries or [])
                            if isinstance(p, dict)
                            and str(p.get("id") or "").strip()
                        ],
                    )
                    or extract_named_tool_ids(probe)
                )
                if (
                    unavailable
                    and not had_tool_results
                    and intent_only
                ):
                    final = unavailable
                    self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                    self._emit(
                        "assistant_status",
                        {"text": "工具不可用", "tools": []},
                    )
                    self._goal_satisfied = False
                    break

                # Primary gate: the Think-model goal verdict owns stop vs
                # continue. continue → another ReAct Think round. Tool-intent
                # heuristics never reopen the turn by themselves; they only
                # help the next Think emit native tool_calls when the judge
                # already said the goal is unfinished.
                continue_goal, goal_nudges = self._reject_early_stop(
                    goal_candidate,
                    loop_start=loop_start,
                    hold_for_goal=hold_for_goal,
                    goal_nudges=goal_nudges,
                    emit_assistant_text=emit_assistant_text,
                    evidence_start=evidence_start,
                )
                if continue_goal:
                    if (
                        bool(schemas)
                        and not had_tool_results
                        and should_continue_for_tool_intent(
                            probe,
                            tools_available=bool(schemas),
                            had_tool_results=had_tool_results,
                            nudges=tool_intent_nudges,
                            known_tool_names=schema_names,
                            plugin_summaries=plugin_summaries,
                        )
                    ):
                        try:
                            from .tools.extension_budget import (
                                pin_mentioned_extension_schemas,
                            )

                            plugin_ids_for_alias = [
                                str(p.get("id") or "").strip()
                                for p in (plugin_summaries or [])
                                if isinstance(p, dict)
                                and str(p.get("id") or "").strip()
                            ]
                            pin_src = "\n".join(
                                x
                                for x in (
                                    str(
                                        getattr(self, "_budget_mention_text", "")
                                        or ""
                                    ),
                                    probe,
                                )
                                if x
                            )
                            # Leaf aliases (generate_dockerfile → plugin_*) so
                            # schema budget can surface the real tool id.
                            for named in resolve_mentioned_tools(
                                pin_src,
                                schema_names,
                                known_plugin_ids=plugin_ids_for_alias,
                            ):
                                pin_src = f"{pin_src}\n{named}"
                            pinned_now = pin_mentioned_extension_schemas(
                                self.registry,
                                pin_src,
                                plugin_summaries=plugin_summaries,
                            )
                            if pinned_now:
                                schemas = (
                                    filter_readonly_schemas(
                                        self.registry.schemas(),
                                        self.registry.readonly_names(),
                                    )
                                    if getattr(self, "_disable_tools", False)
                                    else self.registry.schemas()
                                )
                                if getattr(self, "_block_delegate", False):
                                    schemas = drop_delegate_schemas(schemas)
                        except Exception:
                            pass
                        tool_intent_nudges += 1
                        if tool_intent_nudges == 1:
                            force_tool_choice = "required"
                        else:
                            force_tool_choice = None
                    continue

                # 模型把“准备调用工具：”写成了最终答复时，不能把冒号原样上屏。
                final = strip_dangling_colon(preamble)
                if final != str(self.messages[-1].get("content") or ""):
                    self.messages[-1]["content"] = final
                if not final:
                    # History keeps only the new part of a round; a final
                    # answer built from thinking still needs the whole thought.
                    reasoning = (
                        raw_round_reasoning
                        or str(assistant.get("reasoning") or "").strip()
                    )
                    if reasoning and not streaming_text_looped(reasoning):
                        final = (
                            reasoning
                            if len(reasoning) <= 3000
                            else reasoning[:3000] + "…"
                        )
                    elif getattr(self, "_goal_satisfied", None) is False:
                        final = "目标尚未完成，但继续执行没有带来新的结果。"
                    else:
                        final = "（本轮已完成）"
                    final = strip_dangling_colon(final)
                    self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit_paced_text(final)
                break

            ready_calls, dropped_calls = drop_unready_tool_calls(self.registry, tool_calls)
            if dropped_calls:
                for tc in dropped_calls:
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    self._emit(
                        "tool_end",
                        {
                            "name": str((fn or {}).get("name") or ""),
                            "call_id": str(tc.get("id") or ""),
                            "ok": False,
                            "result": "参数还没到，这次调用已取消",
                        },
                    )
                self.messages[-1]["tool_calls"] = ready_calls
                tool_calls = ready_calls
                if not tool_calls:
                    self.messages.append(
                        {
                            "role": "user",
                            "sidekick_internal": True,
                            "content": (
                                "The previous tool call had an empty argument object. "
                                "Call it again with every required field filled in."
                            ),
                        }
                    )
                    continue
            names = [(tc.get("function") or {}).get("name", "?") for tc in tool_calls]
            # A name cycle alone is not a loop: read→edit→test can legitimately
            # repeat while every result changes. Stop only when the same calls
            # also kept returning the same observations.
            cycle_period = stalled_tool_cycle(self.messages, start=loop_start)
            if cycle_period and repeating_tool_sequence(
                tool_names + [str(name) for name in names if name and name != "?"]
            ):
                self.messages[-1]["tool_calls"] = []
                self._emit(
                    "assistant_status",
                    {"text": "同样的步骤重复且没有新结果，正在收尾…", "tools": []},
                )
                if emit_assistant_text:
                    coalesce_internal_nudge(
                        self.messages,
                        kind="spin_stop",
                        content=(
                            "This tool cycle keeps returning the same observations. "
                            "Stop calling tools; report what is done and what still fails. "
                            "Do not restart the same script or page."
                        ),
                        role="system",
                    )
                    final = self._stream_final_reply()
                else:
                    final = "同样的步骤重复且没有新结果，已停止。"
                # The goal is not met; do not report a stall as "satisfied".
                self._goal_satisfied = False
                self._emit("goal_settled", {"satisfied": False, "blocked": False})
                break
            if getattr(self, "_disable_tools", False):
                # A read-only pass has no write_file. Opening the browser here
                # is the model substituting a page open for a file edit.
                kept_calls = []
                blocked_nav = False
                for tc in tool_calls:
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    if str((fn or {}).get("name") or "") != "browser_navigate":
                        kept_calls.append(tc)
                        continue
                    blocked_nav = True
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(tc.get("id") or ""),
                            "name": "browser_navigate",
                            "content": (
                                "ERROR: this turn cannot open the browser. "
                                "Do not call browser_navigate again. "
                                "Answer from what you already read."
                            ),
                            "ts": time.time(),
                        }
                    )
                if blocked_nav:
                    self._browser_fail_streak = int(getattr(self, "_browser_fail_streak", 0) or 0) + 1
                    tool_calls = kept_calls
                    names = [(tc.get("function") or {}).get("name", "?") for tc in tool_calls]
                    if self._browser_fail_streak >= 2 and not kept_calls:
                        final = (preamble or "").strip() or "这一步是只读检查，不能打开浏览器。请基于已读内容回答。"
                        break
                    if not kept_calls:
                        continue
            tool_names.extend(str(name) for name in names if name and name != "?")
            if any(name in ("delegate_task", "delegate_dialogue") for name in names):
                self._delegation_started = True
            self._emit(
                "assistant_status",
                {"text": f"调用工具：{', '.join(names)}", "tools": names},
            )
            if self.cancelled():
                was_cancelled = True
                break
            tool_results: list[dict[str, Any]] = []
            batch_start = len(self.messages)
            for tr in self._execute_tools(tool_calls):
                if self.cancelled():
                    was_cancelled = True
                    break
                self.messages.append(tr)
                # Record before squeezing: the judge must still see what the
                # tool returned after the transcript payload is cleared.
                ledger.record(
                    str(tr.get("name") or ""), str(tr.get("content") or "")
                )
                tool_results.append(tr)
            if was_cancelled:
                break
            self._squeeze_tool_batch(batch_start)
            # Park/squeeze must not leave dangling tool_calls for the next LLM call.
            self.messages = repair_incomplete_tool_chains(self.messages)
            navs = [tr for tr in tool_results if str(tr.get("name") or "") == "browser_navigate"]
            failed_navs = [
                tr
                for tr in navs
                if self._browser_nav_result_failed(str(tr.get("content") or ""))
            ]
            if navs and len(failed_navs) == len(navs):
                self._browser_fail_streak = int(getattr(self, "_browser_fail_streak", 0) or 0) + 1
            elif navs:
                self._browser_fail_streak = 0
            # Transient navigate failures must NOT end the turn. Nudge recovery
            # (start server / fix URL / alternate path) and keep the loop alive.
            if (
                navs
                and failed_navs
                and len(failed_navs) == len(navs)
                and browser_nav_fail_nudges < 3
            ):
                browser_nav_fail_nudges += 1
                coalesce_internal_nudge(
                    self.messages,
                    kind="browser_nav_recover",
                    content=_BROWSER_NAV_RECOVER_NUDGE,
                    role="system",
                )
            if (
                file_fail_nudges < 2
                and file_tool_results_blocked(tool_results)
            ):
                file_fail_nudges += 1
                coalesce_internal_nudge(
                    self.messages,
                    kind="file_retry",
                    content=_FILE_RETRY_NUDGE,
                    role="system",
                )
        if exhausted and not was_cancelled:
            self._emit("max_iterations", {"n": cap})
            if emit_assistant_text:
                self.messages.append(
                    {
                        "role": "user",
                        "content": "Iteration budget exhausted. Summarize status and stop.",
                    }
                )
                final = self._stream_final_reply()
            else:
                final = ""

        # Never end a non-cancelled tool-using turn with zero assistant text —
        # the UI would show emptyRoundOutput and look like a hard stop.
        if not was_cancelled and not (final or "").strip():
            for m in reversed(self.messages):
                if m.get("role") != "assistant":
                    continue
                if m.get("tool_calls"):
                    continue
                bit = str(m.get("content") or "").strip()
                if bit:
                    final = bit
                    break
            had_tools = any(
                m.get("role") == "tool" for m in self.messages[max(0, loop_start) :]
            )
            if not (final or "").strip() and emit_assistant_text and had_tools:
                coalesce_internal_nudge(
                    self.messages,
                    kind="empty_final_recover",
                    content=(
                        "Tool results are in context. Give a short status and next "
                        "step for the user goal. Do not apologize about truncation."
                    ),
                    role="system",
                )
                final = self._stream_final_reply()
            if not (final or "").strip():
                final = (
                    "（本轮已处理工具结果，但模型未返回可见回复。请继续下达指令。）"
                    if had_tools
                    else "（本轮已完成）"
                )
                self.messages.append(
                    {"role": "assistant", "content": final, "ts": time.time()}
                )
                if emit_assistant_text:
                    self._emit_paced_text(final)

        return final, turned, was_cancelled, compressed

    def run(
        self,
        user_text: str,
        *,
        mode: str = "agent",
        do_review: bool = True,
        display: str = "",
        disable_tools: bool = False,
        skip_plan: bool = False,
        block_delegate: bool = False,
        classify: bool = False,
    ) -> AgentResult:
        from ..services.fs_api import bind_active_workspace, reset_active_workspace
        from ..services.tenant_context import bind_session_id, reset_session_id

        ws_token = bind_active_workspace(self.settings.workspace)
        sid_token = bind_session_id(self.session_id or "")
        try:
            return self._run_turn(
                user_text,
                mode=mode,
                do_review=do_review,
                display=display,
                disable_tools=disable_tools,
                skip_plan=skip_plan,
                block_delegate=block_delegate,
                classify=classify,
            )
        finally:
            reset_session_id(sid_token)
            reset_active_workspace(ws_token)

    def _run_turn(
        self,
        user_text: str,
        *,
        mode: str = "agent",
        do_review: bool = True,
        display: str = "",
        disable_tools: bool = False,
        skip_plan: bool = False,
        block_delegate: bool = False,
        classify: bool = False,
    ) -> AgentResult:
        self._budget_mention_text = str(user_text or "")
        if self.settings is not None:
            try:
                setattr(self.settings, "budget_mention_text", self._budget_mention_text)
            except Exception:
                pass
        self._refresh_skills()
        self._delegation_started = False
        # Each user turn gets a clean evidence ledger; subagent turns too.
        _evidence_ledger(self).reset()
        # Thinking is compared within a turn: plan steps reuse the same goal,
        # a new user message does not.
        self._last_shown_reasoning = ""
        self._classify = bool(classify)
        if classify:
            disable_tools = True
        self._disable_tools = bool(disable_tools)
        self._block_delegate = bool(block_delegate)
        # Do not hard-force multi-agent fan-out from keyword regex.
        # The model decides whether to call delegate_task when tools are enabled.
        self._requires_initial_delegation = False
        # Top-level turns reset cancel; subagents keep a cancel already set by parent.
        if not self.is_subagent:
            self.clear_cancel()
            self.approval.begin_turn()
            self.guard.begin_turn()
            # Previous stop may have left unfinished tool_calls in history
            self._repair_dangling_tool_calls()
            # Also scrub cancel-control text that leaked into assistant bubbles
            # (so the model cannot imitate "用户已停止本轮生成" mid-stream).
            self._scrub_cancel_notices_in_history()
            # Re-pin live workspace truth so the model does not fall back to src/ priors
            if skip_plan and self.messages and self.messages[0].get("role") == "system":
                if classify:
                    self.messages[0]["content"] = (
                        "You classify one chat message. Reply with only the JSON object "
                        "the user asked for. Do not call tools, do not write a plan, "
                        "and do not answer the user's question."
                    )
                else:
                    from .prompts import build_social_system_prompt

                    self.messages[0]["content"] = build_social_system_prompt(
                        workspace=self.settings.workspace
                    )
            self._refresh_workspace_grounding()
            if not skip_plan:
                # A long-lived session keeps its first system message. Refresh
                # the memory section too, or a note that was later repaired
                # (or removed) would still be re-sent for the rest of the chat.
                self._refresh_memory_block()
                self._apply_turn_coherence_policy(user_text)
            self._turn_mutated = False
            self._turn_verified = False
        elif self.full_agent:
            self._repair_dangling_tool_calls()
            self._refresh_workspace_grounding()
            self._refresh_memory_block()
            self._turn_mutated = False
            self._turn_verified = False
        inherited = str(getattr(self, "_inherited_language", "") or "").strip()
        self._response_language_src = (
            inherited if self.is_subagent and inherited else ((display or "").strip() or user_text)
        )
        if self.messages and self.messages[0].get("role") == "system":
            sys_txt = str(self.messages[0].get("content") or "")
            # Refresh MCP / plugin capability text after tool rebind.
            if (not self.is_subagent) or self.full_agent:
                try:
                    sys_txt = apply_extensions_block(
                        sys_txt,
                        settings=self.settings,
                        talk_only=self.talk_only or self._disable_tools,
                    )
                except Exception:
                    pass
            self.messages[0]["content"] = apply_response_language(
                sys_txt,
                self._response_language_src,
            )
        user_turn = sum(1 for m in self.messages if m.get("role") == "user")
        if not self.is_subagent:
            # Each user turn owns a fresh spawn forest. Clearing here also stops
            # edit→resend from replaying the previous team's done cards/details.
            self.clear_canvas_forest()
            self._canvas_turn = user_turn + 1
        user_msg: dict[str, Any] = {"role": "user", "content": user_text, "ts": time.time()}
        disp = (display or "").strip()
        if disp and disp != user_text:
            user_msg["sidekick"] = {"display": disp}
        self.messages.append(user_msg)
        self.turn_counter += 1
        if not self.is_subagent and self.session_id:
            from ..services import fs_undo

            fs_undo.push_checkpoint(
                self.session_id, user_turn, user_text=disp or user_text
            )
            fs_undo.set_turn_context(self.session_id, user_turn)
            # Checkpoint early so a restart mid-turn still keeps the user message.
            try:
                from ..services.store import STORE

                STORE.persist(self.session_id)
            except Exception as exc:
                from ..core.logutil import get_logger, log_exception

                log_exception(
                    get_logger("metateam.agent"),
                    f"early persist failed for {self.session_id}",
                    exc,
                )
        self._emit(
            "turn_start",
            {"text": (disp or user_text)[:500], "message": "user turn"},
        )
        if not self.talk_only:
            self._autoload_requested_skill(user_text)

        max_iters = (
            1
            if classify
            else (
                self.settings.max_iterations
                if (not self.is_subagent or self.full_agent)
                else self.settings.subagent_max_iterations
            )
        )
        compressed = False
        self._last_compressed = False
        final = ""
        turned = 0
        was_cancelled = False
        mode_n = (mode or "agent").strip().lower()

        try:
            plan_goal = extract_plan_goal(user_text) or user_text
            if not self.is_subagent and mode_n == "plan":
                final, turned, was_cancelled, compressed = self._run_plan_only(plan_goal)
            elif not self.is_subagent and mode_n == "agent" and not disable_tools and not skip_plan:
                self._emit(
                    "assistant_status",
                    {"text": "正在判断是否需要先出方案…", "tools": []},
                )
                if needs_plan(self.compress_llm, user_text):
                    # Model decides Plan-confirm; plan against the user ask,
                    # not Skill-template scaffolding.
                    final, turned, was_cancelled, compressed = self._run_plan_only(
                        plan_goal
                    )
                else:
                    final, turned, was_cancelled, compressed = self._run_agent_loop(
                        max_iters
                    )
            else:
                final, turned, was_cancelled, compressed = self._run_agent_loop(max_iters)

            if was_cancelled:
                self._emit("cancelled", {"message": "已停止生成"})
                # Strip unfinished tool chains so the next user message won't resume them
                final = self._seal_cancelled_turn(final or "")

            if not was_cancelled and not self.is_subagent:
                extra = self._maybe_auto_verify()
                if extra:
                    final = (final or "") + extra
                    self._emit("assistant_delta", {"chunk": extra})
                    updated = False
                    for m in reversed(self.messages):
                        if m.get("role") == "assistant" and not m.get("tool_calls"):
                            m["content"] = (m.get("content") or "") + extra
                            updated = True
                            break
                    if not updated:
                        self.messages.append({"role": "assistant", "content": extra.lstrip()})

            review: dict[str, Any] = {}
            if (
                do_review
                and not classify
                and not was_cancelled
                and not self.is_subagent
                and self.settings.auto_skill_review
                and self.turn_counter % max(1, self.settings.review_every_n_turns) == 0
            ):
                try:
                    review = run_review(self.settings, self.messages, llm=self.compress_llm)
                    if review.get("memory") or review.get("skill"):
                        self.skills = load_skills(self.settings.skills_dir)
                        self._emit("review", {"result": review, "message": "self-improve review"})
                except Exception as exc:  # noqa: BLE001
                    review = {"error": str(exc)}

            self._emit(
                "turn_end",
                {
                    "iterations": turned,
                    "tokens": context_budget_tokens(self.messages, self.registry.schemas()),
                    "message": "turn complete",
                    "cancelled": was_cancelled,
                },
            )
            return AgentResult(
                text=final,
                messages=self.messages,
                iterations=turned,
                compressed=compressed or bool(getattr(self, "_last_compressed", False)),
                agent_id=self.agent_id,
                review=review,
                cancelled=was_cancelled,
            )
        finally:
            if not self.is_subagent and self.session_id:
                from ..services import fs_undo

                fs_undo.clear_turn_context()

    def _model_messages(self) -> list[dict[str, Any]]:
        """History plus a call-only language pin. Classification stays untouched."""
        if getattr(self, "_classify", False):
            return self.messages
        return language_pin_messages(
            dedupe_repeated_context(prune_stale_internal_nudges(self.messages)),
            str(getattr(self, "_response_language_src", "") or ""),
        )

    def _stream_text_to_ui(self, text: str, reasoning: str = "") -> None:
        from .think_tags import split_think_tags

        visible, tagged = split_think_tags(text or "")
        think = (reasoning or "").strip() or tagged
        if think:
            # Non-stream fallbacks bypass the live folder; fold here so a
            # repetition-heavy reasoning dump cannot paint the bubble twice.
            think = fold_reasoning_text(think)
        if not visible and not think:
            return
        self._emit("assistant_delta", {"chunk": "", "reset": True})
        if think:
            self._emit("assistant_reasoning_delta", {"chunk": think})
        if visible:
            self._emit_paced_text(visible, reset=False)

    def _emit_paced_text(self, text: str, *, reset: bool = True) -> None:
        """Send a finished reply in small pieces so the bubble is not one dump."""
        body = text or ""
        if reset:
            self._emit("assistant_delta", {"chunk": "", "reset": True})
        if not body:
            return
        if len(body) <= 48:
            self._emit("assistant_delta", {"chunk": body})
            return
        step = 8 if len(body) < 400 else 24
        for i in range(0, len(body), step):
            if self.cancelled():
                return
            self._emit("assistant_delta", {"chunk": body[i : i + step]})
            time.sleep(0.04)

    def _stream_final_reply(self) -> str:
        """Stream a tool-less completion into the UI; return full text."""
        self._emit("assistant_delta", {"chunk": "", "reset": True})
        parts: list[str] = []
        reasoning_parts: list[str] = []
        # Same holdback as the tool loop: a trailing colon may be the lead-in
        # of an action the model never took. Never let it reach the bubble.
        lead_in_tail = ""
        try:
            for kind, payload in self.llm.stream_chat(
                self._model_messages(), tools=None, cancel_check=self.cancelled
            ):
                if self.cancelled():
                    break
                if kind == "reasoning_delta":
                    piece = str(payload or "")
                    if piece:
                        reasoning_parts.append(piece)
                        self._emit("assistant_reasoning_delta", {"chunk": piece})
                elif kind == "delta":
                    piece = str(payload or "")
                    if not piece:
                        continue
                    parts.append(piece)
                    if lead_in_tail:
                        piece = lead_in_tail + piece
                        lead_in_tail = ""
                    piece, tail = split_dangling_colon(piece)
                    if tail:
                        lead_in_tail = tail
                    if piece:
                        self._emit("assistant_delta", {"chunk": piece})
        except Exception:
            if self.cancelled():
                text = strip_dangling_colon("".join(parts))
                msg: dict[str, Any] = {"role": "assistant", "content": text, "ts": time.time()}
                reasoning = "".join(reasoning_parts).strip()
                if reasoning:
                    msg["reasoning"] = reasoning
                self.messages.append(msg)
                return text
            assistant = self.llm.chat(
                self._model_messages(), tools=None, fold_repeats=True
            )
            text = strip_dangling_colon(assistant.get("content") or "")
            reasoning = str(assistant.get("reasoning") or "")
            self._stream_text_to_ui(text, reasoning=reasoning)
            self.messages.append({"role": "assistant", "content": text, "ts": time.time()})
            if reasoning:
                self.messages[-1]["reasoning"] = fold_reasoning_text(reasoning)
            return text

        text = strip_dangling_colon("".join(parts).strip())
        reasoning = "".join(reasoning_parts).strip()
        if streaming_text_looped(text) or streaming_text_looped(reasoning):
            text = (text or reasoning)[:480].rstrip()
            reasoning = ""
            self._emit("assistant_delta", {"chunk": "", "reset": True})
            if text:
                self._emit("assistant_delta", {"chunk": text})
            self._emit("assistant_reasoning_delta", {"chunk": "", "reset": True})
        msg = {"role": "assistant", "content": text, "ts": time.time()}
        if reasoning:
            msg["reasoning"] = reasoning
        self.messages.append(msg)
        return text

def run_once(prompt: str, settings: Optional[Settings] = None) -> AgentResult:
    return Agent(settings).run(prompt)
