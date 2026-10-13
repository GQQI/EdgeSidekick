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
from .agent_history import AgentHistoryMixin
from .agent_canvas import AgentCanvasMixin, _subagent_snapshot_item
from .agent_compress import AgentCompressMixin
from .agent_plan import AgentPlanMixin
from .agent_loop import AgentLoopMixin
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
from .tools.build import register_plugins_safely
from .tools.support import _skill_as_tool

PrintFn = Callable[[str], None]


@dataclass
class AgentResult:
    text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    compressed: bool = False
    agent_id: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


class Agent(
    AgentHistoryMixin,
    AgentExecuteMixin,
    AgentGroundingMixin,
    AgentCanvasMixin,
    AgentCompressMixin,
    AgentPlanMixin,
    AgentLoopMixin,
):
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
        self._mid_seq: int = 0
        # The plan currently being executed by _execute_plan (None when idle).
        # Single source of truth for the pinned plan panel on reattach.
        self._active_plan: Optional[dict[str, Any]] = None
        self._tool_repeat_streak = 0
        self._tool_repeat_nudges = 0
        self._browser_same_runs = 0
        self._browser_fail_streak = 0
        self._incomplete_write_streak = 0
        self._last_tool_sig_set: Optional[frozenset[str]] = None
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
        # Plugins installed or configured after this session started.
        register_plugins_safely(self.registry)

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

def run_once(prompt: str, settings: Optional[Settings] = None) -> AgentResult:
    return Agent(settings).run(prompt)
