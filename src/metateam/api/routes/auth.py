"""Auth routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...services.local_auth import TOKEN_HEADER
from ...services.store import STORE
from ...services.user_auth import (
    auth_status,
    create_user,
    list_users,
    login as user_login,
    multi_user_enabled,
    register_account,
    revoke_token,
    setup_admin,
)
from ..http import require_loopback
from ..schemas import AuthCreateUserBody, AuthLoginBody, AuthSetupBody

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def api_auth_status(request: Request) -> dict[str, Any]:
    require_loopback(request)
    status = auth_status()
    user = None
    uid = getattr(request.state, "user_id", None)
    uname = getattr(request.state, "username", None)
    if uid and uname:
        user = {"id": uid, "username": uname}
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


_REGISTER_MSG = {
    "username too short": "显示名至少 2 个字符",
    "password must be at least 6 characters": "密码至少 6 位",
    "username taken": "这个显示名已被使用",
    "email already registered": "这个邮箱已经注册",
    "invalid email": "请输入有效邮箱",
}


@router.post("/register")
def api_auth_register(body: AuthSetupBody, request: Request) -> dict[str, Any]:
    """Open registration. No existing login is required."""
    require_loopback(request)
    try:
        user, token = register_account(body.username, body.password, email=body.email)
    except ValueError as exc:
        raise HTTPException(400, _REGISTER_MSG.get(str(exc), str(exc))) from exc
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
    uname = getattr(request.state, "username", None)
    if not uid:
        raise HTTPException(401, "not authenticated")
    return {"id": uid, "username": uname or ""}


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
