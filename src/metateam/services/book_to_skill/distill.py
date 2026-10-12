"""Distill chapter slices with Sidekick's own LLM.complete_text path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .structure import Chapter

CompleteFn = Callable[[str, str], str]

SYSTEM_CHAPTER = (
    "You are distilling a user-provided book chapter into a durable Agent Skill reference. "
    "Write concise Markdown in the same language as the source. "
    "Do not invent facts not present in the text. "
    "Output ONLY the Markdown body (no YAML frontmatter)."
)

SYSTEM_META = (
    "You are building compact Agent Skill reference files from chapter summaries of a "
    "user-provided book. Stay faithful to the summaries. Same language as the input. "
    "Output ONLY Markdown (no YAML frontmatter)."
)


@dataclass
class DistillResult:
    skill_body: str
    description: str
    chapter_summaries: list[tuple[str, str]] = field(default_factory=list)  # title, md
    glossary: str = ""
    patterns: str = ""
    cheatsheet: str = ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n\n…(truncated)"


def default_complete_fn() -> CompleteFn:
    """Bind Sidekick LLM using current settings (chat/agent model path)."""
    from ...core.config import get_settings
    from ...runtime.llm import LLM
    from ...services.model_config import apply_to_settings, load_model_config

    settings = get_settings().clone()
    try:
        apply_to_settings(settings, load_model_config())
    except Exception:
        pass
    # Cap completion size for distill; keep calls offline against configured endpoint.
    llm = LLM(settings, max_tokens=min(int(getattr(settings, "compress_max_tokens", 0) or 2500), 2500) or 2500)

    def _call(system: str, user: str) -> str:
        return llm.complete_text(system, user, temperature=0.1)

    return _call


def _heuristic_chapter_summary(ch: Chapter) -> str:
    lines = [ln.strip() for ln in ch.text.splitlines() if ln.strip()]
    head = lines[:12]
    body = "\n".join(f"- {ln[:200]}" for ln in head) or ch.text[:800]
    return (
        f"## {ch.title}\n\n"
        f"(Heuristic extract — model unavailable / demo)\n\n"
        f"{body}\n"
    )


def distill_chapters(
    chapters: list[Chapter],
    *,
    source_filename: str,
    skill_name: str,
    complete: Optional[CompleteFn] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
    chapter_input_chars: int = 6_000,
) -> DistillResult:
    fn = complete or default_complete_fn()
    summaries: list[tuple[str, str]] = []
    n = max(1, len(chapters))

    for i, ch in enumerate(chapters):
        if on_progress:
            on_progress(10 + 70 * (i / n), f"distilling chapter {i + 1}/{n}: {ch.title}")
        user = (
            f"Source file: {source_filename}\n"
            f"Skill slug: {skill_name}\n"
            f"Chapter {ch.index}: {ch.title}\n\n"
            f"--- CHAPTER TEXT ---\n"
            f"{_truncate(ch.text, chapter_input_chars)}\n"
            f"--- END ---\n\n"
            "Write:\n"
            f"# {ch.title}\n"
            "## Key points (bullets)\n"
            "## Concepts / definitions\n"
            "## Procedures or patterns (if any)\n"
            "## Notable quotes or formulas (if any)\n"
            "Keep under ~800 words."
        )
        try:
            md = (fn(SYSTEM_CHAPTER, user) or "").strip()
        except Exception:
            md = ""
        if not md or len(md) < 40:
            md = _heuristic_chapter_summary(ch)
        if not md.lstrip().startswith("#"):
            md = f"# {ch.title}\n\n{md}"
        summaries.append((ch.title, md))

    if on_progress:
        on_progress(82.0, "distilling skill overview / glossary / patterns")

    catalog = "\n\n".join(
        f"### {title}\n{_truncate(body, 1200)}" for title, body in summaries
    )
    catalog = _truncate(catalog, 14_000)

    overview_user = (
        f"Source filename (user-provided): {source_filename}\n"
        f"Target skill name: {skill_name}\n\n"
        "Chapter summaries:\n"
        f"{catalog}\n\n"
        "Produce the SKILL.md BODY (no frontmatter) with these sections:\n"
        f"# {skill_name}\n"
        "## Overview\n"
        "## When to use\n"
        "## How to use this skill (progressive disclosure)\n"
        "Explain that chapter details live under references/chapters/ and must be "
        "loaded on demand with read_file — NEVER paste the whole book into context.\n"
        "## Chapter index\n"
        "List each chapter file as `references/chapters/NN-slug.md` with one-line blurb.\n"
        "## Working style\n"
        "Short bullets.\n"
        "## Attribution\n"
        f"Note: distilled from user-provided file `{source_filename}`; content is user-provided.\n"
        "Keep the whole body under ~400 lines."
    )
    try:
        skill_body = (fn(SYSTEM_META, overview_user) or "").strip()
    except Exception:
        skill_body = ""
    if not skill_body or len(skill_body) < 80:
        index_lines = []
        for i, (title, _) in enumerate(summaries, start=1):
            index_lines.append(f"{i}. `{_chapter_rel(i, title)}` — {title}")
        skill_body = (
            f"# {skill_name}\n\n"
            f"## Overview\n\n"
            f"Skill distilled from user-provided book `{source_filename}`.\n\n"
            f"## When to use\n\n"
            f"Use when the user asks about topics covered in `{source_filename}`.\n\n"
            f"## How to use this skill (progressive disclosure)\n\n"
            f"1. Read this SKILL.md first.\n"
            f"2. Load only the needed chapter from `references/chapters/` via read_file.\n"
            f"3. Optionally open `references/glossary.md`, `patterns.md`, or `cheatsheet.md`.\n"
            f"4. Never dump the entire book into the conversation context.\n\n"
            f"## Chapter index\n\n"
            + "\n".join(index_lines)
            + "\n\n## Attribution\n\n"
            f"Source file: `{source_filename}` (user-provided). Generated by Sidekick book-to-skill.\n"
        )

    desc_user = (
        f"Skill name: {skill_name}\nSource: {source_filename}\n\n"
        f"Summaries:\n{_truncate(catalog, 4000)}\n\n"
        "Write ONE English-or-source-language description sentence (8–200 chars) for YAML "
        "`description:` — what it does AND when to trigger. No quotes. No newline."
    )
    try:
        description = (fn(SYSTEM_META, desc_user) or "").strip().splitlines()[0].strip().strip("\"'")
    except Exception:
        description = ""
    if len(description) < 8:
        description = (
            f"Knowledge skill distilled from user book {source_filename}. "
            f"Use when answering questions about that book's topics."
        )
    description = description[:380]

    def _ref_doc(kind: str, instruction: str) -> str:
        user = (
            f"Source: {source_filename}\nSkill: {skill_name}\n\n"
            f"Chapter summaries:\n{catalog}\n\n"
            f"Write references/{kind}.md:\n{instruction}\n"
            "Keep under ~300 lines."
        )
        try:
            out = (fn(SYSTEM_META, user) or "").strip()
        except Exception:
            out = ""
        if not out or len(out) < 40:
            out = f"# {kind}\n\n(See chapter summaries under references/chapters/.)\n"
        return out

    glossary = _ref_doc("glossary", "# Glossary\nTerm — short definition bullets from the book.")
    patterns = _ref_doc("patterns", "# Patterns\nReusable workflows / design patterns found in the book.")
    cheatsheet = _ref_doc("cheatsheet", "# Cheatsheet\nQuick commands, formulas, checklists.")

    if on_progress:
        on_progress(90.0, "distill complete")

    return DistillResult(
        skill_body=skill_body,
        description=description,
        chapter_summaries=summaries,
        glossary=glossary,
        patterns=patterns,
        cheatsheet=cheatsheet,
    )


def _slug_piece(title: str) -> str:
    from ..skills import slug_skill_name

    s = slug_skill_name(title) or "chapter"
    return s[:48]


def _chapter_rel(index: int, title: str) -> str:
    return f"references/chapters/{index:02d}-{_slug_piece(title)}.md"


def chapter_filename(index: int, title: str) -> str:
    return f"{index:02d}-{_slug_piece(title)}.md"
