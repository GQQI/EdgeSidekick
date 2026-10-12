"""Skill package list / import / edit / validate."""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ...core.config import get_settings
from ...runtime.tools import skill_tool_name
from ...services.skills import (
    compose_skill_markdown,
    create_skill_package_entry,
    delete_skill,
    delete_skill_package_file,
    find_skill,
    import_skill_dir,
    import_skill_markdown,
    list_skill_package_files,
    load_skills,
    read_skill_package_file,
    skill_package_dir,
    validate_skill_text,
    write_skill,
    write_skill_package_file,
)
from ..http import require_loopback
from ..schemas import (
    SkillFileCreateBody,
    SkillFileWriteBody,
    SkillImportDirBody,
    SkillValidateBody,
    SkillWriteBody,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _public(sk: Any) -> dict[str, Any]:
    s = get_settings()
    folder = ""
    try:
        folder = sk.path.parent.relative_to(s.skills_dir).as_posix()
    except Exception:
        folder = sk.path.parent.name
    return {
        "name": sk.name,
        "tool": skill_tool_name(sk.name),
        "description": sk.description,
        "path": str(sk.path),
        "folder": folder,
        "mode": "function_call",
        "writable": True,
    }


@router.get("")
def api_skills() -> list[dict[str, Any]]:
    s = get_settings()
    return [_public(sk) for sk in load_skills(s.skills_dir)]


@router.post("/validate")
def api_skill_validate(body: SkillValidateBody) -> dict[str, Any]:
    text = (body.markdown or "").strip()
    if not text:
        text = compose_skill_markdown(body.name, body.description, body.content)
    return validate_skill_text(text)


@router.post("/import")
async def api_skill_import(
    file: UploadFile = File(...),
    overwrite: bool = False,
) -> dict[str, Any]:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    if len(raw) > 5_000_000:
        raise HTTPException(400, "file too large (5MB)")
    filename = (file.filename or "skill.md").strip()
    s = get_settings()
    try:
        if filename.lower().endswith(".zip"):
            imported = _import_zip(s.skills_dir, raw, overwrite=overwrite)
        else:
            text = raw.decode("utf-8")
            imported = [
                import_skill_markdown(
                    s.skills_dir,
                    text,
                    overwrite=overwrite,
                    fallback_name=Path(filename).stem,
                )
            ]
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "file is not UTF-8 text") from exc
    return {"status": "ok", "imported": [_public(sk) for sk in imported]}


@router.post("/import-dir")
def api_skill_import_dir(request: Request, body: SkillImportDirBody) -> dict[str, Any]:
    require_loopback(request)
    s = get_settings()
    try:
        imported = import_skill_dir(
            s.skills_dir, Path(body.path), overwrite=body.overwrite
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "imported": [_public(sk) for sk in imported]}


@router.post("/import-files")
async def api_skill_import_files(
    files: list[UploadFile] = File(...),
    overwrite: bool = False,
) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "no files")
    if len(files) > 80:
        raise HTTPException(400, "too many files")
    s = get_settings()
    with tempfile.TemporaryDirectory(prefix="skill-import-") as tmp:
        tmp_path = Path(tmp)
        total = 0
        for item in files:
            rel = (item.filename or "").replace("\\", "/").lstrip("/")
            if not rel or ".." in Path(rel).parts:
                raise HTTPException(400, f"unsafe path: {item.filename}")
            raw = await item.read()
            total += len(raw)
            if total > 8_000_000:
                raise HTTPException(400, "package too large")
            dest = tmp_path / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        try:
            imported = import_skill_dir(s.skills_dir, tmp_path, overwrite=overwrite)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "imported": [_public(sk) for sk in imported]}


@router.post("")
def api_skill_create(body: SkillWriteBody) -> dict[str, Any]:
    s = get_settings()
    try:
        sk = write_skill(
            s.skills_dir,
            name=body.name,
            description=body.description,
            content=body.content,
            overwrite=body.overwrite,
            previous=body.previous,
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "skill": _public(sk)}


@router.get("/{name}")
def api_skill(name: str) -> dict[str, Any]:
    s = get_settings()
    sk = find_skill(s.skills_dir, name)
    if sk is None:
        raise HTTPException(404, "skill not found")
    root = skill_package_dir(sk)
    return {
        **_public(sk),
        "body": sk.read_body(),
        "root": str(root),
        "files": list_skill_package_files(sk),
    }


@router.put("/{name}")
def api_skill_update(name: str, body: SkillWriteBody) -> dict[str, Any]:
    s = get_settings()
    if find_skill(s.skills_dir, name) is None:
        raise HTTPException(404, "skill not found")
    try:
        sk = write_skill(
            s.skills_dir,
            name=body.name or name,
            description=body.description,
            content=body.content,
            overwrite=True,
            previous=name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "skill": {**_public(sk), "body": sk.read_body()}}



@router.get("/{name}/files/{file_path:path}")
def api_skill_file(name: str, file_path: str) -> dict[str, Any]:
    s = get_settings()
    sk = find_skill(s.skills_dir, name)
    if sk is None:
        raise HTTPException(404, "skill not found")
    try:
        return read_skill_package_file(sk, file_path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "file not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{name}/files/{file_path:path}")
def api_skill_file_put(
    name: str, file_path: str, body: SkillFileWriteBody
) -> dict[str, Any]:
    s = get_settings()
    sk = find_skill(s.skills_dir, name)
    if sk is None:
        raise HTTPException(404, "skill not found")
    try:
        result = write_skill_package_file(
            sk, file_path, body.content, skills_dir=s.skills_dir
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "file not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", **result}




@router.post("/{name}/files")
def api_skill_file_create(name: str, body: SkillFileCreateBody) -> dict[str, Any]:
    s = get_settings()
    sk = find_skill(s.skills_dir, name)
    if sk is None:
        raise HTTPException(404, "skill not found")
    try:
        result = create_skill_package_entry(
            sk,
            body.path,
            entry_type=body.type,
            content=body.content or "",
            skills_dir=s.skills_dir,
        )
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", **result}


@router.delete("/{name}/files/{file_path:path}")
def api_skill_file_delete(name: str, file_path: str) -> dict[str, Any]:
    s = get_settings()
    sk = find_skill(s.skills_dir, name)
    if sk is None:
        raise HTTPException(404, "skill not found")
    try:
        result = delete_skill_package_file(
            sk, file_path, skills_dir=s.skills_dir
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "file not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", **result}


@router.delete("/{name}")
def api_skill_delete(name: str) -> dict[str, Any]:
    s = get_settings()
    try:
        removed = delete_skill(s.skills_dir, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, "skill not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "name": removed}


def _import_zip(skills_dir: Path, raw: bytes, *, overwrite: bool) -> list[Any]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid zip") from exc
    with zf, tempfile.TemporaryDirectory(prefix="skill-zip-") as tmp:
        tmp_path = Path(tmp)
        has_skill = False
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("/")
            if not name or ".." in Path(name).parts:
                raise ValueError(f"unsafe zip path: {name}")
            if Path(name).name.lower() == "skill.md":
                has_skill = True
            data = zf.read(info)
            dest = tmp_path / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if not has_skill:
            raise ValueError("zip has no SKILL.md")
        return import_skill_dir(skills_dir, tmp_path, overwrite=overwrite)
