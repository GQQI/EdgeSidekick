"""Chat SSE + reconnect."""

from __future__ import annotations

import contextvars
import queue
import threading
import time

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ...core.events import Event
from ...core.logutil import get_logger, log_exception
from ...runtime.context import context_budget_tokens, messages_tokens
from ...services.store import (
    STORE,
    _summarize_title,
    generate_session_title,
    is_untitled_session,
)
from ..http import require_session
from ..schemas import ChatRequest
from ..sse import event_payload, gate_replay_events, queue_sse, subscribe_bus

router = APIRouter(tags=["chat"])
_log = get_logger("metateam.api.chat")


@router.post("/api/chat")
async def chat_sse(req: ChatRequest) -> EventSourceResponse:
    sess = STORE.get(req.session_id) if req.session_id else None
    if sess is None:
        sess = STORE.create(workspace=req.workspace or None, social=bool(req.hidden))
        # Social one-shot calls (会话版 group/DM member streams) arrive with a
        # synthetic session id that cannot resolve; the fresh session they
        # force must never surface in the workbench history list.
        if req.hidden:
            sess.hidden = True
    elif req.workspace:
        if sess.busy:
            raise HTTPException(409, "会话正在执行，不能切换工作区")
        try:
            STORE.bind_workspace(sess, req.workspace)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    display = (req.display or "").strip()
    title_src = display or req.message
    title_candidate = _summarize_title(title_src)
    # Hidden (social one-shot) sessions are never listed — don't spend an LLM
    # call titling them.
    needs_llm_title = not sess.hidden and (
        is_untitled_session(sess.title) or len(sess.title) < 4
    )
    if title_candidate and needs_llm_title:
        sess.title = title_candidate

    q: queue.Queue[dict | None] = queue.Queue()
    unsub = subscribe_bus(sess, q)

    def worker() -> None:
        # 会话版 turns (hidden) browse headless with turn-end auto close;
        # set inside the worker thread because tools execute here.
        social_token = None
        if req.hidden:
            try:
                sess.agent.approval.set_allow_all(True)
            except Exception:
                pass
            try:
                from ...services.browser_sandbox import SOCIAL_BROWSING

                social_token = SOCIAL_BROWSING.set(True)
            except Exception:
                social_token = None
        try:
            q.put(
                event_payload(
                    "session",
                    {"session_id": sess.id, "demo": sess.agent.settings.demo_mode},
                    agent_id=sess.agent.agent_id,
                )
            )
            if needs_llm_title:
                try:
                    llm_title = generate_session_title(
                        sess.agent.llm,
                        req.message,
                        display=display,
                    )
                    if llm_title:
                        sess.title = llm_title
                except Exception as exc:
                    log_exception(_log, f"session title LLM failed for {sess.id}", exc)
            sess.updated_at = time.time()
            sess.stop_requested = False
            sess.busy = True
            try:
                from ...services.model_config import apply_auto_for_task

                if apply_auto_for_task(sess.agent.settings, req.message):
                    sess.agent.rebuild_llms()
            except Exception as exc:
                log_exception(_log, f"auto model selection failed for {sess.id}", exc)
            # The UI explicitly disables tools only for the group decision
            # pass. Execution calls use the same short-lived social session
            # naming convention, so session ids must never decide capability.
            classify = bool(getattr(req, "classify", False))
            disable_tools = bool(getattr(req, "disable_tools", False)) or classify
            result = sess.agent.run(
                req.message,
                mode=req.mode or "agent",
                display=display,
                disable_tools=disable_tools,
                skip_plan=bool(req.hidden),
                block_delegate=bool(getattr(req, "block_delegate", False)),
                classify=classify,
            )
            sess.updated_at = time.time()
            try:
                path = STORE.persist(sess.id)
                if not path:
                    _log.error("persist returned None for session %s", sess.id)
            except Exception as exc:
                log_exception(_log, f"persist failed for {sess.id}", exc)
            try:
                _schemas = sess.agent.registry.schemas()
                context_tokens = context_budget_tokens(sess.agent.messages, _schemas)
            except Exception:
                context_tokens = messages_tokens(result.messages)
            last_reasoning = ""
            for m in reversed(result.messages or []):
                if m.get("role") == "assistant" and m.get("reasoning"):
                    last_reasoning = str(m.get("reasoning") or "")
                    break
            final_data = {
                "text": result.text,
                "reasoning": last_reasoning,
                "iterations": result.iterations,
                "tokens": messages_tokens(result.messages),
                "context_tokens": context_tokens,
                "context_limit": int(getattr(sess.agent.settings, "context_limit", 0) or 0),
                "review": result.review,
                "session_id": sess.id,
                "cancelled": result.cancelled,
                "title": sess.title,
            }
            q.put(event_payload("final", final_data, agent_id=sess.agent.agent_id))
            try:
                sess.agent.bus.emit(
                    Event(type="final", data=final_data, agent_id=sess.agent.agent_id)
                )
            except Exception as exc:
                log_exception(_log, f"emit final failed for {sess.id}", exc)
        except Exception as exc:
            try:
                STORE.persist(sess.id)
            except Exception as persist_exc:
                log_exception(_log, f"persist after error failed for {sess.id}", persist_exc)
            q.put(event_payload("error", {"message": str(exc)}))
        finally:
            sess.busy = False
            if social_token is not None:
                try:
                    from ...services.browser_sandbox import SANDBOX

                    SANDBOX.close_agent_owned(lane="social")
                except Exception:
                    pass
                try:
                    from ...services.browser_sandbox import SOCIAL_BROWSING

                    SOCIAL_BROWSING.reset(social_token)
                except Exception:
                    pass
            try:
                from ...services.shell_job_notify import flush_pending_job_notices

                flush_pending_job_notices(sess.id)
            except Exception as flush_exc:
                log_exception(_log, f"flush job notices failed for {sess.id}", flush_exc)
            unsub()
            q.put(None)

    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(worker,), daemon=True).start()
    return queue_sse(q, on_disconnect=unsub)


@router.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str) -> EventSourceResponse:
    """Re-attach to a live turn after refresh. Does not start a new agent run."""
    sess = require_session(session_id)
    q: queue.Queue[dict | None] = queue.Queue()
    unsub = subscribe_bus(sess, q)
    for item in gate_replay_events(sess):
        q.put(item)
    if not sess.busy or sess.stop_requested:
        q.put(
            event_payload(
                "final",
                {
                    "text": "",
                    "session_id": sess.id,
                    "replay": True,
                    "cancelled": False,
                },
                agent_id=sess.agent.agent_id,
            )
        )
        q.put(None)
    return queue_sse(q, on_disconnect=unsub, on_close=unsub)
