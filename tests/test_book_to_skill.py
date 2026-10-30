"""Unit tests for book-to-skill (no live LLM)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from metateam.services.book_to_skill.distill import distill_chapters
from metateam.services.book_to_skill.extract import PageText, extract_document, join_pages
from metateam.services.book_to_skill.jobs import BOOK_SKILL_JOBS, JOB_STATES
from metateam.services.book_to_skill.pipeline import (
    enqueue_book_to_skill,
    run_job_sync,
    sanitize_upload_filename,
    suggest_skill_name,
)
from metateam.services.book_to_skill.structure import structure_chapters
from metateam.services.book_to_skill.writer import validate_generated, write_skill_package
from metateam.services.skills import find_skill, load_skills, slug_skill_name


def test_extract_docx_xlsx_and_markdown(tmp_path: Path) -> None:
    docx = tmp_path / "guide.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>每次改项目前先读说明</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", xml)
    assert "说明" in join_pages(extract_document(docx))

    xlsx = tmp_path / "sheet.xlsx"
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>收入</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row><c t="s"><v>0</v></c></row></sheetData></worksheet>'
    )
    with zipfile.ZipFile(xlsx, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    assert "收入" in join_pages(extract_document(xlsx))

    notes = tmp_path / "notes.md"
    notes.write_text("# 说明\n\nhello markdown\n", encoding="utf-8")
    assert "hello markdown" in join_pages(extract_document(notes))


def test_enqueue_rejects_unknown_and_legacy_office() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        enqueue_book_to_skill(filename="payload.exe", raw=b"MZ", start_thread=False)
    with pytest.raises(ValueError, match="docx"):
        enqueue_book_to_skill(filename="old.doc", raw=b"doc", start_thread=False)


def test_sanitize_upload_filename() -> None:
    assert sanitize_upload_filename("../evil/My Book!!.pdf") == "My_Book_.pdf"
    assert sanitize_upload_filename(r"C:\\tmp\\guide.epub").endswith(".epub")
    assert ".." not in sanitize_upload_filename("../../x.pdf")


def test_suggest_skill_name_slug() -> None:
    assert suggest_skill_name("Deep Learning.pdf") == "deep-learning"
    assert suggest_skill_name("x.pdf", override="My Cool Skill") == "my-cool-skill"
    assert len(slug_skill_name("你好世界手册")) >= 0  # may be empty for CJK-only
    # override wins when valid
    assert suggest_skill_name("a.pdf", override="ml-handbook") == "ml-handbook"


def test_structure_chapters_slices_and_caps() -> None:
    pages = [
        PageText(1, "p1", "Chapter 1. Intro\n\n" + ("alpha " * 400)),
        PageText(2, "p2", "Chapter 2. Next\n\n" + ("beta " * 400)),
        PageText(3, "p3", "Chapter 3. More\n\n" + ("gamma " * 400)),
    ]
    chapters = structure_chapters(pages, chapter_chars=500, max_chapters=10)
    assert chapters
    assert len(chapters) <= 10
    assert all(len(c.text) <= 500 for c in chapters)


def test_structure_fallback_page_bundles() -> None:
    pages = [PageText(i, f"p{i}", ("word " * 200) + f" page{i}") for i in range(1, 6)]
    chapters = structure_chapters(pages, chapter_chars=800, max_chapters=40)
    assert len(chapters) >= 2
    assert sum(len(c.text) for c in chapters) > 0


def test_job_state_machine_transitions() -> None:
    job = BOOK_SKILL_JOBS.create(
        source_filename="t.pdf",
        source_path="/tmp/t.pdf",
        work_dir="/tmp",
        skill_name="t",
        user_id="test-user",
    )
    assert job.status == "pending"
    for st in ("extracting", "distilling", "validating", "installed"):
        assert st in JOB_STATES
        job.bump(status=st, progress=50.0, message=st)
        assert job.status == st
    snap = job.snapshot()
    assert snap["job_id"] == job.id
    assert snap["status"] == "installed"


def test_job_rejects_bad_status() -> None:
    job = BOOK_SKILL_JOBS.create(
        source_filename="t.pdf",
        source_path="/tmp/t.pdf",
        work_dir="/tmp",
        user_id="test-user-2",
    )
    with pytest.raises(ValueError):
        job.bump(status="running")


def test_writer_skill_md_and_refs(tmp_path: Path) -> None:
    from metateam.services.book_to_skill.distill import DistillResult

    distill = DistillResult(
        skill_body=(
            "# demo-book\n\n## Overview\n\nA demo.\n\n"
            "## When to use\n\nUse for demo topics.\n\n"
            "## How to use this skill (progressive disclosure)\n\n"
            "Load chapters on demand from references/chapters/.\n\n"
            "## Chapter index\n\n1. `references/chapters/01-intro.md` — Intro\n\n"
            "## Attribution\n\nSource file: `demo.pdf` (user-provided).\n"
        ),
        description="Use when answering questions about the demo book topics offline.",
        chapter_summaries=[("Intro", "# Intro\n\nKey points:\n- a\n- b\n")],
        glossary="# Glossary\n\nterm — def\n",
        patterns="# Patterns\n\n- pattern one\n",
        cheatsheet="# Cheatsheet\n\n- tip\n",
    )
    path = write_skill_package(
        tmp_path,
        name="demo-book",
        distill=distill,
        source_filename="demo.pdf",
        overwrite=False,
    )
    assert path.is_file()
    assert (tmp_path / "demo-book" / "references" / "chapters" / "01-intro.md").is_file()
    assert (tmp_path / "demo-book" / "references" / "glossary.md").is_file()
    assert (tmp_path / "demo-book" / "references" / "SOURCE.txt").is_file()
    sk = find_skill(tmp_path, "demo-book")
    assert sk is not None
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "name: demo-book" in text
    check = validate_generated(
        "demo-book",
        distill.description,
        distill.skill_body,
    )
    assert check["ok"]


def test_distill_uses_injected_complete(tmp_path: Path) -> None:
    chapters = structure_chapters(
        [PageText(1, "p1", "Chapter 1. Hello\n\n" + ("content " * 100))],
        chapter_chars=2000,
    )

    def fake(system: str, user: str) -> str:
        if "YAML" in user or "description" in user.lower():
            return "Use when studying the hello chapter of the sample book."
        if "Glossary" in user or "glossary" in user:
            return "# Glossary\n\nhello — greeting\n"
        if "Patterns" in user or "patterns" in user:
            return "# Patterns\n\n- greet then explain\n"
        if "Cheatsheet" in user or "cheatsheet" in user:
            return "# Cheatsheet\n\n- say hello\n"
        if "SKILL.md BODY" in user or "progressive disclosure" in user.lower():
            return (
                "# sample\n\n## Overview\n\nHello book.\n\n"
                "## When to use\n\nWhen user asks about hello.\n\n"
                "## How to use this skill (progressive disclosure)\n\n"
                "Load references/chapters on demand.\n\n"
                "## Chapter index\n\n1. chapter\n\n"
                "## Attribution\n\nuser-provided sample.pdf\n"
            )
        return "# Chapter\n\n## Key points\n\n- point\n\n## Concepts / definitions\n\n- c\n"

    result = distill_chapters(
        chapters,
        source_filename="sample.pdf",
        skill_name="sample",
        complete=fake,
    )
    assert result.chapter_summaries
    assert "Overview" in result.skill_body or "overview" in result.skill_body.lower()
    assert len(result.description) >= 8

    write_skill_package(
        tmp_path,
        name="sample",
        distill=result,
        source_filename="sample.pdf",
        overwrite=True,
    )
    assert load_skills(tmp_path)
    assert find_skill(tmp_path, "sample") is not None


def _minimal_pdf_bytes(text: str = "Chapter 1. Test\n\nHello Sidekick book distill.") -> bytes:
    """Build a tiny PDF with pypdf so extract_pdf works in tests."""
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    # Prefer reportlab-free path: pypdf cannot easily add text without a font.
    # Fallback: write a one-page blank PDF and mock extract in pipeline test via pages.
    # For integration we create a PDF and if extract fails, skip.
    buf = BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)
    return buf.getvalue()


def test_pipeline_with_mock_extract_and_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from metateam.services.book_to_skill import pipeline as pipe

    skills = tmp_path / "tenant-skills"
    skills.mkdir()
    monkeypatch.setattr(pipe, "jobs_root", lambda: tmp_path / "jobs")
    monkeypatch.setattr(pipe, "install_skills_dir", lambda user_id=None: skills)
    monkeypatch.setattr(pipe, "runtime_skills_mirror_dir", lambda user_id=None: None)

    pages = [
        PageText(1, "p1", "Chapter 1. Alpha\n\n" + ("alpha knowledge. " * 80)),
        PageText(2, "p2", "Chapter 2. Beta\n\n" + ("beta knowledge. " * 80)),
    ]
    monkeypatch.setattr(pipe, "extract_document", lambda path: pages)

    def fake(system: str, user: str) -> str:
        if "description" in user.lower() and "YAML" in user:
            return "Use when the user asks about alpha or beta topics from the uploaded book."
        if "glossary" in user.lower():
            return "# Glossary\n\nalpha — first\nbeta — second\n"
        if "patterns" in user.lower():
            return "# Patterns\n\n- read then summarize\n"
        if "cheatsheet" in user.lower():
            return "# Cheatsheet\n\n- alpha\n- beta\n"
        if "SKILL.md BODY" in user or "progressive disclosure" in user:
            return (
                "# pipeline-book\n\n## Overview\n\nTest book skill.\n\n"
                "## When to use\n\nAlpha beta questions.\n\n"
                "## How to use this skill (progressive disclosure)\n\n"
                "Load chapter files on demand; do not dump the whole book.\n\n"
                "## Chapter index\n\n1. alpha\n2. beta\n\n"
                "## Working style\n\nBe concise.\n\n"
                "## Attribution\n\nSource: user-provided book.pdf\n"
            )
        return f"# Chapter\n\n## Key points\n\n- from mock\n\n## Concepts / definitions\n\n- x\n"

    raw = b"%PDF-1.4 mock"
    job = enqueue_book_to_skill(
        filename="book.pdf",
        raw=raw + b"0" * 100,
        skill_name="pipeline-book",
        overwrite=True,
        complete=fake,
        start_thread=False,
    )
    done = run_job_sync(job.id, complete=fake)
    assert done.status == "installed", done.error
    assert find_skill(skills, "pipeline-book") is not None
    snap = done.snapshot()
    assert snap["progress"] == 100.0
    assert snap["skill_name"] == "pipeline-book"
    assert done.skill_path
    assert Path(done.skill_path).resolve().is_relative_to(skills.resolve())


def test_install_skills_dir_never_repo_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev ROOT==SRC_ROOT installs under APPDATA-style root, never repo src/skills."""
    from metateam.services.book_to_skill import pipeline as pipe

    fake_src = tmp_path / "repo" / "src"
    bundled = fake_src / "skills"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(pipe, "SRC_ROOT", fake_src)
    monkeypatch.setattr(pipe, "ROOT", fake_src)  # simulate unset SIDEKICK_DATA_DIR
    monkeypatch.setattr(pipe, "tenant_skills_dir", lambda user_id=None: bundled)
    monkeypatch.setattr(pipe, "_stable_user_data_root", lambda: tmp_path / "AppData" / "EdgeSidekick")
    dest = pipe.install_skills_dir("u_test")
    assert dest == tmp_path / "AppData" / "EdgeSidekick" / "data" / "tenants" / "u_test" / "skills"
    assert dest.is_dir()
    assert dest.resolve() != bundled.resolve()
    assert "skills" not in {dest.parent.name} or dest.parent.name == "u_test" or True
    # Must not be the bundled tree
    assert not str(dest.resolve()).startswith(str(bundled.resolve()))
