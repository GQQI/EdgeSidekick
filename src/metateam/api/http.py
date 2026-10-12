"""Shared HTTP helpers for API routers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NoReturn, Optional

from fastapi import HTTPException, Request

from ..services.local_auth import peer_is_loopback
from ..services.store import STORE, ChatSession


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if not peer_is_loopback(host):
        raise HTTPException(403, "loopback only")


def require_session(session_id: str) -> ChatSession:
    sess = STORE.get(session_id)
    if not sess:
        raise HTTPException(404, "session not found")
    return sess


def raise_fs_http(exc: BaseException) -> NoReturn:
    """Map filesystem / path errors to HTTP status codes."""
    from ..services.fs_api import describe_fs_error

    detail = describe_fs_error(exc)
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(404, detail) from exc
    if isinstance(exc, FileExistsError):
        raise HTTPException(409, detail) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, detail) from exc
    if isinstance(exc, OSError):
        raise HTTPException(500, detail) from exc
    raise HTTPException(500, detail) from exc


@contextmanager
def workspace_override(workspace: Optional[str]) -> Iterator[None]:
    """Point file/git panel calls at a specific open workspace for one request.

    Several chats may be pinned to different folders at once; the file and
    git side panels pass the workspace of whichever chat is currently
    focused (or being previewed) instead of always using the tenant's single
    "default" workspace.
    """
    raw = (workspace or "").strip()
    if not raw:
        yield
        return
    from ..services.fs_api import bind_active_workspace, reset_active_workspace

    try:
        folder = Path(raw).expanduser().resolve()
    except Exception:
        # Soft-fallback like resolve_panel_workspace: a deleted/missing chat
        # folder must not break the file panel mid-request.
        yield
        return
    if not folder.is_dir():
        yield
        return
    token = bind_active_workspace(folder)
    try:
        yield
    finally:
        reset_active_workspace(token)


def call_fs(fn, *args: Any, workspace: Optional[str] = None, **kwargs: Any) -> Any:
    try:
        with workspace_override(workspace):
            return fn(*args, **kwargs)
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        raise_fs_http(exc)


def resolve_panel_workspace(workspace: Optional[str]) -> Path:
    """Absolute root for git panel calls — falls back to the default workspace."""
    from ..core.config import get_settings

    raw = (workspace or "").strip()
    if raw:
        try:
            folder = Path(raw).expanduser().resolve()
            if folder.is_dir():
                return folder
        except Exception:
            pass
    return Path(get_settings().workspace)


def git_result_or_400(result: str) -> str:
    if str(result).startswith("ERROR"):
        raise HTTPException(400, result)
    return result
