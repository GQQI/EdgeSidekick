"""Persist / truncate session history."""
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

class SessionPersistMixin:
    def truncate_before_user_turn(self, session_id: str, keep_user_turns: int) -> bool:
        """Keep the first `keep_user_turns` user messages; drop that turn and everything after."""
        sess = self.get(session_id)
        if not sess:
            return False
        if keep_user_turns < 0:
            raise ValueError("keep_user_turns must be >= 0")
        # Cut against the full archived+live transcript — some early turns may
        # already have been moved into the compaction archive.
        msgs = _full_transcript(sess.agent)
        count = 0
        cut = len(msgs)
        for i, m in enumerate(msgs):
            if m.get("role") == "user" and not _is_internal_message(m):
                if count == keep_user_turns:
                    cut = i
                    break
                count += 1
        sess.agent.messages = msgs[:cut]
        sess.agent._archived_messages = []
        # Truncate used to leave _canvas_index / agent_tree intact, so edit→resend
        # immediately painted the previous multi-agent board with old summaries.
        try:
            clearer = getattr(sess.agent, "clear_canvas_forest", None)
            if callable(clearer):
                clearer()
            elif hasattr(sess.agent, "_canvas_index"):
                sess.agent._canvas_index.clear()
                if hasattr(sess.agent, "_ask_edges"):
                    sess.agent._ask_edges.clear()
            if hasattr(sess.agent, "_canvas_turn"):
                sess.agent._canvas_turn = 0
        except Exception as exc:
            log_exception(_log, f"clear canvas after truncate failed for {session_id}", exc)
        sess.agent_tree = []
        sess.updated_at = time.time()
        try:
            self.persist(session_id)
        except Exception as exc:
            log_exception(_log, f"persist after truncate failed for {session_id}", exc)
        return True

    def persist(self, session_id: str) -> Optional[str]:
        # Prefer live map — worker threads may not have the request ContextVar.
        sess = self._get_live(session_id) or self.get(session_id)
        if not sess:
            _log.error("persist skipped: session %s not found", session_id)
            return None
        # Full chronological transcript, not the (possibly compacted) live LLM
        # working set — otherwise context compression during a long chat would
        # permanently erase earlier turns from disk history.
        full_msgs = _full_transcript(sess.agent)
        # The 日程 home is created empty so a hidden social chat can anchor a
        # future run. It must survive a restart even before the first fire.
        if _user_turn_count(full_msgs) <= 0 and not str(session_id).startswith("schedhome_"):
            return None
        tree: list[Any] = []
        try:
            tree = sess.agent.canvas_tree()
        except Exception as exc:
            log_exception(_log, f"canvas_tree failed for {session_id}", exc)
        if not tree:
            tree = list(sess.agent_tree or [])
        sess.agent_tree = tree
        extra: dict[str, Any] = {"agent_tree": tree}
        try:
            from ..memory.user_directives import directives_to_payload

            dirs = directives_to_payload(getattr(sess.agent, "user_directives", None) or [])
            if dirs:
                extra["user_directives"] = dirs
        except Exception as exc:
            log_exception(_log, f"user_directives persist failed for {session_id}", exc)
        path = save_session(
            self.settings.root,
            full_msgs,
            model=self.settings.model,
            workspace=getattr(sess.agent.settings, "workspace", None) or self.settings.workspace,
            session_id=session_id,
            user_id=sess.user_id or None,
            title=sess.title or "",
            extra=extra,
            hidden=sess.hidden,
        )
        return str(path)

