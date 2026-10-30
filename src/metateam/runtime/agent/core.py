"""Agent core: construction, registry, small helpers."""
from __future__ import annotations

from typing import Any, Callable, Optional

from .agent_execute import AgentExecuteMixin
from .agent_grounding import AgentGroundingMixin
from .agent_history import AgentHistoryMixin
from .compress import AgentCompressMixin
from .loop import AgentLoopMixin
from .plan_run import AgentPlanMixin
from .spawn import AgentSpawnMixin
from .turn import AgentTurnMixin
from .helpers import *  # noqa: F403

PrintFn = Callable[[str], None]

class Agent(
    AgentHistoryMixin,
    AgentExecuteMixin,
    AgentGroundingMixin,
    AgentSpawnMixin,
    AgentCompressMixin,
    AgentPlanMixin,
    AgentLoopMixin,
    AgentTurnMixin,
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
        task_contract: Optional[dict[str, Any]] = None,
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
        # How this worker reports back (stop_when + required sections). The
        # goal judge receives it so a delivered report is not mistaken for
        # unfinished work. Empty for the lead and for free-form party turns.
        self.task_contract = dict(task_contract or {})
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
        # Session sticky constraints ("不要读 XX") — refreshed into system each turn.
        self.user_directives: list[dict[str, Any]] = []
        self._turn_policy = None
        self._turn_mutated = False
        self._turn_verified = False

        if messages is not None:
            self.messages = messages
        else:
            if self.social:
                from ..prompts import build_social_system_prompt

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
        from ..tools.build import apply_extension_budget_safely

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
            from ...core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), "close_active_stream failed", exc)
        for child in list(self._children):
            try:
                child.stop_generation()
            except Exception as exc:
                from ...core.logutil import get_logger, log_exception

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
