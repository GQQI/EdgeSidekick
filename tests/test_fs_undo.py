from __future__ import annotations

from pathlib import Path

from metateam.services import fs_undo
from metateam.services.git_ops import panel_snapshot


def test_checkpoint_stores_user_text(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="请帮我改登录页")
    data = fs_undo.status(tmp_path)
    assert data["count"] == 1
    item = data["items"][0]
    assert item["op"] == "checkpoint"
    assert item["user_text"] == "请帮我改登录页"
    assert "对话轮次" in (item.get("label") or "")


def test_review_snapshot_modified_and_new(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    fs_undo.push_before_write("a.txt", tmp_path / "a.txt", tmp_path)
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    fs_undo.push_before_write("new.txt", tmp_path / "new.txt", tmp_path)
    (tmp_path / "new.txt").write_text("one\ntwo\n", encoding="utf-8")
    snap = fs_undo.review_snapshot(tmp_path)
    paths = {f["path"]: f for f in snap["files"]}
    assert "a.txt" in paths
    assert paths["a.txt"]["kind"] == "modified"
    assert paths["a.txt"]["added"] >= 1
    assert paths["new.txt"]["kind"] == "added"
    assert paths["new.txt"]["added"] == 2
    assert snap["totals"]["files"] == 2


def test_review_snapshot_deleted(tmp_path: Path) -> None:
    (tmp_path / "gone.txt").write_text("x\ny\n", encoding="utf-8")
    fs_undo.push_before_delete("gone.txt", tmp_path / "gone.txt", tmp_path)
    (tmp_path / "gone.txt").unlink()
    snap = fs_undo.review_snapshot(tmp_path)
    assert snap["files"][0]["path"] == "gone.txt"
    assert snap["files"][0]["kind"] == "deleted"
    assert snap["files"][0]["deleted"] == 2
    pair = fs_undo.file_review_pair(tmp_path, "gone.txt")
    assert pair["kind"] == "deleted"
    assert pair["is_deleted"] is True
    assert pair["new"] == ""
    assert pair["old"].replace("\r\n", "\n") == "x\ny\n"


def test_review_shows_create_then_delete(tmp_path: Path) -> None:
    fs_undo.push_before_create("tmp.txt", "file", tmp_path)
    (tmp_path / "tmp.txt").write_text("n\n", encoding="utf-8")
    fs_undo.push_before_delete("tmp.txt", tmp_path / "tmp.txt", tmp_path)
    (tmp_path / "tmp.txt").unlink()
    snap = fs_undo.review_snapshot(tmp_path)
    assert len(snap["files"]) == 1
    assert snap["files"][0]["path"] == "tmp.txt"
    assert snap["files"][0]["kind"] == "deleted"
    pair = fs_undo.file_review_pair(tmp_path, "tmp.txt")
    assert pair["kind"] == "deleted"
    assert "n" in pair["old"]


def test_review_missing_after_modify_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    fs_undo.push_before_write("a.txt", tmp_path / "a.txt", tmp_path)
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "a.txt").unlink()
    snap = fs_undo.review_snapshot(tmp_path)
    assert len(snap["files"]) == 1
    assert snap["files"][0]["kind"] == "deleted"
    pair = fs_undo.file_review_pair(tmp_path, "a.txt")
    assert pair["kind"] == "deleted"
    assert pair["old"].replace("\r\n", "\n") == "hello\n"


def test_review_dir_delete_lists_inner_files(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "a.txt").write_text("one\n", encoding="utf-8")
    (pack / "b.txt").write_text("two\nthree\n", encoding="utf-8")
    fs_undo.push_before_delete("pack", pack, tmp_path)
    import shutil

    shutil.rmtree(pack)
    snap = fs_undo.review_snapshot(tmp_path)
    paths = {f["path"]: f for f in snap["files"]}
    assert paths["pack/a.txt"]["kind"] == "deleted"
    assert paths["pack/b.txt"]["kind"] == "deleted"
    pair = fs_undo.file_review_pair(tmp_path, "pack/a.txt")
    assert pair["kind"] == "deleted"
    assert "one" in pair["old"]
    assert pair["new"] == ""


def test_checkpoint_clips_long_user_text(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 1, workspace=tmp_path, user_text="x" * 800)
    item = fs_undo.status(tmp_path)["items"][0]
    assert len(item["user_text"]) <= 500
    assert item["user_text"].endswith("…")


def test_panel_snapshot_non_repo_lists_undo_files(tmp_path: Path) -> None:
    fs_undo.push_before_write("x.txt", tmp_path / "x.txt", tmp_path)
    (tmp_path / "x.txt").write_text("hi\n", encoding="utf-8")
    snap = panel_snapshot(tmp_path)
    assert snap["is_repo"] is False
    assert snap["totals"]["files"] == 1
    assert snap["files"][0]["path"] == "x.txt"
    assert snap["files"][0]["kind"] == "added"


def test_file_review_pair_modified(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    fs_undo.push_before_write("a.txt", tmp_path / "a.txt", tmp_path)
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    pair = fs_undo.file_review_pair(tmp_path, "a.txt")
    assert pair["kind"] == "modified"
    assert pair["old"].replace("\r\n", "\n") == "hello\n"
    assert "world" in pair["new"]


def test_status_groups_checkpoint_with_writes(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="请生成一份说明")
    fs_undo.push_before_write("doc.md", tmp_path / "doc.md", tmp_path)
    (tmp_path / "doc.md").write_text("# hi\n", encoding="utf-8")
    data = fs_undo.status(tmp_path)
    assert data["count"] == 1
    item = data["items"][0]
    assert item["op"] == "checkpoint"
    assert item["user_text"] == "请生成一份说明"
    assert item["files"] == ["doc.md"]


def test_duplicate_checkpoint_not_pushed(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="一次对话")
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="一次对话")
    data = fs_undo.status(tmp_path)
    assert data["count"] == 1


def test_review_create_then_later_modify(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="生成文档")
    fs_undo.push_before_write("doc.md", tmp_path / "doc.md", tmp_path)
    (tmp_path / "doc.md").write_text("hello\n", encoding="utf-8")
    snap = fs_undo.review_snapshot(tmp_path)
    assert snap["files"][0]["kind"] == "added"

    fs_undo.push_checkpoint("sess-1", 1, workspace=tmp_path, user_text="改一下刚才的文档")
    fs_undo.push_before_write("doc.md", tmp_path / "doc.md", tmp_path)
    (tmp_path / "doc.md").write_text("hello\nworld\n", encoding="utf-8")
    snap = fs_undo.review_snapshot(tmp_path)
    assert len(snap["files"]) == 1
    assert snap["files"][0]["kind"] == "added"
    assert snap["files"][0]["added"] >= 2
    pair = fs_undo.file_review_pair(tmp_path, "doc.md")
    assert pair["kind"] == "added"
    assert pair["old"] == ""
    assert "world" in pair["new"]
    data = fs_undo.status(tmp_path)
    assert data["count"] == 2
    assert data["items"][0]["user_text"] == "改一下刚才的文档"
    assert data["items"][1]["user_text"] == "生成文档"


def test_review_ignores_previous_session(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-a", 0, workspace=tmp_path, user_text="旧对话")
    fs_undo.push_before_write("old.md", tmp_path / "old.md", tmp_path)
    (tmp_path / "old.md").write_text("old\n", encoding="utf-8")
    fs_undo.push_checkpoint("sess-b", 0, workspace=tmp_path, user_text="新对话")
    fs_undo.push_before_write("new.md", tmp_path / "new.md", tmp_path)
    (tmp_path / "new.md").write_text("new\n", encoding="utf-8")
    snap = fs_undo.review_snapshot(tmp_path, session_id="sess-b")
    paths = {f["path"] for f in snap["files"]}
    assert paths == {"new.md"}
    default = fs_undo.review_snapshot(tmp_path)
    assert {f["path"] for f in default["files"]} == {"new.md"}
    old = fs_undo.review_snapshot(tmp_path, session_id="sess-a")
    assert {f["path"] for f in old["files"]} == {"old.md"}


def test_undo_latest_turn_restores_whole_conversation(tmp_path: Path) -> None:
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="生成")
    fs_undo.push_before_write("doc.md", tmp_path / "doc.md", tmp_path)
    (tmp_path / "doc.md").write_text("hello\n", encoding="utf-8")
    result = fs_undo.undo_latest_turn(tmp_path)
    assert result["undone_count"] >= 2
    assert not (tmp_path / "doc.md").exists()
    assert fs_undo.status(tmp_path)["count"] == 0


def test_status_and_undo_are_scoped_to_session(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a0\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("b0\n", encoding="utf-8")
    fs_undo.push_checkpoint("sess-a", 0, workspace=tmp_path, user_text="改 A")
    fs_undo.set_turn_context("sess-a", 0)
    fs_undo.push_before_write("a.md", tmp_path / "a.md", tmp_path)
    (tmp_path / "a.md").write_text("a1\n", encoding="utf-8")
    fs_undo.clear_turn_context()
    fs_undo.push_checkpoint("sess-b", 0, workspace=tmp_path, user_text="改 B")
    fs_undo.set_turn_context("sess-b", 0)
    fs_undo.push_before_write("b.md", tmp_path / "b.md", tmp_path)
    (tmp_path / "b.md").write_text("b1\n", encoding="utf-8")
    fs_undo.clear_turn_context()

    a_items = fs_undo.status(tmp_path, session_id="sess-a")["items"]
    b_items = fs_undo.status(tmp_path, session_id="sess-b")["items"]
    assert [i["user_text"] for i in a_items] == ["改 A"]
    assert [i["user_text"] for i in b_items] == ["改 B"]
    assert a_items[0]["files"] == ["a.md"]
    assert b_items[0]["files"] == ["b.md"]

    fs_undo.undo_latest_turn(tmp_path, session_id="sess-a")
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a0\n"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b1\n"
    assert fs_undo.status(tmp_path, session_id="sess-a")["count"] == 0
    assert fs_undo.status(tmp_path, session_id="sess-b")["count"] == 1


def test_undo_to_turn_keeps_explorer_deletes(tmp_path: Path) -> None:
    """Restore-files-and-resend must not resurrect files the user deleted in the explorer."""
    (tmp_path / "keep.txt").write_text("orig\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("x\n", encoding="utf-8")
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="改 keep")
    fs_undo.set_turn_context("sess-1", 0)
    fs_undo.push_before_write("keep.txt", tmp_path / "keep.txt", tmp_path)
    (tmp_path / "keep.txt").write_text("edited\n", encoding="utf-8")
    fs_undo.clear_turn_context()

    fs_undo.push_checkpoint("sess-1", 1, workspace=tmp_path, user_text="later")
    fs_undo.push_before_delete("gone.txt", tmp_path / "gone.txt", tmp_path, source="explorer")
    (tmp_path / "gone.txt").unlink()

    fs_undo.undo_to_turn("sess-1", 0, tmp_path)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "orig\n"
    assert not (tmp_path / "gone.txt").exists()


def test_undo_to_turn_does_not_restore_agent_write_of_explorer_deleted_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("orig\n", encoding="utf-8")
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="改 a")
    fs_undo.set_turn_context("sess-1", 0)
    fs_undo.push_before_write("a.txt", tmp_path / "a.txt", tmp_path)
    (tmp_path / "a.txt").write_text("edited\n", encoding="utf-8")
    fs_undo.clear_turn_context()
    fs_undo.push_before_delete("a.txt", tmp_path / "a.txt", tmp_path, source="explorer")
    (tmp_path / "a.txt").unlink()

    fs_undo.undo_to_turn("sess-1", 0, tmp_path)
    assert not (tmp_path / "a.txt").exists()
