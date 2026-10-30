"""Workspace path helpers and kind constants."""
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


_ACTIVE_WORKSPACE: ContextVar[Optional[Path]] = ContextVar(
    "metateam_active_workspace", default=None
)

SKIP_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".DS_Store"}

# —— Previewable kinds ——
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff", ".avif"}
PDF_EXTS = {".pdf"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".wma", ".opus"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}

# ZIP-based office / docs we can extract text from
DOCX_EXTS = {".docx"}
PPTX_EXTS = {".pptx"}
XLSX_EXTS = {".xlsx", ".xlsm"}
ODT_EXTS = {".odt"}
ODP_EXTS = {".odp"}
ODS_EXTS = {".ods"}
EPUB_EXTS = {".epub"}
RTF_EXTS = {".rtf"}

# Legacy binary Office — no reliable preview without converters
LEGACY_OFFICE_EXTS = {".doc", ".ppt", ".xls", ".pps"}

TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".csv",
    ".tsv",
    ".py",
    ".pyi",
    ".ipynb",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".less",
    ".html",
    ".htm",
    ".xml",
    ".xsl",
    ".xsd",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".r",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".vue",
    ".svelte",
    ".env",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".editorconfig",
    ".log",
    ".rst",
    ".tex",
    ".bib",
    ".dockerfile",
    ".makefile",
    ".cmake",
    ".gradle",
    ".properties",
    ".plist",
    ".lock",
    ".sum",
    ".mod",
    ".graphql",
    ".gql",
    ".proto",
    ".lua",
    ".pl",
    ".pm",
    ".dart",
    ".scala",
    ".clj",
    ".ex",
    ".exs",
    ".erl",
    ".hs",
    ".ml",
    ".mli",
    ".vim",
    ".diff",
    ".patch",
    ".srt",
    ".vtt",
    ".asc",
}


def bind_active_workspace(path: Path | str) -> Any:
    """Pin fs helpers to this agent's workspace for the current context/thread."""
    return _ACTIVE_WORKSPACE.set(resolve_path(Path(path)))


def reset_active_workspace(token: Any) -> None:
    _ACTIVE_WORKSPACE.reset(token)


def describe_fs_error(exc: BaseException) -> str:
    """Explain a filesystem error.

    ``FileNotFoundError(path)`` and ``FileExistsError(path)`` stringify to only
    the path, so callers were showing the filename as if it were the reason.
    """
    text = str(exc).strip()
    low = text.lower()
    if isinstance(exc, FileNotFoundError):
        if any(mark in low for mark in ("not found", "no such file", "cannot find", "找不到")):
            return text or "not found"
        return f"not found: {text}" if text else "not found"
    if isinstance(exc, FileExistsError):
        if any(mark in low for mark in ("already exists", "file exists", "已存在")):
            return text or "already exists"
        return f"already exists: {text}" if text else "already exists"
    return text or exc.__class__.__name__


def workspace_root() -> Path:
    bound = _ACTIVE_WORKSPACE.get()
    if bound is not None:
        return resolve_path(bound)
    return resolve_path(get_settings().workspace)


def safe_resolve(rel_or_path: str, *, allow_outside: bool = False) -> Path:
    """Resolve a path. By default stays inside the active workspace."""
    raw = (rel_or_path or ".").strip().strip('"').strip("'") or "."
    ws = workspace_root()
    p = normalize_user_path(raw, ws)
    if not allow_outside and not is_relative_to(p, ws):
        raise ValueError(f"path outside workspace: {p} (workspace is {ws})")
    return p


def rel_to_workspace(path: Path) -> str:
    ws = workspace_root()
    try:
        return relative_to_posix(path, ws)
    except ValueError:
        return str(path)


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def detect_kind(path: Path) -> str:
    """
    Return a UI preview kind:
    text | image | pdf | audio | video | document | unsupported
    """
    name = path.name.lower()
    ext = path.suffix.lower()
    if name in {"dockerfile", "makefile", "gemfile", "procfile", "cmakelists.txt"}:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in (
        DOCX_EXTS
        | PPTX_EXTS
        | XLSX_EXTS
        | ODT_EXTS
        | ODP_EXTS
        | ODS_EXTS
        | EPUB_EXTS
        | RTF_EXTS
    ):
        return "document"
    if ext in LEGACY_OFFICE_EXTS:
        return "unsupported"
    if ext in TEXT_EXTS:
        return "text"
    # No extension but looks like plain text → text; otherwise unsupported
    if ext == "":
        try:
            sample = path.read_bytes()[:2048]
            if sample and b"\x00" not in sample:
                return "text"
        except OSError:
            pass
    return "unsupported"
