"""Codebase-as-Memory — project structure projection (not prose MEMORY.md).

Indexes workspace symbols/paths so the agent can align with what already exists
before inventing. Domain rules stay out of product code; the repo is the memory.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    ".sidekick",
    "sessions",
    ".cache",
}

CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".vue",
    ".svelte",
    ".css",
    ".scss",
    ".less",
    ".html",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
}

# Language-agnostic-ish symbol capture (best-effort, not a full parser).
_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?"
    r"(?:function|class|const|let|var|type|interface|enum|def|fn|func|struct|trait|impl)\s+"
    r"([A-Za-z_][\w]*)"
    r"|^class\s+([A-Za-z_][\w]*)"
    r"|^def\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)

_MAX_FILE_BYTES = 400_000
_MAX_FILES = 8_000
_INDEX_VERSION = 1


@dataclass
class SymbolHit:
    name: str
    path: str
    line: int


@dataclass
class FileEntry:
    path: str
    suffix: str
    size: int
    mtime: float
    symbols: list[str] = field(default_factory=list)


@dataclass
class CodebaseIndex:
    version: int = _INDEX_VERSION
    workspace: str = ""
    built_at: float = 0.0
    files: list[FileEntry] = field(default_factory=list)

    def file_count(self) -> int:
        return len(self.files)


def index_path(workspace: Path) -> Path:
    return workspace.resolve() / ".sidekick" / "codebase_index.json"


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _extract_symbols(text: str, limit: int = 40) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for m in _SYMBOL_RE.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if not name or name in seen:
            continue
        seen.add(name)
        line = text.count("\n", 0, m.start()) + 1
        out.append((name, line))
        if len(out) >= limit:
            break
    return out


def build_index(workspace: Path, *, max_files: int = _MAX_FILES) -> CodebaseIndex:
    ws = workspace.resolve()
    files: list[FileEntry] = []
    if not ws.exists():
        return CodebaseIndex(workspace=str(ws), built_at=time.time(), files=[])

    for fp in ws.rglob("*"):
        if len(files) >= max_files:
            break
        if not fp.is_file() or _should_skip(fp):
            continue
        suffix = fp.suffix.lower()
        if suffix and suffix not in CODE_SUFFIXES:
            continue
        try:
            st = fp.stat()
            if st.st_size > _MAX_FILE_BYTES:
                continue
            rel = fp.relative_to(ws).as_posix()
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        syms = [name for name, _ in _extract_symbols(text)]
        files.append(
            FileEntry(
                path=rel,
                suffix=suffix or "",
                size=st.st_size,
                mtime=st.st_mtime,
                symbols=syms,
            )
        )

    files.sort(key=lambda f: f.path.lower())
    return CodebaseIndex(workspace=str(ws), built_at=time.time(), files=files)


def save_index(workspace: Path, index: CodebaseIndex) -> Path:
    path = index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": index.version,
        "workspace": index.workspace,
        "built_at": index.built_at,
        "files": [asdict(f) for f in index.files],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_index(workspace: Path) -> Optional[CodebaseIndex]:
    path = index_path(workspace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if int(raw.get("version") or 0) != _INDEX_VERSION:
        return None
    files = [
        FileEntry(
            path=str(f.get("path") or ""),
            suffix=str(f.get("suffix") or ""),
            size=int(f.get("size") or 0),
            mtime=float(f.get("mtime") or 0),
            symbols=[str(s) for s in (f.get("symbols") or []) if s],
        )
        for f in (raw.get("files") or [])
        if f.get("path")
    ]
    return CodebaseIndex(
        version=_INDEX_VERSION,
        workspace=str(raw.get("workspace") or workspace),
        built_at=float(raw.get("built_at") or 0),
        files=files,
    )


def get_or_build_index(workspace: Path, *, force: bool = False, max_age_sec: float = 300) -> CodebaseIndex:
    ws = workspace.resolve()
    if not force:
        cached = load_index(ws)
        if cached and (time.time() - cached.built_at) <= max_age_sec:
            # Drop stale caches that still list deleted files / miss new ones lightly.
            if _cache_still_valid(ws, cached):
                return cached
    index = build_index(ws)
    try:
        save_index(ws, index)
    except OSError:
        pass
    return index


def _cache_still_valid(workspace: Path, index: CodebaseIndex) -> bool:
    """Reject cache if any indexed path is gone (common after manual deletes)."""
    if not index.files:
        # Empty index is fine only if workspace has no code files either — cheap probe.
        for fp in workspace.rglob("*"):
            if not fp.is_file() or _should_skip(fp):
                continue
            if fp.suffix.lower() in CODE_SUFFIXES:
                return False
            # non-code files don't matter
        return True
    missing = 0
    sample = index.files[:80]
    for fe in sample:
        if not (workspace / fe.path).is_file():
            missing += 1
            if missing >= 1:
                return False
    return True


def _tokens(text: str) -> list[str]:
    parts = re.split(r"[^A-Za-z0-9_]+", text.lower())
    return [p for p in parts if len(p) >= 2]


def find_similar(
    index: CodebaseIndex,
    query: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    q_tokens = _tokens(q)
    q_lower = q.lower()
    scored: list[tuple[float, dict[str, Any]]] = []

    for fe in index.files:
        score = 0.0
        reasons: list[str] = []
        path_l = fe.path.lower()
        base = Path(fe.path).stem.lower()

        if q_lower in path_l:
            score += 8.0
            reasons.append("path_contains_query")
        if q_lower == base or q_lower in base:
            score += 6.0
            reasons.append("filename_match")

        for tok in q_tokens:
            if tok in path_l:
                score += 1.5
            for sym in fe.symbols:
                sl = sym.lower()
                if tok == sl:
                    score += 5.0
                    reasons.append(f"symbol:{sym}")
                elif tok in sl or sl in tok:
                    score += 2.0
                    reasons.append(f"symbol_partial:{sym}")

        # light camel/snake overlap
        for sym in fe.symbols:
            sym_tokens = _tokens(sym)
            overlap = len(set(q_tokens) & set(sym_tokens))
            if overlap:
                score += overlap * 1.2

        if score <= 0:
            continue
        scored.append(
            (
                score,
                {
                    "path": fe.path,
                    "score": round(score, 2),
                    "symbols": fe.symbols[:12],
                    "reasons": list(dict.fromkeys(reasons))[:6],
                },
            )
        )

    scored.sort(key=lambda x: (-x[0], x[1]["path"]))
    return [item for _, item in scored[:limit]]


def find_references(
    workspace: Path,
    index: CodebaseIndex,
    symbol_or_path: str,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Best-effort text references across the indexed corpus."""
    needle = (symbol_or_path or "").strip()
    if not needle:
        return []
    ws = workspace.resolve()
    # If a path was passed, search by basename / stem too
    names = {needle}
    if "/" in needle or "\\" in needle:
        p = Path(needle)
        names.add(p.name)
        names.add(p.stem)
    hits: list[dict[str, Any]] = []

    for fe in index.files:
        fp = ws / fe.path
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if any(n in line for n in names):
                # skip self-definition-only noise lightly
                hits.append(
                    {
                        "path": fe.path,
                        "line": i,
                        "text": line.strip()[:200],
                    }
                )
                if len(hits) >= limit:
                    return hits
                break  # one hit per file is enough for blast radius skim
    return hits


def overview(index: CodebaseIndex, *, max_dirs: int = 24, max_symbols: int = 40) -> dict[str, Any]:
    by_suffix: dict[str, int] = {}
    top_dirs: dict[str, int] = {}
    symbols: list[str] = []
    for fe in index.files:
        by_suffix[fe.suffix or "(none)"] = by_suffix.get(fe.suffix or "(none)", 0) + 1
        top = fe.path.split("/", 1)[0] if fe.path else "."
        top_dirs[top] = top_dirs.get(top, 0) + 1
        for s in fe.symbols:
            if s not in symbols:
                symbols.append(s)
            if len(symbols) >= max_symbols:
                break

    dir_items = sorted(top_dirs.items(), key=lambda x: (-x[1], x[0]))[:max_dirs]
    suf_items = sorted(by_suffix.items(), key=lambda x: (-x[1], x[0]))
    return {
        "file_count": index.file_count(),
        "built_at": index.built_at,
        "top_dirs": [{"name": k, "files": v} for k, v in dir_items],
        "by_suffix": [{"suffix": k, "files": v} for k, v in suf_items[:20]],
        "sample_symbols": symbols[:max_symbols],
    }


def format_overview_block(index: CodebaseIndex, *, max_chars: int = 1800) -> str:
    ov = overview(index)
    lines = [
        "## Codebase memory (structure projection)",
        f"Indexed files: {ov['file_count']}",
        "Top dirs: "
        + ", ".join(f"{d['name']}({d['files']})" for d in ov["top_dirs"][:12]),
        "Suffixes: "
        + ", ".join(f"{s['suffix']}:{s['files']}" for s in ov["by_suffix"][:10]),
    ]
    if ov["sample_symbols"]:
        lines.append("Sample symbols: " + ", ".join(ov["sample_symbols"][:30]))
    lines.append(
        "Before creating new modules, call codebase_find_similar. "
        "Before risky edits, call codebase_impact. "
        "Prefer extending existing assets over parallel reimplementation."
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[truncated]"
    return text


def invalidate_index(workspace: Path) -> None:
    path = index_path(workspace)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
