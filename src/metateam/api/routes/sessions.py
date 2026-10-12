"""Session CRUD, gates, and transcript rewind."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ...runtime.context import context_budget_tokens, messages_tokens, schemas_tokens
from ...services.store import STORE
from ..http import require_session
from ..schemas import (
    ApprovalDecision,
    AskAnswer,
    PlanConfirm,
    ReplayBody,
    SessionCreate,
    TruncateBody,
    WorkspaceSet,
)

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
def list_sessions(
    page: int = 1,
    page_size: int = 20,
    workspace: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    return STORE.list(page=page, page_size=page_size, workspace=workspace, workspace_id=workspace_id)


def _workspace_ref(ws: str) -> dict[str, Any] | None:
    if not ws:
        return None
    from ...services.tenant_context import workspace_settings_key

    return {
        "path": ws,
        "name": ws.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        "id": workspace_settings_key(ws),
    }


@router.post("/api/sessions")
def create_session(body: SessionCreate = SessionCreate()) -> dict[str, Any]:
    try:
        sess = STORE.create(workspace=body.workspace or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ws = str(getattr(sess.agent.settings, "workspace", "") or "")
    return {
        "id": sess.id,
        "demo": sess.agent.settings.demo_mode,
        "workspace": _workspace_ref(ws),
    }


@router.post("/api/sessions/{session_id}/workspace")
def bind_session_workspace(session_id: str, body: WorkspaceSet) -> dict[str, Any]:
    """Pin one chat to a folder. Other chats keep the folder they already have."""
    sess = require_session(session_id)
    target = (body.path or body.name or "").strip()
    if not target:
        raise HTTPException(400, "缺少项目路径")
    try:
        STORE.bind_workspace(sess, target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.persist(sess.id)
    ws = str(getattr(sess.agent.settings, "workspace", "") or "")
    return {"id": sess.id, "workspace": _workspace_ref(ws)}


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    sess = require_session(session_id)
    schemas = sess.agent.registry.schemas()
    budget = context_budget_tokens(sess.agent.messages, schemas)
    snap = STORE.runtime_snapshot(sess)
    # The in-memory agent knows about workers already spawned in this turn.
    # Prefer it over the prior persisted tree while a stopped worker unwinds.
    live_tree = sess.agent.canvas_tree()
    ws = str(getattr(sess.agent.settings, "workspace", "") or "")
    return {
        "id": sess.id,
        "title": sess.title,
        "workspace": _workspace_ref(ws),
        "messages": STORE.ui_messages(sess),
        "tokens": budget,
        "messages_tokens": messages_tokens(sess.agent.messages),
        "schemas_tokens": schemas_tokens(schemas),
        "limit": sess.agent.settings.context_limit,
        "tools": sess.agent.registry.names(),
        "demo": sess.agent.settings.demo_mode,
        "agent_tree": live_tree or getattr(sess, "agent_tree", None) or [],
        **snap,
    }


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    if not STORE.delete(session_id):
        raise HTTPException(404, "session not found")
    from ...services.scheduler import SCHEDULES
    from ...services.tenant_context import get_user_id

    SCHEDULES.pause_for_session(get_user_id(), session_id)
    return {"status": "ok", "session_id": session_id}


@router.post("/api/sessions/{session_id}/truncate")
def truncate_session(session_id: str, body: TruncateBody) -> dict[str, Any]:
    require_session(session_id)
    file_undo: dict[str, Any] | None = None
    if body.restore_files:
        from ...services import fs_undo

        try:
            file_undo = fs_undo.undo_to_turn(session_id, body.keep_user_turns)
        except Exception as exc:
            raise HTTPException(500, f"file restore failed: {exc}") from exc
    try:
        ok = STORE.truncate_before_user_turn(session_id, body.keep_user_turns)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "session not found")
    sess = STORE.get(session_id)
    out: dict[str, Any] = {
        "status": "ok",
        "session_id": session_id,
        "keep_user_turns": body.keep_user_turns,
        "messages": len(sess.agent.messages) if sess else 0,
        "restore_files": body.restore_files,
    }
    if file_undo is not None:
        out["file_undo"] = file_undo
    return out


@router.post("/api/sessions/{session_id}/replay")
def replay_session(session_id: str, body: ReplayBody) -> dict[str, Any]:
    """Restore files to a turn's confirmation point and drop that turn's work.

    The client re-sends ``user_text`` so the agent re-executes the plan.
    """
    require_session(session_id)
    from ...services import fs_undo

    user_text = fs_undo.turn_user_text(session_id, body.user_turn)
    file_undo: dict[str, Any] | None = None
    if body.restore_files:
        try:
            file_undo = fs_undo.undo_to_turn(session_id, body.user_turn)
        except Exception as exc:
            raise HTTPException(500, f"file restore failed: {exc}") from exc
    try:
        ok = STORE.truncate_before_user_turn(session_id, body.user_turn)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "session not found")
    sess = STORE.get(session_id)
    out: dict[str, Any] = {
        "status": "ok",
        "session_id": session_id,
        "user_turn": body.user_turn,
        "keep_user_turns": body.user_turn,
        "user_text": user_text,
        "messages": len(sess.agent.messages) if sess else 0,
        "restore_files": body.restore_files,
    }
    if file_undo is not None:
        out["file_undo"] = file_undo
    return out


@router.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: str) -> dict[str, Any]:
    if not STORE.stop(session_id):
        raise HTTPException(404, "session not found")
    return {"status": "ok", "session_id": session_id}


@router.post("/api/sessions/{session_id}/approvals/{approval_id}")
def decide_approval(session_id: str, approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    require_session(session_id)
    STORE.decide_approval(
        session_id,
        approval_id,
        body.approved,
        remember=bool(body.remember),
        patch_args=body.patch_args,
    )
    return {
        "status": "ok",
        "approval_id": approval_id,
        "approved": body.approved,
        "remember": bool(body.remember) and bool(body.approved),
    }


@router.post("/api/sessions/{session_id}/asks/{ask_id}")
def answer_ask(session_id: str, ask_id: str, body: AskAnswer) -> dict[str, Any]:
    require_session(session_id)
    choice = (body.choice or "").strip()
    if not choice and not (body.text or "").strip():
        raise HTTPException(400, "choice or text required")
    STORE.answer_ask(
        session_id,
        ask_id,
        choice=choice or "custom",
        text=body.text or "",
        option_label=body.option_label or "",
    )
    return {
        "status": "ok",
        "ask_id": ask_id,
        "choice": choice or "custom",
    }


@router.post("/api/sessions/{session_id}/plans/{plan_id}")
def confirm_plan(session_id: str, plan_id: str, body: PlanConfirm) -> dict[str, Any]:
    require_session(session_id)
    STORE.decide_plan(
        session_id,
        plan_id,
        bool(body.approved),
        summary=body.summary,
        tasks=body.tasks,
    )
    return {
        "status": "ok",
        "plan_id": plan_id,
        "approved": bool(body.approved),
    }


@router.post("/api/sessions/{session_id}/save")
def api_save(session_id: str) -> dict[str, str]:
    path = STORE.persist(session_id)
    if not path:
        raise HTTPException(404, "session not found")
    return {"path": path}
