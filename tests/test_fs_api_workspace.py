from pathlib import Path

import pytest

from metateam.services import fs_api


def test_describe_fs_error_keeps_the_reason_with_the_path() -> None:
    missing = FileNotFoundError("nested/item.json")
    assert fs_api.describe_fs_error(missing) == "not found: nested/item.json"
    present = FileExistsError("nested/item.json")
    assert fs_api.describe_fs_error(present) == "already exists: nested/item.json"
    explained = FileNotFoundError("not found: nested/item.json")
    assert fs_api.describe_fs_error(explained) == "not found: nested/item.json"


def test_workspace_root_uses_bound_path(tmp_path: Path) -> None:
    bound = tmp_path / "opened-folder"
    bound.mkdir()
    token = fs_api.bind_active_workspace(bound)
    try:
        assert fs_api.workspace_root() == bound.resolve()
        target = fs_api.safe_resolve("hello.md")
        assert target == (bound / "hello.md").resolve()
    finally:
        fs_api.reset_active_workspace(token)


def test_write_text_remaps_foreign_drive(tmp_path: Path, monkeypatch) -> None:
    from metateam.core import pathutil

    monkeypatch.setattr(pathutil, "_windows_drive_ready", lambda letter: False)
    token = fs_api.bind_active_workspace(tmp_path)
    try:
        res = fs_api.write_text("Q:/Project/hello.md", "hi", allow_outside=True)
        dest = tmp_path / "Project" / "hello.md"
        assert dest.is_file()
        assert dest.read_text(encoding="utf-8") == "hi"
        assert "hello.md" in str(res["path"]).replace("\\", "/")
    finally:
        fs_api.reset_active_workspace(token)


def test_write_and_delete_outside_after_allow(tmp_path: Path) -> None:
    token = fs_api.bind_active_workspace(tmp_path)
    dest = tmp_path.parent / f"out-write-{tmp_path.name}" / "hello.md"
    try:
        res = fs_api.write_text(str(dest), "hi", allow_outside=True)
        assert dest.is_file()
        assert dest.read_text(encoding="utf-8") == "hi"
        assert dest.name in str(res["path"])
        with pytest.raises(ValueError, match="outside workspace"):
            fs_api.delete_entry(str(dest))
        gone = fs_api.delete_entry(str(dest), allow_outside=True)
        assert gone["deleted"] is True
        assert not dest.exists()
    finally:
        if dest.exists():
            dest.unlink()
        if dest.parent.exists():
            dest.parent.rmdir()
        fs_api.reset_active_workspace(token)


def test_write_text_keeps_full_body(tmp_path: Path) -> None:
    token = fs_api.bind_active_workspace(tmp_path)
    try:
        body = ("line\n" * 4000) + "报告 (终稿)#v2"
        res = fs_api.write_text("docs/big.md", body)
        dest = tmp_path / "docs" / "big.md"
        assert dest.read_text(encoding="utf-8") == body
        assert res["size"] == len(body)
    finally:
        fs_api.reset_active_workspace(token)


def test_write_uses_explicit_encoding(tmp_path: Path) -> None:
    token = fs_api.bind_active_workspace(tmp_path)
    try:
        dest = tmp_path / "gb.txt"
        dest.write_bytes("中文内容".encode("gb18030"))
        fs_api.write_text("gb.txt", "中文内容改", encoding="gb18030")
        assert dest.read_bytes() == "中文内容改".encode("gb18030")
        fs_api.write_text("gb.txt", "中文内容改")
        assert dest.read_bytes() == "中文内容改".encode("utf-8")
    finally:
        fs_api.reset_active_workspace(token)
