"""Catalog injection + keyword memory_search."""

from __future__ import annotations

from pathlib import Path

from metateam.services.memory import (
    MEMORY_BLOCK_HEAD,
    MEMORY_PINNED_HEAD,
    append_memory,
    format_memory_block,
    load_library,
    save_library,
    search_library,
)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "MEMORY.md"


def test_catalog_lists_enabled_without_full_body(tmp_path: Path) -> None:
    path = _path(tmp_path)
    long = "Prefer pytest for the backend.\n" + ("detail line\n" * 40)
    append_memory(path, long, title="测试习惯", tags="pytest")
    block = format_memory_block(path)
    assert MEMORY_BLOCK_HEAD in block
    assert "### Catalog" in block
    assert "测试习惯" in block
    assert "memory_search" in block
    # Full repeated body must not be dumped into the prompt.
    assert block.count("detail line") <= 1


def test_short_pin_injects_body(tmp_path: Path) -> None:
    path = _path(tmp_path)
    append_memory(path, "Always reply in Simplified Chinese.", title="语言", tags="pin")
    lib = load_library(path)
    mem = lib.categories[0].memories[0]
    mem.pin = True
    save_library(path, lib)
    block = format_memory_block(path)
    assert MEMORY_PINNED_HEAD in block
    assert "Always reply in Simplified Chinese." in block


def test_search_ranks_by_keyword(tmp_path: Path) -> None:
    path = _path(tmp_path)
    append_memory(path, "Use pnpm for UI installs.", title="前端包管理", tags="pnpm,ui")
    append_memory(path, "Run pytest -q before claiming done.", title="后端测试", tags="pytest")
    hit = search_library(path, "pytest")
    assert "后端测试" in hit
    assert "mem_" in hit
    miss = search_library(path, "kubernetes-cluster-xyz")
    assert "no matching" in miss
