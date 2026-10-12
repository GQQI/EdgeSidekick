"""Book PDF/EPUB -> Skill async compile API (offline + Sidekick LLM)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ...services.book_to_skill import BOOK_SKILL_JOBS, enqueue_book_to_skill
from ...services.book_to_skill.pipeline import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/api/book-to-skill", tags=["book-to-skill"])


@router.get("")
def api_book_to_skill_list(limit: int = 50) -> dict[str, Any]:
    jobs = [j.snapshot() for j in BOOK_SKILL_JOBS.list(limit=limit)]
    active = sum(1 for j in jobs if j.get("status") in {"pending", "extracting", "distilling", "validating"})
    return {"jobs": jobs, "active": active}


@router.post("")
async def api_book_to_skill_create(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    overwrite: bool = Form(False),
) -> dict[str, Any]:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)")
    filename = (file.filename or "book.pdf").strip()
    try:
        job = enqueue_book_to_skill(
            filename=filename,
            raw=raw,
            skill_name=(name or "").strip(),
            overwrite=bool(overwrite),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "ok", "job": job.snapshot()}


@router.get("/{job_id}")
def api_book_to_skill_status(job_id: str) -> dict[str, Any]:
    job = BOOK_SKILL_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    return job.snapshot()


@router.post("/{job_id}/cancel")
def api_book_to_skill_cancel(job_id: str) -> dict[str, Any]:
    job = BOOK_SKILL_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    if job.status in {"installed", "failed"}:
        return job.snapshot()
    job.request_cancel()
    return job.snapshot()
