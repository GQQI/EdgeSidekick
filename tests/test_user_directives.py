"""Sticky user directives: extract, merge, and path deny matching."""

from __future__ import annotations

from metateam.services.user_directives import (
    DIRECTIVES_BLOCK_END,
    DIRECTIVES_BLOCK_HEAD,
    apply_directive_updates,
    extract_directive_updates,
    format_directives_block,
    merge_directives_block,
    path_is_denied,
)


def test_extract_deny_zh() -> None:
    updates = extract_directive_updates("请分析项目，但不要读取 requirements.txt 的内容")
    assert any(u.get("op") == "deny_read" and "requirements.txt" in u.get("target", "") for u in updates)


def test_extract_deny_en() -> None:
    updates = extract_directive_updates("Summarize the repo but do not read secrets.env")
    assert any(u.get("op") == "deny_read" and "secrets.env" in u.get("target", "").lower() for u in updates)


def test_clear_all_removes_denies() -> None:
    cur = apply_directive_updates([], [{"op": "deny_read", "target": "a.md"}])
    assert len(cur) == 1
    cleared = apply_directive_updates(cur, [{"op": "clear_all"}])
    assert cleared == []


def test_allow_one_removes_matching() -> None:
    cur = apply_directive_updates(
        [],
        [
            {"op": "deny_read", "target": "a.md"},
            {"op": "deny_read", "target": "b.md"},
        ],
    )
    next_dirs = apply_directive_updates(cur, [{"op": "allow_read", "target": "a.md"}])
    assert [d.target for d in next_dirs] == ["b.md"]


def test_path_is_denied_matches_basename() -> None:
    dirs = apply_directive_updates([], [{"op": "deny_read", "target": "secret.docx"}])
    assert path_is_denied("docs/secret.docx", dirs) == "secret.docx"
    assert path_is_denied("docs/other.docx", dirs) is None


def test_merge_inserts_before_memory() -> None:
    dirs = apply_directive_updates([], [{"op": "deny_read", "target": "x.pdf"}])
    system = "CORE\n\n## Memory\ncatalog\n"
    merged = merge_directives_block(system, dirs)
    assert DIRECTIVES_BLOCK_HEAD in merged
    assert "DENY read/open: x.pdf" in merged
    assert merged.index(DIRECTIVES_BLOCK_HEAD) < merged.index("## Memory")
    assert DIRECTIVES_BLOCK_END in merged


def test_format_empty_when_no_directives() -> None:
    assert format_directives_block([]) == ""
