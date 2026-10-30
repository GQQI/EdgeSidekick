"""Approval / ask / plan gate helpers for SessionStore."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...runtime.agent import Agent
from ...core.config import Settings, get_settings, reload_settings
from ...core.events import EventBus, new_id
from ...core.logutil import get_logger, log_exception
from ..session import load_session, load_session_bundle, save_session, sessions_dir, workspace_matches

_log = get_logger("metateam.store")

from .model import ChatSession

class SessionGatesMixin:
    def _approval_gate(self) -> Any:
        from ...runtime.approval import ApprovalGate
        from ..app_prefs import auto_approve_tools

        gate = ApprovalGate()
        if auto_approve_tools():
            gate.set_allow_all(True)
        return gate

    def apply_auto_approve_tools(self, enabled: bool, user_id: str | None = None) -> None:
        from ..tenant_context import get_user_id

        uid = user_id if user_id is not None else get_user_id()
        with self._lock:
            sessions = [
                s
                for s in self._sessions.values()
                if not uid or s.user_id == uid or not s.user_id
            ]
        for sess in sessions:
            try:
                # 会话版 has no approval UI. Keep its gate open even when the
                # workbench safety switch is turned off.
                social = bool(getattr(sess.agent, "social", False) or sess.hidden)
                sess.agent.approval.set_allow_all(True if social else bool(enabled))
            except Exception as exc:
                log_exception(_log, f"apply auto-approve failed for {sess.id}", exc)

    def decide_approval(
        self,
        session_id: str,
        approval_id: str,
        approved: bool,
        *,
        remember: bool = False,
        patch_args: Optional[dict[str, Any]] = None,
    ) -> bool:
        sess = self.get(session_id)
        if not sess:
            return False
        return sess.agent.approval.decide(
            approval_id, approved, remember=remember, patch_args=patch_args
        )

    def answer_ask(
        self,
        session_id: str,
        ask_id: str,
        *,
        choice: str,
        text: str = "",
        option_label: str = "",
    ) -> bool:
        sess = self.get(session_id)
        if not sess:
            return False
        return sess.agent.ask.answer(
            ask_id,
            choice=choice,
            text=text,
            option_label=option_label,
        )

    def decide_plan(
        self,
        session_id: str,
        plan_id: str,
        approved: bool,
        *,
        summary: Optional[str] = None,
        tasks: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        sess = self.get(session_id)
        if not sess:
            return False
        return sess.agent.plan_gate.decide(
            plan_id, approved, summary=summary, tasks=tasks
        )

    def runtime_snapshot(self, sess: ChatSession) -> dict[str, Any]:
        agent = sess.agent
        return {
            # A stop request is terminal from the UI's perspective even while
            # the worker thread is unwinding a provider stream in the background.
            "busy": bool(sess.busy and not sess.stop_requested),
            "pending_approvals": agent.approval.pending(),
            "pending_asks": agent.ask.pending(),
            "pending_plans": agent.plan_gate.pending(),
            "active_plan": agent.active_plan_snapshot() if sess.busy else None,
            "live_subagents": agent.live_subagent_snapshot(),
        }

