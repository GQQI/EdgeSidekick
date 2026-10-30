"""Agent mixin: turn."""
from __future__ import annotations

from typing import Any, Optional

from . import helpers as _h
from .helpers import *  # noqa: F403

class AgentTurnMixin:

    def _iteration_budget(self) -> int:
        """ReAct round budget for this turn.

        Config value 0 means "no explicit cap" for the lead. A leaf worker
        still gets a finite budget so a model that never yields a tool-less
        reply cannot run forever; the goal judge normally stops it earlier.
        """
        if not self.is_subagent or self.full_agent:
            return int(self.settings.max_iterations or 0)
        return int(self.settings.subagent_max_iterations or 0) or SUBAGENT_ITERATION_BUDGET

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
        from ...services.fs_api import bind_active_workspace, reset_active_workspace
        from ...services.tenant_context import bind_session_id, reset_session_id

        ws_token = bind_active_workspace(self.settings.workspace)
        sid_token = bind_session_id(self.session_id or "")
        self._directives_ctx_token = None
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
            token = getattr(self, "_directives_ctx_token", None)
            if token is not None:
                try:
                    from ...services.user_directives import reset_directives

                    reset_directives(token)
                except Exception:
                    pass
                self._directives_ctx_token = None
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
            # Sticky user constraints ("不要读 XX") — extract before tools run.
            if not classify:
                try:
                    from ...services.user_directives import (
                        apply_directive_updates,
                        bind_directives,
                        directives_to_payload,
                        extract_directive_updates,
                    )

                    updates = extract_directive_updates(
                        (display or "").strip() or user_text
                    )
                    if updates:
                        merged = apply_directive_updates(
                            getattr(self, "user_directives", None) or [],
                            updates,
                        )
                        self.user_directives = directives_to_payload(merged)
                    self._directives_ctx_token = bind_directives(
                        getattr(self, "user_directives", None) or []
                    )
                except Exception:
                    self._directives_ctx_token = None
            # Re-pin live workspace truth so the model does not fall back to src/ priors
            if skip_plan and self.messages and self.messages[0].get("role") == "system":
                if classify:
                    self.messages[0]["content"] = (
                        "You classify one chat message. Reply with only the JSON object "
                        "the user asked for. Do not call tools, do not write a plan, "
                        "and do not answer the user's question."
                    )
                else:
                    from ..prompts import build_social_system_prompt

                    self.messages[0]["content"] = build_social_system_prompt(
                        workspace=self.settings.workspace
                    )
            self._refresh_workspace_grounding()
            if not skip_plan:
                # A long-lived session keeps its first system message. Refresh
                # CORE (delegation / tool-call rules) and the memory section —
                # otherwise prompt fixes never reach mid-chat DeepSeek turns,
                # and repaired/removed notes would still be re-sent.
                self._refresh_core_prompt()
                self._refresh_directives_block()
                self._refresh_memory_block()
                self._apply_turn_coherence_policy(user_text)
            self._turn_mutated = False
            self._turn_verified = False
        elif self.full_agent:
            self._repair_dangling_tool_calls()
            self._refresh_workspace_grounding()
            try:
                from ...services.user_directives import bind_directives

                self._directives_ctx_token = bind_directives(
                    getattr(self, "user_directives", None) or []
                )
            except Exception:
                self._directives_ctx_token = None
            self._refresh_directives_block()
            self._refresh_memory_block()
            self._turn_mutated = False
            self._turn_verified = False
        elif self.is_subagent:
            # Leaf workers still need the deny-read ContextVar for read_file.
            try:
                from ...services.user_directives import bind_directives

                self._directives_ctx_token = bind_directives(
                    getattr(self, "user_directives", None) or []
                )
            except Exception:
                self._directives_ctx_token = None
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
            from ...services import fs_undo

            fs_undo.push_checkpoint(
                self.session_id, user_turn, user_text=disp or user_text
            )
            fs_undo.set_turn_context(self.session_id, user_turn)
            # Checkpoint early so a restart mid-turn still keeps the user message.
            try:
                from ...services.store import STORE

                STORE.persist(self.session_id)
            except Exception as exc:
                from ...core.logutil import get_logger, log_exception

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

        max_iters = 1 if classify else self._iteration_budget()
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
                from ...services import fs_undo

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
        from ..think_tags import split_think_tags

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
