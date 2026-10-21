"""Health, bootstrap, model, MCP, skills, memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from ...core.hostinfo import network_available

from ...core.config import get_settings
from ...services.mcp_config import McpServerConfig, load_mcp_config, update_mcp_config
from ...services import mcp_catalog
from ...services.mcp_runtime import test_server as mcp_test_server
from ...services.memory import (
    library_from_payload,
    load_library,
    read_memory,
    save_library,
    write_memory,
)
from ...services.model_config import load_model_config, select_model_role, update_model_config
from ...services.app_prefs import auto_approve_tools, load_app_prefs, save_app_prefs
from ...services.local_auth import get_token
from ...services.store import STORE
from ...services.user_auth import auth_status
from ...services.workspace_store import get_active_workspace
from ..http import require_loopback
from ..schemas import (
    MemoryLibraryUpdate,
    McpCatalogInstallBody,
    McpCatalogUninstallBody,
    McpTestBody,
    McpUpdateBody,
    MemoryUpdate,
    ModelSelect,
    ModelUpdate,
    PrefsUpdate,
)

router = APIRouter(tags=["system"])


class SpeakJudgeBody(BaseModel):
    name: str = ""
    duty: str = ""
    user_text: str = ""
    prior: list[dict[str, str]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    addressed: bool = False
    greeting: bool = False
    capability: bool = False
    context: str = ""
    workspace: str = ""


@router.post("/api/social/speak-judge")
def api_speak_judge(body: SpeakJudgeBody) -> dict[str, Any]:
    from ...services.systemone import judge_speak

    return judge_speak(
        name=body.name,
        duty=body.duty,
        user_text=body.user_text,
        prior=body.prior,
        history=body.history,
        addressed=body.addressed,
        greeting=body.greeting,
        capability=body.capability,
        context=body.context,
        workspace=body.workspace,
    )


@router.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    active = get_active_workspace()
    configured = bool(active.get("configured"))
    demo = bool(s.demo_mode)
    try:
        cfg = load_model_config()
        demo = not cfg.any_key_set()
    except Exception:
        pass
    return {
        "ok": True,
        "online": network_available(),
        "demo": demo,
        "model": s.model,
        "base_url": s.base_url,
        "provider": getattr(s, "provider", ""),
        "workspace": str(active.get("path") or ""),
        "workspace_configured": configured,
        "thinking_enabled": getattr(s, "thinking_enabled", False),
        "reasoning_effort": getattr(s, "reasoning_effort", ""),
        "context_limit": s.context_limit,
        "compress_trigger_ratio": s.compress_trigger_ratio,
        "allow_shell": bool(getattr(s, "allow_shell", False)),
        "shell_sandbox": bool(getattr(s, "shell_sandbox", True)),
        "mcp_enabled": bool(getattr(s, "mcp_enabled", True)),
        "auto_approve_tools": auto_approve_tools(),
        "auth_register": True,
    }


@router.get("/api/prefs")
def get_prefs() -> dict[str, Any]:
    return load_app_prefs()


@router.put("/api/prefs")
def put_prefs(body: PrefsUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = save_app_prefs(patch)
    if "auto_approve_tools" in patch:
        STORE.apply_auto_approve_tools(bool(cfg.get("auto_approve_tools")))
    return {"status": "ok", **cfg}


@router.get("/api/bootstrap")
def bootstrap(request: Request) -> dict[str, Any]:
    require_loopback(request)
    status = auth_status()
    if status["needs_setup"]:
        return {
            **status,
            "token": get_token(),
            "token_header": "X-Sidekick-Token",
            "auth_required": False,
        }
    return {
        **status,
        "token": None,
        "token_header": "X-Sidekick-Token",
        "auth_required": True,
    }


@router.get("/api/mcp")
def api_mcp_get() -> dict[str, Any]:
    return load_mcp_config().public_dict()


@router.put("/api/mcp")
def api_mcp_put(body: McpUpdateBody) -> dict[str, Any]:
    try:
        setup = update_mcp_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", **setup.public_dict()}


@router.post("/api/mcp/test")
def api_mcp_test(body: McpTestBody) -> dict[str, Any]:
    server = McpServerConfig(
        id=body.id or "test",
        name=body.name or body.id or "test",
        transport=body.transport or "stdio",
        command=body.command or "",
        args=list(body.args or []),
        env=dict(body.env or {}),
        url=body.url or "",
        headers=dict(body.headers or {}),
        enabled=True,
    )
    return mcp_test_server(server)


@router.get("/api/mcp/catalog")
def api_mcp_catalog() -> dict[str, Any]:
    return mcp_catalog.list_catalog()


@router.post("/api/mcp/catalog/install")
def api_mcp_catalog_install(body: McpCatalogInstallBody) -> dict[str, Any]:
    try:
        return mcp_catalog.install_from_catalog(
            body.id,
            args=body.args,
            env=dict(body.env or {}),
        )
    except ValueError as exc:
        raise HTTPException(404 if "没有" in str(exc) else 400, str(exc)) from exc


@router.post("/api/mcp/catalog/uninstall")
def api_mcp_catalog_uninstall(body: McpCatalogUninstallBody) -> dict[str, Any]:
    try:
        return mcp_catalog.uninstall_from_catalog(body.id)
    except ValueError as exc:
        raise HTTPException(404 if "没有" in str(exc) else 400, str(exc)) from exc


@router.get("/api/model")
def get_model() -> dict[str, Any]:
    return load_model_config().masked()


@router.put("/api/model")
def put_model(body: ModelUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = update_model_config(patch)
    STORE.refresh_settings(rebind_llm=True, rebind_workspace=False)
    mode = "demo" if cfg.demo_mode else "api"
    _, model_name, _, _ = cfg.resolve(cfg.main)
    return {
        "status": "ok",
        "config": cfg.masked(),
        "note": (
            "已保存（Demo 模式）。"
            if cfg.demo_mode
            else f"已保存并生效（{mode} · {model_name or 'model'}）。"
        ),
    }


@router.patch("/api/model/select")
def patch_model_select(body: ModelSelect) -> dict[str, Any]:
    try:
        cfg = select_model_role(body.role, body.provider_id, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    STORE.refresh_settings(rebind_llm=True, rebind_workspace=False)
    _, model_name, _, _ = cfg.resolve(
        cfg.main if body.role == "main" else cfg.subagent if body.role == "subagent" else cfg.compress
    )
    return {
        "status": "ok",
        "config": cfg.masked(),
        "note": f"已切换 {body.role} → {model_name}",
    }


@router.get("/api/memory")
def api_memory() -> dict[str, str]:
    s = get_settings()
    return {"content": read_memory(s.memory_file, max_chars=100_000)}


@router.put("/api/memory")
def api_memory_put(body: MemoryUpdate) -> dict[str, str]:
    s = get_settings()
    write_memory(s.memory_file, body.content)
    return {"status": "ok"}


@router.get("/api/memory/library")
def api_memory_library() -> dict[str, Any]:
    s = get_settings()
    return load_library(s.memory_file).to_dict()


@router.put("/api/memory/library")
def api_memory_library_put(body: MemoryLibraryUpdate) -> dict[str, Any]:
    s = get_settings()
    lib = library_from_payload({"version": body.version, "categories": body.categories})
    saved = save_library(s.memory_file, lib)
    return {"status": "ok", "library": saved.to_dict()}
