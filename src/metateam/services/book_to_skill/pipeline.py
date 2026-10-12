"""Orchestrate extract -> structure -> distill -> validate -> install."""

from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from ...core.config import ROOT, SRC_ROOT, get_process_settings
from ..skills import find_skill, slug_skill_name
from ..tenant_context import (
    get_user_id,
    reset_user,
    set_user,
    tenant_skills_dir,
)
from .distill import CompleteFn, distill_chapters
from .extract import SUPPORTED_BOOK_SUFFIXES, extract_document
from .jobs import BOOK_SKILL_JOBS, BookSkillJob
from .structure import structure_chapters
from .writer import write_skill_package

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fff\-]+")


def sanitize_upload_filename(name: str) -> str:
    base = Path(name or "book.pdf").name
    base = base.replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "book.pdf"
    if len(cleaned) > 180:
        stem = Path(cleaned).stem[:140]
        suf = Path(cleaned).suffix[:20]
        cleaned = stem + suf
    return cleaned


def suggest_skill_name(filename: str, override: str = "") -> str:
    if override and slug_skill_name(override):
        return slug_skill_name(override)
    stem = Path(filename).stem
    slug = slug_skill_name(stem)
    if len(slug) >= 2:
        return slug
    import hashlib

    digest = hashlib.sha1(stem.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"book-{digest}"


def jobs_root() -> Path:
    """Job scratch under process data_dir (uploads/temp), not the skills library."""
    root = get_process_settings().data_dir / "book_to_skill_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stable_user_data_root() -> Path:
    """Desktop-compatible personal data root when SIDEKICK_DATA_DIR is unset.

    Matches Electron userData: %APPDATA%/EdgeSidekick (see desktop/main.mjs).
    """
    env = (os.environ.get("SIDEKICK_DATA_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "EdgeSidekick"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "EdgeSidekick"


def _safe_uid(user_id: str) -> str:
    uid = (user_id or "default").strip() or "default"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in uid)[:64] or "default"


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def install_skills_dir(user_id: Optional[str] = None) -> Path:
    """Personal/tenant skills library for book-to-skill installs.

    Never writes into the repo bundled tree ``SRC_ROOT/skills``.

    * Packaged / ``SIDEKICK_DATA_DIR`` set: tenant skills under that data root
      (same path ``apply_knowledge_to_settings`` binds to ``settings.skills_dir``).
    * Dev with ``ROOT == SRC_ROOT``: prefer ``%APPDATA%/EdgeSidekick/...`` (Electron
      userData). Caller may mirror into the runtime tenant dir for list/load.
    """
    uid = user_id or get_user_id()
    bundled = SRC_ROOT / "skills"

    if ROOT.resolve() != SRC_ROOT.resolve():
        dest = tenant_skills_dir(uid)
    else:
        # Source-tree layout without SIDEKICK_DATA_DIR: do not treat repo as store.
        dest = _stable_user_data_root() / "data" / "tenants" / _safe_uid(uid) / "skills"

    # Hard guard: project/repo skills tree is always wrong.
    if _is_under(dest, bundled) or dest.resolve() == bundled.resolve():
        dest = _stable_user_data_root() / "data" / "tenants" / _safe_uid(uid) / "skills"

    dest.mkdir(parents=True, exist_ok=True)
    return dest


def runtime_skills_mirror_dir(user_id: Optional[str] = None) -> Optional[Path]:
    """When install dir is APPDATA but runtime still lists ROOT/tenants, return mirror target."""
    uid = user_id or get_user_id()
    install = install_skills_dir(uid)
    runtime = tenant_skills_dir(uid)
    bundled = SRC_ROOT / "skills"
    try:
        if install.resolve() == runtime.resolve():
            return None
        if _is_under(runtime, bundled) or runtime.resolve() == bundled.resolve():
            return None
    except OSError:
        return None
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def _assert_installable(slug: str, *, overwrite: bool, user_id: str) -> Path:
    dest_root = install_skills_dir(user_id)
    if not overwrite:
        if find_skill(dest_root, slug) is not None:
            raise FileExistsError(f"skill already exists: {slug}")
        mirror = runtime_skills_mirror_dir(user_id)
        if mirror is not None and find_skill(mirror, slug) is not None:
            raise FileExistsError(f"skill already exists: {slug}")
    return dest_root


def enqueue_book_to_skill(
    *,
    filename: str,
    raw: bytes,
    skill_name: str = "",
    overwrite: bool = False,
    complete: Optional[CompleteFn] = None,
    start_thread: bool = True,
) -> BookSkillJob:
    if not raw:
        raise ValueError("empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)")

    safe_name = sanitize_upload_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_BOOK_SUFFIXES:
        if suffix in {".doc", ".xls", ".ppt"}:
            raise ValueError("save this as .docx, .xlsx, or .pptx and upload that")
        raise ValueError(
            "unsupported file type. Use pdf, epub, docx, xlsx, pptx, txt, or markdown"
        )

    name = suggest_skill_name(safe_name, skill_name)
    uid = get_user_id()
    _assert_installable(name, overwrite=overwrite, user_id=uid)

    work = jobs_root() / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    staging = (
        work
        / f"upload_{threading.get_ident()}_{int(__import__('time').time() * 1000)}{suffix}"
    )
    staging.write_bytes(raw)

    job = BOOK_SKILL_JOBS.create(
        source_filename=safe_name,
        source_path=str(staging),
        work_dir="",
        skill_name=name,
        overwrite=overwrite,
        user_id=uid,
    )
    job_dir = jobs_root() / job.id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / safe_name
    shutil.move(str(staging), str(dest))
    job.source_path = str(dest)
    job.work_dir = str(job_dir)
    job.bump(message="pending", progress=1.0)

    if start_thread:
        threading.Thread(
            target=_run_job,
            args=(job.id,),
            kwargs={"complete": complete},
            name=f"book-to-skill-{job.id}",
            daemon=True,
        ).start()
    return job


def run_job_sync(job_id: str, *, complete: Optional[CompleteFn] = None) -> BookSkillJob:
    """Run pipeline inline (tests)."""
    return _run_job(job_id, complete=complete)


def _run_job(job_id: str, *, complete: Optional[CompleteFn] = None) -> BookSkillJob:
    # Worker threads do not inherit request ContextVar — lookup by id only.
    job = BOOK_SKILL_JOBS.get_by_id(job_id)
    if job is None:
        raise KeyError(job_id)

    set_user(job.user_id)
    try:
        if job.is_cancelled():
            job.bump(status="failed", progress=100.0, error="cancelled", message="cancelled")
            return job

        job.bump(status="extracting", progress=5.0, message="extracting text")
        pages = extract_document(Path(job.source_path))
        if job.is_cancelled():
            job.bump(status="failed", progress=100.0, error="cancelled", message="cancelled")
            return job

        chapters = structure_chapters(pages)
        if not chapters:
            raise ValueError("no chapters produced from document")

        def _prog(p: float, msg: str) -> None:
            if job.is_cancelled():
                raise RuntimeError("cancelled")
            job.bump(status="distilling", progress=p, message=msg)

        skill_slug = job.skill_name or suggest_skill_name(job.source_filename)
        job.bump(status="distilling", progress=10.0, message="distilling with Sidekick model")
        distill = distill_chapters(
            chapters,
            source_filename=job.source_filename,
            skill_name=skill_slug,
            complete=complete,
            on_progress=_prog,
        )

        if job.is_cancelled():
            job.bump(status="failed", progress=100.0, error="cancelled", message="cancelled")
            return job

        job.bump(status="validating", progress=92.0, message="validating SKILL.md frontmatter")
        skills_dir = _assert_installable(
            skill_slug, overwrite=job.overwrite, user_id=job.user_id
        )

        skill_md = write_skill_package(
            skills_dir,
            name=skill_slug,
            distill=distill,
            source_filename=job.source_filename,
            overwrite=job.overwrite,
        )
        # Guardrail: never leave artifacts under repo bundled skills.
        bundled = (SRC_ROOT / "skills").resolve()
        installed = skill_md.resolve()
        if _is_under(installed, bundled) or installed.parent == bundled:
            raise RuntimeError(
                f"refusing install into repo skills tree: {installed} (bundled={bundled})"
            )

        # Dev layout: primary store is APPDATA; mirror into runtime tenant dir for /skills UI.
        mirror_root = runtime_skills_mirror_dir(job.user_id)
        report_path = skill_md
        if mirror_root is not None:
            mirror_dest = mirror_root / skill_md.parent.name
            if mirror_dest.exists():
                shutil.rmtree(mirror_dest)
            shutil.copytree(skill_md.parent, mirror_dest)
            from ..skills import invalidate_skills_cache

            invalidate_skills_cache(mirror_root)
            report_path = mirror_dest / "SKILL.md"

        job.bump(
            status="installed",
            progress=100.0,
            message="installed",
            skill_name=skill_md.parent.name,
            skill_path=str(report_path),
            error="",
        )
    except Exception as exc:
        job.bump(
            status="failed",
            progress=100.0,
            error=str(exc),
            message="failed",
        )
    finally:
        reset_user()
    return job
