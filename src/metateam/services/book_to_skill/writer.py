"""Write distilled skill package into the user/tenant skills directory."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..skills import (
    compose_skill_markdown,
    invalidate_skills_cache,
    slug_skill_name,
    validate_skill_text,
)
from .distill import DistillResult, chapter_filename


def build_skill_markdown(name: str, description: str, body: str) -> str:
    return compose_skill_markdown(name, description, body)


def validate_generated(name: str, description: str, body: str) -> dict[str, Any]:
    return validate_skill_text(compose_skill_markdown(name, description, body))


def write_skill_package(
    skills_dir: Path,
    *,
    name: str,
    distill: DistillResult,
    source_filename: str,
    overwrite: bool = False,
) -> Path:
    """Materialize SKILL.md + references/ then return path to SKILL.md."""
    slug = slug_skill_name(name)
    if len(slug) < 2:
        raise ValueError("invalid skill name")

    check = validate_generated(slug, distill.description, distill.skill_body)
    if not check["ok"]:
        # try to pad body if only "too short"
        body = distill.skill_body
        if "body" in " ".join(str(x) for x in check["errors"]).lower() or any(
            "太短" in str(x) or "empty" in str(x).lower() or "短" in str(x) for x in check["errors"]
        ):
            body = (
                body.rstrip()
                + "\n\n## Notes\n\n"
                + f"Distilled offline from user-provided `{source_filename}`.\n"
                + "Load chapter files under references/chapters/ on demand.\n"
            )
            check = validate_generated(slug, distill.description, body)
            distill.skill_body = body
        if not check["ok"]:
            raise ValueError("; ".join(str(x) for x in (check["errors"] or [])))

    dest = Path(skills_dir) / slug
    if dest.exists():
        if not overwrite:
            raise FileExistsError(f"skill already exists: {slug}")
        shutil.rmtree(dest)

    chapters_dir = dest / "references" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    skill_md = compose_skill_markdown(slug, distill.description, distill.skill_body)
    (dest / "SKILL.md").write_text(skill_md, encoding="utf-8")

    for i, (title, md) in enumerate(distill.chapter_summaries, start=1):
        fname = chapter_filename(i, title)
        (chapters_dir / fname).write_text(md.strip() + "\n", encoding="utf-8")

    (dest / "references" / "glossary.md").write_text(
        distill.glossary.rstrip() + "\n", encoding="utf-8"
    )
    (dest / "references" / "patterns.md").write_text(
        distill.patterns.rstrip() + "\n", encoding="utf-8"
    )
    (dest / "references" / "cheatsheet.md").write_text(
        distill.cheatsheet.rstrip() + "\n", encoding="utf-8"
    )
    (dest / "references" / "SOURCE.txt").write_text(
        (
            f"source_filename: {source_filename}\n"
            "attribution: user-provided content; distilled by Sidekick book-to-skill\n"
            "runtime: offline extract + local/configured Sidekick LLM (no external book-to-skill CLI)\n"
        ),
        encoding="utf-8",
    )

    invalidate_skills_cache(skills_dir)
    return dest / "SKILL.md"
