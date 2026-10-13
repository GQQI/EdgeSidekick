"Main ReAct loop, turn runner, and final-reply streaming."

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

_STALL_NUDGE = (
    "Your previous generation stalled (no progress). "
    "Continue the task now: call a tool or give a concise answer. "
    "Do not restart a long silent thinking pass."
)
_FILE_RETRY_NUDGE = (
    "The previous file tool returned ERROR or WARNING. "
    "Do not treat that result as success. "
    "If a write or replace was cut off or missing required fields, retry that "
    "same tool with the exact filename and the complete arguments. "
    "If the path does not exist, look it up or report it — do not invent a "
    "file body, and do not repeat the same missing path. "
    "If the error is about decoding, retry with encoding= set to another codec."
)


class AgentLoopMixin:
    def _reject_early_stop(
        self,
        preamble: str,
        *,
        loop_start: int,
        hold_for_goal: bool,
        goal_nudges: int,
        tools_at_nudge: int,
        emit_assistant_text: bool,
    ) -> tuple[bool, int, int]:
        """Codex ends the turn when this reply has no tool call.

        The model already saw the tool results and decided whether they match
        the user's request. A reply with no tool call is that decision, so
        the loop does not ask another model.
        """
        if not hold_for_goal or getattr(self, "_disable_tools", False):
            return False, goal_nudges, tools_at_nudge
        self._goal_satisfied = True
        self._emit("goal_settled", {"satisfied": True, "blocked": False})
        return False, goal_nudges, tools_at_nudge

    def _run_agent_loop(
        self,
        max_iters: int,
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        emit_assistant_text: bool = True,
        hold_for_goal: bool = True,
    ) -> tuple[str, int, bool, bool]:
        """Run tool calls, then ask the same model again with the results.

        That next reply decides whether the user's request is met. No tool
        call ends the turn. Workbench and social execution share this loop.
        """
        compressed = False
        final = ""
        turned = 0
        was_cancelled = False
        stall_nudges = 0
        delegate_nudges = 0
        file_fail_nudges = 0
        goal_nudges = 0
        tools_at_nudge = 0
        tool_names: list[str] = []
        cap = int(max_iters or 0)
        i = 0
        exhausted = False
        loop_start = len(self.messages)
        self._goal_satisfied = None
        self._tool_repeat_streak = 0
        self._tool_repeat_nudges = 0
        self._text_loop_cuts = 0
        self._browser_same_runs = 0
        self._browser_fail_streak = 0
        self._incomplete_write_streak = 0
        self._last_tool_sig_set = None

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
            stalled = False
            reasoning_streamed_live = False
            if emit_assistant_text:
                self._emit("assistant_delta", {"chunk": "", "reset": True})
                # A new model round is a new message. The UI seals the previous
                # bubble on the text reset, then this clears live reasoning.
                self._emit("assistant_reasoning_delta", {"chunk": "", "reset": True})
            try:
                for kind, payload in self.llm.stream_chat(
                    self._model_messages(),
                    tools=schemas,
                    cancel_check=self.cancelled,
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
                            # A provider that flushes the whole reply as one
                            # chunk would otherwise appear only after the tool.
                            if len(piece) > 48:
                                self._emit_paced_text(piece, reset=False)
                            else:
                                self._emit("assistant_delta", {"chunk": piece})
                    elif kind == "reasoning_delta":
                        # Gate on emit_assistant_text like the content branch above —
                        # otherwise a silent loop (plan-prep) never sends the opening
                        # reset:true, so every internal round's reasoning glues onto
                        # whatever bubble happened to exist first.
                        if emit_assistant_text:
                            reasoning_streamed_live = True
                            self._emit(
                                "assistant_reasoning_delta",
                                {"chunk": str(payload)},
                            )
                    elif kind == "tool_delta" and isinstance(payload, dict):
                        self._emit("tool_call_delta", payload)
                    elif kind == "done":
                        assistant = payload  # type: ignore[assignment]
            except Exception:
                if self.cancelled():
                    was_cancelled = True
                    if streamed_buf.strip() and not final:
                        final = streamed_buf.strip()
                        self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
                    break
                if stalled:
                    if assistant is None and streamed_buf.strip():
                        assistant = {"role": "assistant", "content": streamed_buf.strip()}
                else:
                    assistant = self.llm.chat(self.messages, tools=schemas)
                    if emit_assistant_text:
                        self._stream_text_to_ui(
                            assistant.get("content") or "",
                            reasoning=str(assistant.get("reasoning") or ""),
                        )

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
                        self.messages.append(
                            {
                                "role": "user",
                                "content": _STALL_NUDGE,
                                "sidekick_internal": True,
                                "sidekick": {"internal": True, "kind": "stall"},
                            }
                        )
                        continue
                    final = streamed_buf.strip() or "（思考超时，已停止本轮）"
                    break
                was_cancelled = True
                if streamed_buf.strip() and not final:
                    final = streamed_buf.strip()
                    self.messages.append({"role": "assistant", "content": final, "ts": time.time()})
                break
            assistant.setdefault("ts", time.time())
            self.messages.append(assistant)

            # Some providers only return reasoning_content on the AGGREGATED
            # response (never as incremental deltas) when the turn also
            # includes tool_calls — common for reasoning models doing
            # function calling. Without this, the "thinking" bubble is
            # correctly saved to history (so it shows up on reload) but
            # never appears live before the tool card, since no
            # reasoning_delta event ever reached the UI.
            if emit_assistant_text and not reasoning_streamed_live:
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
                if len(reasoning_text) > 480:
                    self.messages[-1]["reasoning"] = reasoning_text[:480].rstrip() + "…"
                    if emit_assistant_text:
                        self._emit("assistant_reasoning_delta", {"chunk": "", "reset": True})
                kept = preamble[:480].rstrip() if preamble else ""
                preamble = kept
                self.messages[-1]["content"] = kept
                if emit_assistant_text:
                    self._emit("assistant_delta", {"chunk": "", "reset": True})
                    if kept:
                        self._emit("assistant_delta", {"chunk": kept})
                self._text_loop_cuts = int(getattr(self, "_text_loop_cuts", 0) or 0) + 1
                if self._text_loop_cuts <= 4:
                    self.messages.append(
                        {
                            "role": "user",
                            "sidekick_internal": True,
                            "content": (
                                "The previous draft repeated the same sentences and was cut. "
                                "Do not repeat that text. Continue the task by calling the next "
                                "tool that moves the goal forward. Answer only if the goal is "
                                "already done.\n\n"
                                + language_directive(
                                    str(getattr(self, "_response_language_src", "") or "")
                                )
                            ),
                            "sidekick": {"internal": True, "kind": "text_loop_cut"},
                        }
                    )
                    continue

            # Cross-turn watchdog: the exact same tool+args fired twice in a row
            # (e.g. guard keeps returning the same ERROR and the model keeps
            # retrying verbatim) — hard stop instead of burning iterations on a
            # call that will never succeed differently. Computed on the raw
            # assistant message, before any text/tool collapsing below.
            if tool_calls:
                cur_sig = self._tool_call_sig_set(assistant)
                incomplete = self._assistant_tool_args_incomplete(assistant)
                if incomplete:
                    # Truncated write/edit JSON collapses to path-only signatures;
                    # allow retries instead of treating them as a stuck loop.
                    self._incomplete_write_streak += 1
                    self._tool_repeat_streak = 0
                    self._browser_same_runs = 0
                elif cur_sig and cur_sig == self._last_tool_sig_set:
                    self._tool_repeat_streak += 1
                    self._incomplete_write_streak = 0
                    self._last_tool_sig_set = cur_sig
                else:
                    self._tool_repeat_streak = 0
                    self._incomplete_write_streak = 0
                    self._browser_same_runs = 0
                    self._last_tool_sig_set = cur_sig
            else:
                self._tool_repeat_streak = 0
                self._incomplete_write_streak = 0
                self._browser_same_runs = 0
                self._last_tool_sig_set = None

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

            if self._tool_repeat_streak >= 1 and self._live_browser_repeat(assistant):
                # The page is live: the same look or the same control can be used
                # again. Run it. Stop only after several identical browser calls
                # in a row, and don't mention a security check unless one showed up.
                self._browser_same_runs = int(getattr(self, "_browser_same_runs", 0) or 0) + 1
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
                self._tool_repeat_streak = 0
            elif self._tool_repeat_streak >= 1:
                # Context recovery — do not hard-stop on the first repeat.
                # Put the prior tool substance back in front of the model so it
                # can advance instead of re-firing identical args.
                prior = self._prior_results_for_tool_calls(assistant)
                self.messages[-1]["tool_calls"] = []
                self._tool_repeat_nudges = int(getattr(self, "_tool_repeat_nudges", 0) or 0) + 1
                if self._tool_repeat_nudges >= 3:
                    # The prior observation stays in history for the model.
                    # The chat only gets a short stop, not the raw tool dump.
                    final = (preamble + "\n\n") if preamble else ""
                    final += "同一次操作没有带来新结果，已停止重复。"
                    if self._prior_needs_human(prior):
                        final += "若页面出现安全验证，请在浏览器里完成后告诉我继续。"
                    self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                    break
                self.messages.append(
                    {
                        "role": "user",
                        "sidekick_internal": True,
                        "content": (
                            "这次调用和上一次相同，结果已经在上面。请基于已有结果继续。\n\n"
                            + (prior or "（上一次的结果已不在窗口里。请基于已有结论继续。）")
                        ),
                    }
                )
                self._tool_repeat_streak = 0
                continue

            # Do not abort the turn on repeated assistant text — keep generating.
            # (Previously replaced empty collapsed turns with "（已停止重复输出）".)
            if self._collapse_repeated_assistant(assistant):
                preamble = (assistant.get("content") or "").strip()
                tool_calls = assistant.get("tool_calls") or []
                if not tool_calls and not preamble:
                    # Collapse cleared a duplicate; continue the loop instead of
                    # injecting a stop notice into the user-visible stream.
                    continue

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
                        self.messages.append(
                            {
                                "role": "user",
                                "content": _STALL_NUDGE,
                                "sidekick_internal": True,
                                "sidekick": {"internal": True, "kind": "stall"},
                            }
                        )
                        continue
                    final = preamble or "（思考超时，已停止本轮）"
                    if not preamble:
                        self.messages[-1]["content"] = final
                    if emit_assistant_text:
                        self._emit("assistant_delta", {"chunk": "", "reset": True})
                        self._emit("assistant_delta", {"chunk": final})
                    break

                continue_goal, goal_nudges, tools_at_nudge = self._reject_early_stop(
                    preamble,
                    loop_start=loop_start,
                    hold_for_goal=hold_for_goal,
                    goal_nudges=goal_nudges,
                    tools_at_nudge=tools_at_nudge,
                    emit_assistant_text=emit_assistant_text,
                )
                if continue_goal:
                    continue

                final = preamble
                if not final:
                    reasoning = str(assistant.get("reasoning") or "").strip()
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
            if repeating_tool_sequence(tool_names + [str(name) for name in names if name and name != "?"]):
                self.messages[-1]["tool_calls"] = []
                self._emit(
                    "assistant_status",
                    {"text": "同样的步骤在重复，正在结束…", "tools": []},
                )
                if emit_assistant_text:
                    self.messages.append(
                        {
                            "role": "user",
                            "sidekick_internal": True,
                            "content": (
                                "The same short sequence of tools is repeating and the "
                                "outcome is not changing. Stop calling tools. Tell the "
                                "user what is already done and what is still failing. "
                                "Do not restart servers, reopen the same page, or run "
                                "the same script again.\n\n"
                                + language_directive(
                                    str(getattr(self, "_response_language_src", "") or "")
                                )
                            ),
                            "sidekick": {"internal": True, "kind": "spin_stop"},
                        }
                    )
                    final = self._stream_final_reply()
                else:
                    final = "同样的步骤在重复，已停止。"
                self._goal_satisfied = True
                self._emit("goal_settled", {"satisfied": True, "blocked": False})
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
                        final = (preamble or "").strip() or "这一步不能打开浏览器，已停止重复尝试。"
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
                tool_results.append(tr)
            if was_cancelled:
                break
            self._squeeze_tool_batch(batch_start)
            navs = [tr for tr in tool_results if str(tr.get("name") or "") == "browser_navigate"]
            failed_navs = [
                tr
                for tr in navs
                if str(tr.get("content") or "").lstrip().startswith("ERROR")
                or "chrome-error://" in str(tr.get("content") or "")
                or "chromewebdata" in str(tr.get("content") or "")
            ]
            if navs and len(failed_navs) == len(navs):
                self._browser_fail_streak = int(getattr(self, "_browser_fail_streak", 0) or 0) + 1
            elif navs:
                self._browser_fail_streak = 0
            if self._browser_fail_streak >= 2:
                final = (preamble or "").strip() or "页面没有打开，已停止重复尝试。"
                break
            if (
                file_fail_nudges < 2
                and file_tool_results_blocked(tool_results)
            ):
                file_fail_nudges += 1
                self.messages.append(
                    {
                        "role": "user",
                        "sidekick_internal": True,
                        "content": _FILE_RETRY_NUDGE,
                    }
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
        self._refresh_skills()
        self._delegation_started = False
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
                self._apply_turn_coherence_policy(user_text)
            self._turn_mutated = False
            self._turn_verified = False
        elif self.full_agent:
            self._repair_dangling_tool_calls()
            self._refresh_workspace_grounding()
            self._turn_mutated = False
            self._turn_verified = False
        inherited = str(getattr(self, "_inherited_language", "") or "").strip()
        self._response_language_src = (
            inherited if self.is_subagent and inherited else ((display or "").strip() or user_text)
        )
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = apply_response_language(
                str(self.messages[0].get("content") or ""),
                self._response_language_src,
            )
        user_turn = sum(1 for m in self.messages if m.get("role") == "user")
        if not self.is_subagent:
            # Persisted agent scenes belong to one user turn. History can then
            # restore the matching team rather than every team ever spawned.
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
            if not self.is_subagent and is_multi_agent_request(user_text):
                # Spawn / dialogue first. Plan confirm waits until this turn
                # is an implementation job, not a live multi-agent session.
                final, turned, was_cancelled, compressed = self._run_agent_loop(
                    max_iters
                )
            elif not self.is_subagent and mode_n == "plan":
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
            from .agent import AgentResult  # agent.py imports this mixin
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
            self.messages,
            str(getattr(self, "_response_language_src", "") or ""),
        )

    def _stream_text_to_ui(self, text: str, reasoning: str = "") -> None:
        from .think_tags import split_think_tags

        visible, tagged = split_think_tags(text or "")
        think = (reasoning or "").strip() or tagged
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
                    if piece:
                        parts.append(piece)
                        self._emit("assistant_delta", {"chunk": piece})
        except Exception:
            if self.cancelled():
                text = "".join(parts)
                msg: dict[str, Any] = {"role": "assistant", "content": text, "ts": time.time()}
                reasoning = "".join(reasoning_parts).strip()
                if reasoning:
                    msg["reasoning"] = reasoning
                self.messages.append(msg)
                return text
            assistant = self.llm.chat(self.messages, tools=None)
            text = assistant.get("content") or ""
            self._stream_text_to_ui(text, reasoning=str(assistant.get("reasoning") or ""))
            self.messages.append({"role": "assistant", "content": text, "ts": time.time()})
            if assistant.get("reasoning"):
                self.messages[-1]["reasoning"] = assistant.get("reasoning")
            return text

        text = "".join(parts).strip()
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

