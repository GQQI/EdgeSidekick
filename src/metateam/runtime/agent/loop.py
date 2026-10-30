"""Agent mixin: loop."""
from __future__ import annotations

from typing import Any, Optional

from . import helpers as _h
from .helpers import *  # noqa: F403
from .agent_execute import drop_unready_tool_calls, file_tool_results_blocked

class AgentLoopMixin:

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
                    from ...plugins.tools import plugin_tool_summary

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
                            from ..tools.extension_budget import (
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
                    "（本轮已处理工具结果，但模型未返回可见回复。请继续输入任务。）"
                    if had_tools
                    else "（本轮已完成）"
                )
                self.messages.append(
                    {"role": "assistant", "content": final, "ts": time.time()}
                )
                if emit_assistant_text:
                    self._emit_paced_text(final)

        return final, turned, was_cancelled, compressed
