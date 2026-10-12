"""Workspace selection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...services.folder_picker import pick_folder
from ...services.store import STORE
from ...services.workspace_store import (
    create_workspace,
    forget_recent,
    get_active_workspace,
    is_configured,
    list_workspaces,
    remember_recent,
    resolve_workspace_path,
    set_workspace,
)
from ..http import require_loopback
from ..schemas import WorkspaceCreate, WorkspaceSet

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@router.get("")
def api_workspaces() -> dict[str, Any]:
    configured = is_configured()
    active = get_active_workspace()
    return {
        "configured": configured,
        "items": list_workspaces(),
        "active": active if configured else None,
    }


@router.post("")
def api_workspaces_create(body: WorkspaceCreate) -> dict[str, Any]:
    target = (body.path or body.name or "").strip()
    if not target:
        raise HTTPException(400, "path required")
    try:
        active = create_workspace(target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.refresh_settings(rebind_llm=False, rebind_workspace=False)
    return {
        "status": "ok",
        "configured": True,
        "active": active,
        "items": list_workspaces(),
    }


@router.post("/remember")
def api_workspaces_remember(body: WorkspaceSet) -> dict[str, Any]:
    """Pin a folder into the recent list without changing the active workspace."""
    target = (body.path or body.name or "").strip()
    if not target:
        raise HTTPException(400, "path required")
    try:
        folder = resolve_workspace_path(target)
        remember_recent(folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "items": list_workspaces(), "active": get_active_workspace()}


@router.put("/active")
def api_workspaces_set(body: WorkspaceSet) -> dict[str, Any]:
    target = (body.path or body.name or "").strip()
    if not target:
        raise HTTPException(400, "path required")
    try:
        active = set_workspace(target, create=body.create)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.refresh_settings(rebind_llm=False, rebind_workspace=False)
    return {
        "status": "ok",
        "configured": True,
        "active": active,
        "items": list_workspaces(),
    }


@router.delete("")
def api_workspaces_forget(path: str) -> dict[str, Any]:
    target = (path or "").strip()
    if not target:
        raise HTTPException(400, "path required")
    try:
        items = forget_recent(target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "status": "ok",
        "configured": is_configured(),
        "active": get_active_workspace(),
        "items": items,
    }


@router.post("/browse")
def api_workspaces_browse(request: Request) -> dict[str, Any]:
    require_loopback(request)
    path = pick_folder(title="选择工作区文件夹")
    if not path:
        return {"cancelled": True, "path": None}
    return {"cancelled": False, "path": path}
