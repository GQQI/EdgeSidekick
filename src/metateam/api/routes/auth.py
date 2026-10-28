"""Auth routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...services.local_auth import TOKEN_HEADER
from ...services.store import STORE
from ...services.user_auth import (
    auth_status,
    create_user,
    get_user,
    list_users,
    login as user_login,
    multi_user_enabled,
    register_account,
    revoke_token,
    setup_admin,
    update_profile,
)
from ..http import require_loopback
from ..schemas import AuthCreateUserBody, AuthLoginBody, AuthProfileBody, AuthSetupBody

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def api_auth_status(request: Request) -> dict[str, Any]:
    require_loopback(request)
    status = auth_status()
    user = None
    uid = getattr(request.state, "user_id", None)
    if uid:
        record = get_user(uid)
        user = record.public() if record else {"id": uid, "username": getattr(request.state, "username", "")}
    return {**status, "user": user, "authenticated": bool(user)}


@router.post("/setup")
def api_auth_setup(body: AuthSetupBody, request: Request) -> dict[str, Any]:
    require_loopback(request)
    try:
        user, token = setup_admin(body.username, body.password, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.refresh_settings(rebind_llm=True, rebind_workspace=True)
    return {
        "status": "ok",
        "token": token,
        "token_header": "X-Sidekick-Token",
        "user": user.public(),
    }


@router.post("/register")
def api_auth_register(body: AuthSetupBody, request: Request) -> dict[str, Any]:
    """Open sign-up. The first account initializes the instance; later ones join it."""
    require_loopback(request)
    try:
        user, token = register_account(body.username, body.password, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.refresh_settings(rebind_llm=True, rebind_workspace=True)
    return {
        "status": "ok",
        "token": token,
        "token_header": "X-Sidekick-Token",
        "user": user.public(),
    }


@router.post("/login")
def api_auth_login(body: AuthLoginBody, request: Request) -> dict[str, Any]:
    require_loopback(request)
    try:
        email = (body.email or "").strip() or (body.username or "").strip()
        user, token = user_login(email=email, password=body.password)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {
        "status": "ok",
        "token": token,
        "token_header": "X-Sidekick-Token",
        "user": user.public(),
    }


@router.post("/logout")
def api_auth_logout(request: Request) -> dict[str, Any]:
    token = request.headers.get(TOKEN_HEADER) or request.headers.get("X-Sidekick-Token")
    revoke_token(token)
    return {"status": "ok"}


@router.get("/me")
def api_auth_me(request: Request) -> dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "not authenticated")
    record = get_user(uid)
    if record:
        return record.public()
    return {"id": uid, "username": getattr(request.state, "username", "") or ""}


@router.patch("/profile")
def api_auth_profile(body: AuthProfileBody, request: Request) -> dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "not authenticated")
    try:
        user = update_profile(
            uid,
            display_name=body.display_name,
            email=body.email,
            title=body.title,
            bio=body.bio,
            avatar=body.avatar,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "user": user.public()}


@router.get("/users")
def api_auth_users() -> dict[str, Any]:
    return {"items": [u.public() for u in list_users()]}


@router.post("/users")
def api_auth_create_user(body: AuthCreateUserBody) -> dict[str, Any]:
    if not multi_user_enabled():
        raise HTTPException(400, "complete setup first")
    try:
        user = create_user(body.username, body.password, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "user": user.public()}
