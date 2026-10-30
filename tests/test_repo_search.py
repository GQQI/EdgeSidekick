from __future__ import annotations

from pathlib import Path

from metateam.services.repo_search import search_text


def _search(ws: Path, query: str, **kwargs: object) -> str:
    return search_text(ws, query, engine="walk", **kwargs)  # type: ignore[arg-type]


def test_skips_node_modules(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("needle here\n", encoding="utf-8")
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "index.js").write_text("needle here\n", encoding="utf-8")
    out = _search(tmp_path, "needle")
    assert "src/app.py:1:" in out.replace("\\", "/")
    assert "node_modules" not in out


def test_honors_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("secret.log\n*.tmp\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("findme\n", encoding="utf-8")
    (tmp_path / "secret.log").write_text("findme\n", encoding="utf-8")
    (tmp_path / "x.tmp").write_text("findme\n", encoding="utf-8")
    out = _search(tmp_path, "findme")
    assert "ok.py:1:" in out
    assert "secret.log" not in out
    assert ".tmp" not in out


def test_glob_and_regex(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("alpha_1\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("alpha_1\n", encoding="utf-8")
    py = _search(tmp_path, "alpha", glob="*.py")
    assert "a.py" in py
    assert "b.md" not in py
    rx = _search(tmp_path, r"alpha_\d", regex=True)
    assert "a.py:1:" in rx


def test_skips_binary(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("payload\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"payload\x00more")
    out = _search(tmp_path, "payload")
    assert "plain.txt" in out
    assert "blob.bin" not in out


def test_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    assert _search(tmp_path, "zzz") == "no matches"
