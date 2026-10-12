"""In-house async job store for book->skill compilation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.events import new_id
from ..tenant_context import get_user_id

JOB_STATES = (
    "pending",
    "extracting",
    "distilling",
    "validating",
    "installed",
    "failed",
)

_ACTIVE = {"pending", "extracting", "distilling", "validating"}


@dataclass
class BookSkillJob:
    id: str
    user_id: str
    source_filename: str
    source_path: str
    work_dir: str
    status: str = "pending"
    progress: float = 0.0
    message: str = ""
    skill_name: str = ""
    skill_path: str = ""
    error: str = ""
    overwrite: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_requested: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def bump(
        self,
        *,
        status: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        skill_name: str | None = None,
        skill_path: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                if status not in JOB_STATES:
                    raise ValueError(f"invalid status: {status}")
                self.status = status
            if progress is not None:
                self.progress = max(0.0, min(100.0, float(progress)))
            if message is not None:
                self.message = str(message)
            if skill_name is not None:
                self.skill_name = skill_name
            if skill_path is not None:
                self.skill_path = skill_path
            if error is not None:
                self.error = str(error)
            self.updated_at = time.time()

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
            self.message = self.message or "cancel requested"

    def is_cancelled(self) -> bool:
        with self._lock:
            return bool(self.cancel_requested)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.id,
                "status": self.status,
                "progress": round(float(self.progress), 1),
                "message": self.message,
                "source_filename": self.source_filename,
                "skill_name": self.skill_name or None,
                "skill_path": self.skill_path or None,
                "error": self.error or None,
                "overwrite": self.overwrite,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "cancel_requested": self.cancel_requested,
            }


class BookSkillJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, BookSkillJob] = {}

    def create(
        self,
        *,
        source_filename: str,
        source_path: str,
        work_dir: str,
        skill_name: str = "",
        overwrite: bool = False,
        user_id: Optional[str] = None,
    ) -> BookSkillJob:
        uid = user_id or get_user_id()
        job = BookSkillJob(
            id=new_id("b2s"),
            user_id=uid,
            source_filename=source_filename,
            source_path=source_path,
            work_dir=work_dir,
            skill_name=skill_name,
            overwrite=bool(overwrite),
            message="queued",
        )
        with self._lock:
            self._jobs[job.id] = job
            self._gc_locked()
        return job

    def get(self, job_id: str, *, user_id: Optional[str] = None) -> Optional[BookSkillJob]:
        uid = user_id or get_user_id()
        with self._lock:
            job = self._jobs.get((job_id or "").strip())
        if not job or job.user_id != uid:
            return None
        return job

    def get_by_id(self, job_id: str) -> Optional[BookSkillJob]:
        """Lookup by id without tenant filter (worker threads)."""
        with self._lock:
            return self._jobs.get((job_id or "").strip())

    def list(self, *, user_id: Optional[str] = None, limit: int = 50) -> list[BookSkillJob]:
        uid = user_id or get_user_id()
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.user_id == uid]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[: max(1, min(int(limit or 50), 200))]

    def _gc_locked(self) -> None:
        # keep last 100 jobs
        if len(self._jobs) <= 100:
            return
        ordered = sorted(self._jobs.values(), key=lambda j: j.created_at)
        for job in ordered[:-100]:
            if job.status in _ACTIVE:
                continue
            self._jobs.pop(job.id, None)


BOOK_SKILL_JOBS = BookSkillJobManager()
