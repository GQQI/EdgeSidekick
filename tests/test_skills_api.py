from __future__ import annotations

from pathlib import Path

from metateam.runtime.tool_registry import skill_tool_name
from metateam.services.skills import (
    compose_skill_markdown,
    delete_skill,
    find_skill,
    import_skill_dir,
    import_skill_markdown,
    validate_skill_text,
    write_skill,
)


def test_skill_tool_name_visio() -> None:
    assert skill_tool_name("visio-skill") == "skill_visio_skill"
    assert skill_tool_name("oracle-db") == "skill_oracle_db"


def test_validate_requires_name_and_steps() -> None:
    bad = validate_skill_text("just some notes")
    assert not bad["ok"]
    ok = validate_skill_text(
        compose_skill_markdown(
            "demo-report",
            "Use when drafting a short status report.",
            "# Demo\n\n## Steps\n1. Collect facts\n2. Write the report\n",
        )
    )
    assert ok["ok"]
    assert ok["name"] == "demo-report"


def test_write_import_delete(tmp_path: Path) -> None:
    body = "# Demo\n\n## Steps\n1. Do the work\n2. Report back\n"
    sk = write_skill(
        tmp_path,
        name="My Skill",
        description="Use when the user asks for a canned workflow.",
        content=body,
    )
    assert sk.name == "my-skill"
    assert (tmp_path / "my-skill" / "SKILL.md").is_file()
    assert find_skill(tmp_path, "my-skill") is not None

    again = compose_skill_markdown(
        "other-skill",
        "Use when importing a second skill package.",
        body,
    )
    imported = import_skill_markdown(tmp_path, again)
    assert imported.name == "other-skill"
    assert delete_skill(tmp_path, "my-skill") == "my-skill"
    assert find_skill(tmp_path, "my-skill") is None


def test_import_fallback_name(tmp_path: Path) -> None:
    text = """---
description: Use this helper when summarizing meeting notes into bullets.
---

# Notes

## Steps
1. Read the notes
2. Return bullets
"""
    sk = import_skill_markdown(tmp_path, text, fallback_name="meeting-notes")
    assert sk.name == "meeting-notes"


def test_import_skill_dir_keeps_scripts(tmp_path: Path) -> None:
    src = tmp_path / "dameng-db"
    src.mkdir()
    (src / "SKILL.md").write_text(
        compose_skill_markdown(
            "dameng-db",
            "Use when querying a Dameng database offline.",
            "# Dameng\n\n## Steps\n1. Connect\n2. Run SQL\n",
        ),
        encoding="utf-8",
    )
    (src / "scripts").mkdir()
    (src / "scripts" / "db_exec.py").write_text("print('ok')\n", encoding="utf-8")
    dest = tmp_path / "installed"
    dest.mkdir()
    imported = import_skill_dir(dest, src)
    assert imported[0].name == "dameng-db"
    assert (dest / "dameng-db" / "scripts" / "db_exec.py").is_file()


def test_sync_workspace_skill_installs_package(tmp_path: Path) -> None:
    from metateam.services.skills import find_skill, sync_workspace_skill

    ws = tmp_path / "workspace"
    lib = tmp_path / "library"
    pkg = ws / "demo-pack"
    pkg.mkdir(parents=True)
    lib.mkdir()
    md = pkg / "SKILL.md"
    md.write_text(
        compose_skill_markdown(
            "demo-pack",
            "Use when testing skill library sync from a workspace folder.",
            "# Demo\n\n## Steps\n1. Run the helper\n2. Report\n",
        ),
        encoding="utf-8",
    )
    (pkg / "scripts").mkdir()
    (pkg / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    note = sync_workspace_skill(md, ws, lib)
    assert "demo-pack" in note
    assert find_skill(lib, "demo-pack") is not None
    assert (lib / "demo-pack" / "scripts" / "run.py").is_file()


def test_sync_workspace_skill_ignores_ordinary_files(tmp_path: Path) -> None:
    from metateam.services.skills import sync_workspace_skill

    ws = tmp_path / "workspace"
    lib = tmp_path / "library"
    ws.mkdir()
    lib.mkdir()
    notes = ws / "notes.md"
    notes.write_text("# just notes\n", encoding="utf-8")
    assert sync_workspace_skill(notes, ws, lib) == ""


def test_list_read_write_package_files(tmp_path: Path) -> None:
    from metateam.services.skills import (
        list_skill_package_files,
        read_skill_package_file,
        resolve_skill_package_path,
        write_skill,
        write_skill_package_file,
    )
    body = "# Demo\n\n## Steps\n1. List files\n2. Edit a script\n"
    sk = write_skill(
        tmp_path,
        name="pack-demo",
        description="Use when testing package file browse and edit APIs.",
        content=body,
    )
    scripts = sk.path.parent / "scripts"
    scripts.mkdir()
    (scripts / "hello.py").write_text("print(1)\n", encoding="utf-8")
    (sk.path.parent / "icon.png").write_bytes(bytes([0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A]) + bytes(8))
    files = list_skill_package_files(sk)
    assert "SKILL.md" in files
    assert "scripts/hello.py" in files
    assert files[0].lower() == "skill.md"
    packed = read_skill_package_file(sk, "scripts/hello.py")
    assert packed.get("binary") is False
    assert "print(1)" in str(packed.get("content"))
    binary = read_skill_package_file(sk, "icon.png")
    assert binary.get("binary") is True
    write_skill_package_file(sk, "scripts/hello.py", "print(2)\n", skills_dir=tmp_path)
    assert (scripts / "hello.py").read_text(encoding="utf-8") == "print(2)\n"
    try:
        resolve_skill_package_path(sk, "../escape.txt")
        raise AssertionError("expected traversal reject")
    except ValueError:
        pass


def test_create_skill_package_entry_file_and_folder(tmp_path: Path) -> None:
    from metateam.services.skills import (
        create_skill_package_entry,
        list_skill_package_files,
        write_skill,
    )

    sk = write_skill(
        tmp_path,
        name="pack-create",
        description="Use when testing create file and folder inside a skill package.",
        content="# Demo\n\n## Steps\n1. Create\n2. Verify\n",
    )
    folder = create_skill_package_entry(sk, "scripts", entry_type="folder", skills_dir=tmp_path)
    assert folder["type"] == "folder"
    assert (sk.path.parent / "scripts").is_dir()
    created = create_skill_package_entry(
        sk, "scripts/hello.py", entry_type="file", content="print(1)\n", skills_dir=tmp_path
    )
    assert created["type"] == "file"
    assert created["path"] == "scripts/hello.py"
    assert "scripts/hello.py" in created["files"]
    assert "scripts/hello.py" in list_skill_package_files(sk)
    assert (sk.path.parent / "scripts" / "hello.py").read_text(encoding="utf-8") == "print(1)\n"


def test_create_skill_package_entry_rejects_traversal(tmp_path: Path) -> None:
    from metateam.services.skills import create_skill_package_entry, write_skill

    sk = write_skill(
        tmp_path,
        name="pack-safe",
        description="Use when testing path traversal rejection for skill package create.",
        content="# Demo\n\n## Steps\n1. Reject\n2. Done\n",
    )
    for bad in ("../escape.txt", "..\\escape.txt", "/tmp/x", "C:/Windows/x", "scripts/../../outside.py"):
        try:
            create_skill_package_entry(sk, bad, entry_type="file", skills_dir=tmp_path)
            raise AssertionError(f"expected reject for {bad}")
        except ValueError:
            pass


def test_delete_skill_package_file(tmp_path: Path) -> None:
    from metateam.services.skills import (
        create_skill_package_entry,
        delete_skill_package_file,
        write_skill,
    )

    sk = write_skill(
        tmp_path,
        name="pack-del",
        description="Use when testing delete of non-SKILL.md package files.",
        content="# Demo\n\n## Steps\n1. Delete\n2. Done\n",
    )
    create_skill_package_entry(sk, "notes.txt", entry_type="file", content="hi\n", skills_dir=tmp_path)
    out = delete_skill_package_file(sk, "notes.txt", skills_dir=tmp_path)
    assert "notes.txt" not in out["files"]
    assert not (sk.path.parent / "notes.txt").exists()
    try:
        delete_skill_package_file(sk, "SKILL.md", skills_dir=tmp_path)
        raise AssertionError("expected reject delete SKILL.md")
    except ValueError:
        pass
