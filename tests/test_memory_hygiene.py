"""Memory notes lose repetition at the storage boundary, not in the prompt."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from metateam.core.note_text import compact_note, note_has_body
from metateam.runtime.agent_grounding import AgentGroundingMixin
from metateam.services.memory import (
    MEMORY_BLOCK_END,
    append_memory,
    format_memory_block,
    library_path,
    load_library,
    merge_memory_block,
    replace_memory,
)

POLLUTED = "### 通用 / 记忆\n" * 88 + "# MEMORY\n"


def _memory_file(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "MEMORY.md"


class _PromptAgent(AgentGroundingMixin):
    """Minimal host for AgentGroundingMixin._refresh_memory_block."""

    def __init__(self, system: str, memory_file: Path) -> None:
        self.messages = [{"role": "system", "content": system}]
        self.settings = SimpleNamespace(memory_file=memory_file)
        self.is_subagent = False
        self.full_agent = True


def test_repeated_lines_are_folded() -> None:
    note = "Keep the build green.\nKeep the build green.\nKeep the build green."
    assert compact_note(note) == "Keep the build green."


def test_layout_differences_do_not_hide_a_repeat() -> None:
    note = "Keep the build green。\nKeep the build green！\nKeep the build green,"
    assert compact_note(note) == "Keep the build green。"


def test_blank_runs_collapse() -> None:
    assert compact_note("\n\nalpha\n\n\n\nbeta\n\n\n") == "alpha\n\nbeta"


def test_code_blocks_are_kept_verbatim() -> None:
    note = "Config:\n```\nx = 1\nx = 1\n```\n"
    assert compact_note(note) == note.rstrip()


def test_a_different_fence_marker_inside_code_is_not_a_fence() -> None:
    note = "```\n~~~\n~~~\n```\n"
    assert compact_note(note) == note.rstrip()


def test_headings_alone_are_not_body() -> None:
    assert note_has_body(POLLUTED) is False
    assert note_has_body("# MEMORY") is False
    assert note_has_body("### 通用 / 记忆\nUse pytest.\n") is True
    assert note_has_body("### 通用 / 记忆\n```\nx = 1\n```\n") is True


def test_a_note_without_body_is_not_stored(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    result = append_memory(path, POLLUTED)
    assert "no content" in result
    assert format_memory_block(path) == ""
    assert sum(len(c.memories) for c in load_library(path).categories) == 0


def test_a_repeated_body_is_stored_once(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    note = "Use pytest for the backend.\nUse pytest for the backend.\nRun the UI type check too."
    append_memory(path, note)
    # Catalog injects title + short summary, not the full multi-line body.
    block = format_memory_block(path)
    assert "Use pytest for the backend." in block
    assert "detail line" not in block or block.count("detail line") <= 1
    lib_note = load_library(path).categories[0].memories[0].content
    assert lib_note.splitlines().count("Use pytest for the backend.") == 1
    assert "Run the UI type check too." in lib_note


def test_a_polluted_library_is_repaired_on_load(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    path.parent.mkdir(parents=True)
    library_path(path).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [
                    {
                        "id": "cat_1",
                        "name": "通用",
                        "memories": [
                            {
                                "id": "mem_1",
                                "title": "记忆",
                                "content": POLLUTED,
                                "tags": [],
                                "enabled": True,
                                "updated_at": 1.0,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.write_text(POLLUTED, encoding="utf-8")

    assert format_memory_block(path) == ""

    raw = json.loads(library_path(path).read_text(encoding="utf-8"))
    content = raw["categories"][0]["memories"][0]["content"]
    assert content.count("通用 / 记忆") == 1
    assert path.read_text(encoding="utf-8").strip() == "# MEMORY"


def test_replace_rejects_a_repeat_only_note(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    append_memory(path, "Keep the tests green.")
    item = load_library(path).categories[0].memories[0]

    result = replace_memory(path, POLLUTED, memory_id=item.id)

    assert "no content" in result
    assert "Keep the tests green." in format_memory_block(path)


def test_merge_keeps_the_section_after_the_block(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    append_memory(path, "Keep the tests green.")
    system = (
        "CORE\n\n## Memory\nUser-selected notes.\nold note\n"
        f"{MEMORY_BLOCK_END}\n\n# Connected extensions\nmcp_demo\n\n"
        "## Workspace ground truth (authoritative)\nRoot: /x"
    )

    merged = merge_memory_block(system, path)

    assert "Keep the tests green." in merged
    assert "old note" not in merged
    assert "# Connected extensions" in merged
    assert "## Workspace ground truth" in merged


def test_a_long_lived_session_refreshes_its_memory_section(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    append_memory(path, "Keep the tests green.")
    stale = (
        "CORE\n\n## Memory\nUser-selected notes (1 active).\n"
        "### 通用 / 记忆\n### 通用 / 记忆\n\n"
        "## Workspace ground truth (authoritative)\nRoot: /x"
    )
    agent = _PromptAgent(stale, path)

    agent._refresh_memory_block()

    content = agent.messages[0]["content"]
    assert "Keep the tests green." in content
    assert "### 通用 / 记忆" not in content
    assert "## Workspace ground truth" in content


def test_an_emptied_library_removes_the_stale_section(tmp_path: Path) -> None:
    path = _memory_file(tmp_path)
    path.parent.mkdir(parents=True)
    library_path(path).write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [
                    {
                        "id": "cat_1",
                        "name": "通用",
                        "memories": [
                            {
                                "id": "mem_1",
                                "title": "记忆",
                                "content": POLLUTED,
                                "tags": [],
                                "enabled": True,
                                "updated_at": 1.0,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    stale = (
        "CORE\n\n## Memory\nUser-selected notes (1 active).\n"
        + "### 通用 / 记忆\n" * 88
        + "\n## Workspace ground truth (authoritative)\nRoot: /x"
    )
    agent = _PromptAgent(stale, path)

    agent._refresh_memory_block()

    content = agent.messages[0]["content"]
    assert "## Memory" not in content
    assert "通用 / 记忆" not in content
    assert "## Workspace ground truth" in content
