"""Create / get / list / delete session lifecycle."""
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
from .titles import (
    is_untitled_session,
    generate_session_title,
    _title_from_messages,
    _prefer_title,
    _is_internal_message,
    _full_transcript,
    _user_turn_count,
    _single_social_turn,
    _message_display_text,
    _is_skill_or_command_message,
)

class SessionLifecycleMixin:
    def refresh_settings(
        self,
        *,
        rebind_llm: bool = True,
        rebind_workspace: bool = False,
        user_id: str | None = None,
    ) -> Settings:
        """Reload model config and optionally rebind live agents.

        ``user_id`` limits which sessions are touched (current tenant).
        Workspace is copied onto those sessions only when ``rebind_workspace``
        is True, so saving a model does not yank another chat onto a new folder.
        """
        self.settings = reload_settings() if rebind_llm else get_settings()
        from ...services.skills import load_skills
        from ...extensions.model_config import apply_to_settings, load_model_config
        from ..tenant_context import apply_knowledge_to_settings, get_user_id

        cfg = load_model_config()
        uid = user_id if user_id is not None else get_user_id()
        with self._lock:
            sessions = [
                s
                for s in self._sessions.values()
                if not uid or s.user_id == uid or not s.user_id
            ]
        live_ws = getattr(self.settings, "workspace", None)
        live_skills = getattr(self.settings, "skills_dir", None)
        live_memory = getattr(self.settings, "memory_file", None)
        for sess in sessions:
            try:
                apply_to_settings(sess.agent.settings, cfg)
                apply_knowledge_to_settings(sess.agent.settings, uid)
                if rebind_workspace and live_ws is not None:
                    sess.agent.settings.workspace = live_ws
                    if live_skills is not None:
                        prev_skills = sess.agent.settings.skills_dir
                        sess.agent.settings.skills_dir = live_skills
                        if live_memory is not None:
                            sess.agent.settings.memory_file = live_memory
                        if prev_skills != live_skills:
                            sess.agent.skills = load_skills(live_skills)
                    try:
                        sess.agent._refresh_workspace_grounding()
                    except Exception as exc:
                        log_exception(_log, f"workspace grounding failed for {sess.id}", exc)
                keyed = bool(str(getattr(sess.agent.settings, "api_key", "") or "").strip())
                llm = getattr(sess.agent, "llm", None)
                llm_demo = bool(llm is not None and getattr(llm, "demo", False))
                if not rebind_llm and not (keyed and llm_demo):
                    continue
                sess.agent.rebuild_llms()
            except Exception as exc:
                log_exception(_log, f"refresh_settings failed for session {sess.id}", exc)
        return self.settings

    def create(self, workspace: Optional[str] = None, *, social: bool = False) -> ChatSession:
        # Always use latest settings for new chats (model switch)
        from ..tenant_context import get_user_id
        from ...extensions.model_config import apply_to_settings, load_model_config

        self.settings = get_settings()
        apply_to_settings(self.settings, load_model_config())
        # Per-session clone so pinning a folder cannot mutate the tenant overlay
        # (and so the system prompt is built AFTER the pin, not before).
        sess_settings = self.settings.clone()
        if workspace:
            from ..workspace.workspace_store import remember_recent, resolve_workspace_path

            folder = resolve_workspace_path(workspace)
            sess_settings.workspace = folder
            try:
                remember_recent(folder)
            except Exception:
                pass
        bus = EventBus()
        agent = Agent(sess_settings, bus=bus, approval=self._approval_gate(), social=social)
        if social:
            agent.approval.set_allow_all(True)
        sess = ChatSession(id=new_id("sess"), agent=agent, user_id=get_user_id())
        agent.session_id = sess.id
        with self._lock:
            self._sessions[sess.id] = sess
        return sess

    def ensure_schedule_home(self) -> ChatSession:
        """Durable, non-hidden session that owns tasks created from 会话版."""
        from ..tenant_context import get_user_id
        from ...extensions.model_config import apply_to_settings, load_model_config

        uid = get_user_id()
        raw = (uid or "default").strip() or "default"
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)[:64]
        sid = f"schedhome_{safe}"
        existing = self.get(sid)
        if existing:
            changed = False
            if existing.hidden:
                existing.hidden = False
                changed = True
            if is_untitled_session(existing.title):
                existing.title = "日程"
                changed = True
            if changed:
                self.persist(sid)
            return existing
        self.settings = get_settings()
        apply_to_settings(self.settings, load_model_config())
        sess_settings = self.settings.clone()
        bus = EventBus()
        agent = Agent(sess_settings, bus=bus, approval=self._approval_gate())
        sess = ChatSession(id=sid, agent=agent, user_id=uid, title="日程", hidden=False)
        agent.session_id = sess.id
        with self._lock:
            self._sessions[sid] = sess
        self.persist(sid)
        return sess

    def anchor_schedule_session(self, sess: Optional[ChatSession]) -> ChatSession:
        """Bind a new task to a session that will still exist when it fires.

        Workbench chats keep the task. Hidden 会话版 turns use the 日程 home,
        because their one-shot session is gone before the next run.
        """
        hidden = sess is None or bool(getattr(sess, "hidden", False)) or bool(
            getattr(getattr(sess, "agent", None), "social", False)
        )
        if sess is not None and not hidden:
            return sess
        home = self.ensure_schedule_home()
        if sess is not None and not home.busy:
            folder = getattr(getattr(getattr(sess, "agent", None), "settings", None), "workspace", None)
            if folder:
                try:
                    self.bind_workspace(home, str(folder))
                except Exception:
                    pass
        return home

    def bind_workspace(self, sess: ChatSession, workspace: Optional[str]) -> None:
        """Rebind one idle session to the workspace supplied by its UI turn.

        This is deliberately session-scoped: switching a social group's folder
        must not mutate the tenant-wide default or other conversations.  It is
        also safe for short-lived social sessions, whose synthetic ids make a
        new session on nearly every member/wave call.
        """
        raw = str(workspace or "").strip()
        if not raw:
            return
        from ..workspace.workspace_store import remember_recent, resolve_workspace_path

        folder = resolve_workspace_path(raw)
        current = getattr(sess.agent.settings, "workspace", None)
        try:
            unchanged = current is not None and current.resolve() == folder
        except Exception:
            unchanged = False
        if unchanged:
            return
        sess.agent.settings.workspace = folder
        # Discoveries from the previous root must not be treated as facts in
        # the newly selected folder. The next run rebuilds grounding as well.
        try:
            sess.agent.workspace_facts.clear()
            sess.agent._refresh_workspace_grounding()
        except Exception as exc:
            log_exception(_log, f"workspace grounding failed for session {sess.id}", exc)
        try:
            remember_recent(folder)
        except Exception:
            pass
    def _get_live(self, session_id: str) -> Optional[ChatSession]:
        """Lookup in-memory session without ContextVar ownership checks.

        Chat SSE workers run on threads that do not inherit request ContextVars;
        persist/checkpoint must still find the live session by id.
        """
        with self._lock:
            return self._sessions.get(session_id)

    def get(self, session_id: str) -> Optional[ChatSession]:
        from ..tenant_context import get_user_id

        uid = get_user_id()
        with self._lock:
            hit = self._sessions.get(session_id)
        if hit:
            if hit.user_id and hit.user_id != uid:
                return None
            return hit
        return self._restore_from_disk(session_id)

    def stop(self, session_id: str) -> bool:
        sess = self.get(session_id) or self._get_live(session_id)
        if not sess:
            return False
        sess.stop_requested = True
        sess.updated_at = time.time()
        sess.agent.request_cancel()
        return True

    def _migrate_orphan_sessions(self, uid: str) -> None:
        """Copy legacy/orphan sess_*.json into the current user's tenant folder once."""
        import json
        from pathlib import Path

        from ...core.config import REPO_ROOT
        from ..session import legacy_sessions_dir

        dest = sessions_dir(self.settings.root, uid)
        roots = [
            legacy_sessions_dir(self.settings.root),
            REPO_ROOT / "backend" / "sessions",
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.glob("sess_*.json"):
                target = dest / path.name
                if target.exists():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                    owner = str(meta.get("user_id") or "").strip()
                    # Adopt unowned orphans; skip files clearly owned by someone else.
                    if owner and owner != uid:
                        continue
                    meta["user_id"] = uid
                    data["meta"] = meta
                    target.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception as exc:
                    log_exception(_log, f"orphan session migrate failed for {path}", exc)

    def _session_paths_for_user(self) -> list[Any]:
        from ..session import legacy_sessions_dir
        from ..tenant_context import DEFAULT_USER_ID, get_user_id

        uid = get_user_id()
        try:
            self._migrate_orphan_sessions(uid)
        except Exception as exc:
            log_exception(_log, "orphan session migrate sweep failed", exc)
        paths: list[Any] = list(sessions_dir(self.settings.root, uid).glob("*.json"))
        # Pre-setup / default: also surface leftover flat sessions
        if uid == DEFAULT_USER_ID:
            for path in legacy_sessions_dir(self.settings.root).glob("sess_*.json"):
                paths.append(path)
        return paths

    def _restore_from_disk(self, session_id: str) -> Optional[ChatSession]:
        from ...core.config import REPO_ROOT
        from ..tenant_context import get_user_id

        uid = get_user_id()
        candidates = [
            sessions_dir(self.settings.root, uid) / f"{session_id}.json",
        ]
        from ..session import legacy_sessions_dir
        from ..tenant_context import DEFAULT_USER_ID

        if uid == DEFAULT_USER_ID:
            candidates.append(legacy_sessions_dir(self.settings.root) / f"{session_id}.json")
            candidates.append(REPO_ROOT / "backend" / "sessions" / f"{session_id}.json")

        path = next((p for p in candidates if p.exists()), None)
        if not path:
            return None
        try:
            meta, messages, extra = load_session_bundle(path)
        except Exception:
            return None
        if meta.user_id and meta.user_id != uid:
            return None
        self.settings = get_settings()
        from ...extensions.model_config import apply_to_settings, load_model_config

        apply_to_settings(self.settings, load_model_config())
        sess_settings = self.settings.clone()
        ws = (meta.workspace or "").strip()
        if ws:
            try:
                from pathlib import Path as _Path

                folder = _Path(ws).expanduser().resolve()
                if folder.is_dir():
                    sess_settings.workspace = folder
            except Exception:
                pass
        bus = EventBus()
        agent = Agent(sess_settings, bus=bus, messages=messages, approval=self._approval_gate())
        # Disk stores the full transcript so the chat can scroll back. The live
        # window is only the working set — otherwise the first turn after open
        # is already several times over the limit and every new read is dropped.
        from ...runtime.context import park_overflow

        live, archived = park_overflow(
            agent.messages,
            context_limit=max(8000, int(agent.settings.context_limit or 0)),
            keep_recent_tokens=max(4000, int(agent.settings.keep_recent_tokens or 0)),
        )
        agent.messages = live
        agent._archived_messages = list(archived)
        mtime = path.stat().st_mtime
        title = _prefer_title(meta.title, _title_from_messages(messages, ""), fallback=meta.id)
        def _str_list(key: str) -> list[str]:
            raw = extra.get(key)
            if not isinstance(raw, list):
                return []
            return [str(x).strip() for x in raw if str(x).strip()]

        sess = ChatSession(
            id=meta.id or session_id,
            agent=agent,
            created_at=mtime,
            updated_at=mtime,
            title=title,
            user_id=meta.user_id or uid,
            hidden=bool(meta.hidden),
            agent_tree=list(extra.get("agent_tree") or []) if isinstance(extra.get("agent_tree"), list) else [],
            mcp_server_ids=_str_list("mcp_server_ids"),
            plugin_ids=_str_list("plugin_ids"),
            recent_tool_successes=_str_list("recent_tool_successes"),
        )
        try:
            setattr(agent.settings, "mcp_bound_ids", list(sess.mcp_server_ids))
            setattr(agent.settings, "plugin_bound_ids", list(sess.plugin_ids))
            setattr(agent.settings, "recent_tool_successes", list(sess.recent_tool_successes))
        except Exception:
            pass
        agent.session_id = sess.id
        try:
            from ..memory.user_directives import directives_to_payload

            raw_dirs = extra.get("user_directives")
            if isinstance(raw_dirs, list) and raw_dirs:
                agent.user_directives = directives_to_payload(raw_dirs)
        except Exception as exc:
            log_exception(_log, f"user_directives restore failed for {session_id}", exc)
        with self._lock:
            self._sessions[sess.id] = sess
        return sess

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        workspace: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        from ..tenant_context import get_user_id, workspace_settings_key

        uid = get_user_id()
        current_ws = getattr(get_settings(), "workspace", "")
        # By default show every chat regardless of which folder it is pinned
        # to — several chats may be running against different workspaces at
        # once. Pass ``workspace`` (path) or ``workspace_id`` (stable id) to
        # narrow to one project's history. ``workspace_id`` is preferred —
        # it is looked up directly instead of re-normalizing two path strings
        # on every row, so it cannot drift from what the workspace picker shows.
        filter_ws = workspace if workspace is not None else None
        filter_wsid = (workspace_id or "").strip() or None
        items: dict[str, dict[str, Any]] = {}

        # Disk history first
        for path in self._session_paths_for_user():
            try:
                meta, messages = load_session(path)
                if meta.user_id and meta.user_id != uid:
                    continue
                if filter_ws and not workspace_matches(meta.workspace, filter_ws):
                    continue
                if filter_wsid and workspace_settings_key(meta.workspace or "") != filter_wsid:
                    continue
                sid = meta.id or path.stem
                user_count = _user_turn_count(messages)
                if user_count <= 0:
                    continue
                # Social one-shot sessions (会话版临时会话) never belong in the
                # workbench history — new builds mark them hidden on creation,
                # older files are recognized by their single social prompt.
                if meta.hidden or (user_count == 1 and _single_social_turn(messages)):
                    continue
                mtime = path.stat().st_mtime
                updated = mtime
                if meta.updated_at:
                    try:
                        from datetime import datetime

                        updated = max(
                            mtime,
                            datetime.fromisoformat(meta.updated_at.replace("Z", "+00:00")).timestamp(),
                        )
                    except ValueError:
                        pass
                created = path.stat().st_ctime
                if meta.created_at:
                    try:
                        from datetime import datetime

                        created = datetime.fromisoformat(
                            meta.created_at.replace("Z", "+00:00")
                        ).timestamp()
                    except ValueError:
                        pass
                ws_path = str(meta.workspace or "")
                items[sid] = {
                    "id": sid,
                    "title": _prefer_title(
                        meta.title,
                        _title_from_messages(messages, ""),
                        fallback=sid,
                    ),
                    "created_at": created,
                    "updated_at": updated,
                    "messages": len(messages),
                    "user_turns": user_count,
                    "demo": False,
                    "busy": False,
                    "source": "disk",
                    "workspace": ws_path,
                    "workspace_id": workspace_settings_key(ws_path) if ws_path else "",
                    "workspace_name": Path(ws_path).name if ws_path else "",
                    "is_current_workspace": workspace_matches(ws_path, current_ws),
                }
            except Exception:
                continue

        with self._lock:
            live = list(self._sessions.values())
        for s in live:
            if s.user_id and s.user_id != uid:
                continue
            sess_ws = str(getattr(s.agent.settings, "workspace", "") or "")
            if filter_ws and not workspace_matches(sess_ws, filter_ws):
                continue
            if filter_wsid and workspace_settings_key(sess_ws) != filter_wsid:
                continue
            full_msgs = _full_transcript(s.agent)
            user_turns = _user_turn_count(full_msgs)
            if user_turns <= 0:
                continue
            # Social one-shot sessions never belong in the workbench history.
            if s.hidden or (user_turns == 1 and _single_social_turn(full_msgs)):
                continue
            prev = items.get(s.id)
            # Prefer the freshest interaction time (memory vs disk)
            updated = s.updated_at
            if prev and float(prev.get("updated_at") or 0) > updated:
                updated = float(prev["updated_at"])
            items[s.id] = {
                "id": s.id,
                "title": _prefer_title(
                    s.title,
                    _title_from_messages(full_msgs, ""),
                    fallback=s.id,
                ),
                "created_at": s.created_at,
                "updated_at": updated,
                "messages": len(full_msgs),
                "user_turns": user_turns,
                "demo": s.agent.settings.demo_mode,
                "busy": bool(s.busy and not s.stop_requested),
                "source": "memory",
                "workspace": sess_ws,
                "workspace_id": workspace_settings_key(sess_ws) if sess_ws else "",
                "workspace_name": Path(sess_ws).name if sess_ws else "",
                "is_current_workspace": workspace_matches(sess_ws, current_ws),
            }

        all_items = sorted(
            items.values(),
            key=lambda x: float(x.get("updated_at") or 0),
            reverse=True,
        )
        total = len(all_items)
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 20)))
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * page_size
        chunk = all_items[start : start + page_size]
        return {
            "items": chunk,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }

    def delete(self, session_id: str) -> bool:
        from ...core.config import REPO_ROOT
        from ..session import legacy_sessions_dir
        from ..tenant_context import DEFAULT_USER_ID, get_user_id

        uid = get_user_id()
        removed = False
        with self._lock:
            hit = self._sessions.get(session_id)
            if hit and hit.user_id and hit.user_id != uid:
                return False
            if session_id in self._sessions:
                del self._sessions[session_id]
                removed = True
        candidates = [sessions_dir(self.settings.root, uid) / f"{session_id}.json"]
        if uid == DEFAULT_USER_ID:
            candidates.append(legacy_sessions_dir(self.settings.root) / f"{session_id}.json")
            candidates.append(REPO_ROOT / "backend" / "sessions" / f"{session_id}.json")
        for path in candidates:
            if path.exists():
                try:
                    path.unlink()
                    removed = True
                except OSError:
                    pass
        return removed

