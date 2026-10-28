"""Book PDF/EPUB -> Skill package (offline distill via Sidekick LLM)."""

from __future__ import annotations

from .jobs import BOOK_SKILL_JOBS, BookSkillJob, BookSkillJobManager
from .pipeline import enqueue_book_to_skill

__all__ = [
    "BOOK_SKILL_JOBS",
    "BookSkillJob",
    "BookSkillJobManager",
    "enqueue_book_to_skill",
]
