from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from metateam.services import fs_undo
from metateam.services.git_ops import (
    _rel_paths,
    checkout_branch,
    current_branch,
    file_change_pair,
    panel_snapshot,
    review_panel_snapshot,
    stage_paths,
    validate_branch_name,
    validate_remote_url,
)

git_bin = shutil.which("git")
pytestmark = pytest.mark.skipif(not git_bin, reason="git not installed")


def test_rel_paths_relative(tmp_path: Path) -> None:
    assert _rel_paths(tmp_path, ["src/a.py", "ui\\App.tsx"]) == ["src/a.py", "ui/App.tsx"]


def test_rel_paths_skips_flags(tmp_path: Path) -> None:
    assert _rel_paths(tmp_path, ["-u", "", "ok.txt"]) == ["ok.txt"]


def test_rel_paths_absolute_inside(tmp_path: Path) -> None:
    inner = tmp_path / "nested" / "f.txt"
    inner.parent.mkdir()
    inner.write_text("x", encoding="utf-8")
    assert _rel_paths(tmp_path, [str(inner)]) == ["nested/f.txt"]


def test_rel_paths_absolute_outside(tmp_path: Path) -> None:
    other = tmp_path.parent / f"out-{tmp_path.name}"
    other.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="outside workspace"):
        _rel_paths(tmp_path, [str(other / "x.txt")])


@pytest.mark.parametrize(
    "name",
    ["-bad", "HEAD", "foo..bar", "a;rm", "refs/heads/x", "feat branch", ""],
)
def test_rejects_unsafe_branch_names(name: str) -> None:
    with pytest.raises(ValueError):
        validate_branch_name(name)


def test_accepts_normal_branch_names() -> None:
    assert validate_branch_name("feat/ui-1") == "feat/ui-1"
    assert validate_branch_name("main") == "main"


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "https://evil.com/x;rm", "javascript:alert(1)", ""],
)
def test_rejects_unsafe_remote_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_remote_url(url)


def test_accepts_https_and_ssh_remotes() -> None:
    assert validate_remote_url("https://github.com/a/b.git").startswith("https://")
    assert validate_remote_url("git@github.com:a/b.git").startswith("git@")


def _git(ws: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(ws: Path) -> None:
    _git(ws, "init")
    _git(ws, "config", "user.email", "t@t.test")
    _git(ws, "config", "user.name", "t")
    (ws / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    _git(ws, "add", "a.txt")
    _git(ws, "commit", "-m", "init")


def test_panel_snapshot_counts_edits(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello\nworld\nmore\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("one\ntwo\n", encoding="utf-8")
    snap = panel_snapshot(tmp_path)
    assert snap["is_repo"] is True
    assert snap["branch"]
    paths = {f["path"]: f for f in snap["files"]}
    assert "a.txt" in paths
    assert "new.txt" in paths
    assert paths["a.txt"]["added"] >= 1
    assert paths["new.txt"]["kind"] == "untracked"
    assert paths["new.txt"]["added"] == 2
    assert snap["totals"]["files"] >= 2
    assert snap["totals"]["added"] >= 3


def test_checkout_creates_and_switches(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base = current_branch(tmp_path)
    result = checkout_branch(tmp_path, "feature-x", create=True)
    assert not result.startswith("ERROR")
    assert current_branch(tmp_path) == "feature-x"
    result = checkout_branch(tmp_path, base)
    assert not result.startswith("ERROR")
    assert current_branch(tmp_path) == base
    names = {b["name"] for b in panel_snapshot(tmp_path)["branches"]}
    assert base in names
    assert "feature-x" in names


def test_file_change_pair_modified(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello\nworld\nmore\n", encoding="utf-8")
    pair = file_change_pair(tmp_path, "a.txt")
    assert pair["path"] == "a.txt"
    assert pair["kind"] == "modified"
    assert "hello" in pair["old"]
    assert "more" in pair["new"]
    assert pair["binary"] is False


def test_review_conversation_net_not_last_turn(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    fs_undo.push_checkpoint("sess-1", 0, workspace=tmp_path, user_text="生成")
    fs_undo.push_before_write("new.md", tmp_path / "new.md", tmp_path)
    (tmp_path / "new.md").write_text("alpha\n", encoding="utf-8")
    fs_undo.push_checkpoint("sess-1", 1, workspace=tmp_path, user_text="修改")
    fs_undo.push_before_write("new.md", tmp_path / "new.md", tmp_path)
    (tmp_path / "new.md").write_text("alpha\nbeta\n", encoding="utf-8")
    snap = review_panel_snapshot(tmp_path, session_id="sess-1")
    paths = {f["path"]: f for f in snap["files"]}
    assert "new.md" in paths
    assert paths["new.md"]["kind"] == "added"
    pair = file_change_pair(tmp_path, "new.md", session_id="sess-1")
    assert pair["kind"] == "added"
    assert pair["old"] == ""
    assert "beta" in pair["new"]
    git_snap = panel_snapshot(tmp_path)
    git_paths = {f["path"]: f for f in git_snap["files"]}
    assert git_paths["new.md"]["kind"] == "untracked"
