"""Workspace filesystem + undo."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ...services import fs_api, fs_undo
from ..http import call_fs, require_loopback, raise_fs_http, workspace_override
from ..schemas import FileCreate, FileMove, FileRename, FileReveal, FileWrite, UndoBody

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("")
def api_files_list(path: str = ".", workspace: str | None = None) -> dict[str, Any]:
    return call_fs(fs_api.list_entries, path, workspace=workspace)


@router.get("/search")
def api_files_search(q: str = "", path: str = ".", workspace: str | None = None) -> dict[str, Any]:
    return call_fs(fs_api.search_workspace, q, path=path, workspace=workspace)


@router.post("/upload")
async def api_files_upload(
    request: Request,
    file: UploadFile = File(...),
    dest: str | None = Form(None),
    workspace: str | None = Form(None),
) -> dict[str, Any]:
    """Save an uploaded browser file into the selected workspace.

    `dest` may be a directory (`docs` / `.`) or a full relative file path (`docs/a.pdf`).
    Empty / `.` dest writes into the workspace root.
    `workspace` may arrive as multipart form and/or `?workspace=` query.
    """
    ws = (workspace or request.query_params.get("workspace") or "").strip() or None
    raw_name = (file.filename or "upload.bin").replace("\\", "/").split("/")[-1]
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in raw_name).strip() or "upload.bin"
    dest_raw = (dest or "").replace("\\", "/").strip().strip("/")
    if dest_raw in ("", "."):
        rel = safe
    elif dest_raw.endswith("/") or ("." not in dest_raw.split("/")[-1]):
        rel = f"{dest_raw}/{safe}"
    else:
        rel = dest_raw
    # normalize ..
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "invalid dest")
    rel = "/".join(parts)
    try:
        data = await file.read()
        with workspace_override(ws):
            meta = fs_api.write_bytes(rel, data)
            try:
                preview = fs_api.read_file(meta["path"])
                return {**preview, "uploaded": True}
            except Exception:
                return {**meta, "uploaded": True, "kind": "unsupported"}
    except ValueError as exc:
        raise_fs_http(exc)

@router.get("/content")
def api_files_read(path: str, workspace: str | None = None) -> dict[str, Any]:
    return call_fs(fs_api.read_file, path, workspace=workspace)


@router.get("/raw")
def api_files_raw(path: str, workspace: str | None = None) -> FileResponse:
    try:
        with workspace_override(workspace):
            fp = fs_api.safe_resolve(path)
    except ValueError as exc:
        raise_fs_http(exc)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, f"not found: {path}")
    return FileResponse(
        path=fp,
        media_type=fs_api.guess_mime(fp),
        filename=fp.name,
        content_disposition_type="inline",
    )


@router.put("/content")
def api_files_write(body: FileWrite) -> dict[str, Any]:
    return call_fs(fs_api.write_text, body.path, body.content, workspace=body.workspace)


@router.post("")
def api_files_create(body: FileCreate) -> dict[str, Any]:
    kind = body.kind if body.kind in ("file", "dir") else "file"
    return call_fs(fs_api.create_entry, body.path, kind, workspace=body.workspace)


@router.delete("")
def api_files_delete(path: str, recursive: bool = False, workspace: str | None = None) -> dict[str, Any]:
    return call_fs(
        fs_api.delete_entry,
        path,
        recursive=recursive,
        workspace=workspace,
        source="explorer",
    )


@router.post("/rename")
def api_files_rename(body: FileRename) -> dict[str, Any]:
    return call_fs(fs_api.rename_entry, body.path, body.new_name, workspace=body.workspace)


@router.post("/move")
def api_files_move(body: FileMove) -> dict[str, Any]:
    return call_fs(fs_api.move_entry, body.path, body.dest_dir, workspace=body.workspace)


@router.post("/reveal")
def api_files_reveal(request: Request, body: FileReveal) -> dict[str, Any]:
    require_loopback(request)
    return call_fs(fs_api.reveal_in_os, body.path, workspace=body.workspace)


@router.get("/undo")
def api_files_undo_status(
    session_id: str = "",
    workspace: str | None = None,
) -> dict[str, Any]:
    return call_fs(
        fs_undo.status,
        session_id=session_id or None,
        workspace=workspace,
    )


@router.post("/undo")
def api_files_undo(body: UndoBody = UndoBody()) -> dict[str, Any]:
    entry_id = str(body.id or "").strip()
    sid = str(body.session_id or "").strip() or None
    ws = str(body.workspace or "").strip() or None
    if entry_id:
        return call_fs(fs_undo.undo_to_id, entry_id, session_id=sid, workspace=ws)
    return call_fs(fs_undo.undo_latest_turn, session_id=sid, workspace=ws)
