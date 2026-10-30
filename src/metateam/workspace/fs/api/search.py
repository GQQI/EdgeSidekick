"""Workspace text search."""
from __future__ import annotations

import mimetypes
import re
import shutil
import zipfile
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ....core.config import get_settings
from ....core.pathutil import is_relative_to, normalize_user_path, relative_to_posix, resolve_path

from .paths import SKIP_NAMES, workspace_root, safe_resolve, rel_to_workspace, describe_fs_error

def search_workspace(
    query: str,
    *,
    path: str = ".",
    max_hits: int = 80,
    max_lines_per_file: int = 80,
) -> dict[str, Any]:
    """Search file names and text file contents under the workspace.

    Content hits are grouped per file and include all matching line numbers
    (capped) so the UI can expand a file to show which lines matched.
    """
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": []}
    base = safe_resolve(path)
    if not base.exists():
        raise FileNotFoundError(path)
    q_low = q.lower()
    hits: list[dict[str, Any]] = []
    name_hits: list[dict[str, Any]] = []
    content_hits: list[dict[str, Any]] = []

    paths = [base] if base.is_file() else base.rglob("*")
    for fp in paths:
        if any(p in SKIP_NAMES for p in fp.parts):
            continue
        if not fp.exists():
            continue
        try:
            rel = rel_to_workspace(fp)
        except Exception:
            continue
        name = fp.name
        if q_low in name.lower() or q_low in rel.lower():
            name_hits.append(
                {
                    "path": rel,
                    "name": name,
                    "kind": "dir" if fp.is_dir() else "file",
                    "match": "name",
                    "line": 0,
                    "lines": [],
                    "matchCount": 0,
                    "snippet": rel,
                    "snippets": [],
                }
            )
        if not fp.is_file():
            continue
        if detect_kind(fp) != "text":
            continue
        try:
            if fp.stat().st_size > 1_500_000:
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines: list[int] = []
        snippets: list[dict[str, Any]] = []
        truncated = False
        for i, line in enumerate(text.splitlines(), 1):
            if q_low not in line.lower():
                continue
            if len(lines) >= max_lines_per_file:
                truncated = True
                # Soft-count a few more so badge isn't wildly off on huge files.
                if len(lines) >= max_lines_per_file + 200:
                    break
                lines.append(i)
                continue
            lines.append(i)
            snippets.append({"line": i, "text": line.strip()[:220]})
        if not lines:
            continue
        shown = lines[:max_lines_per_file]
        match_count = len(lines)
        content_hits.append(
            {
                "path": rel,
                "name": name,
                "kind": "file",
                "match": "content",
                "line": shown[0],
                "lines": shown,
                "matchCount": match_count,
                "truncated": truncated,
                "snippet": snippets[0]["text"] if snippets else "",
                "snippets": snippets,
            }
        )
        if len(name_hits) + len(content_hits) >= max_hits * 2:
            break

    # Prefer name matches, then content; one entry per file+match kind
    seen: set[str] = set()
    for item in [*name_hits, *content_hits]:
        key = f"{item['path']}|{item['match']}"
        if key in seen:
            continue
        seen.add(key)
        hits.append(item)
        if len(hits) >= max_hits:
            break
    return {"query": q, "hits": hits}
