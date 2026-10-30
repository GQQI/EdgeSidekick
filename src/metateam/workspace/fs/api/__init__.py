"""FS API package."""

from .paths import (
    SKIP_NAMES, IMAGE_EXTS, PDF_EXTS, AUDIO_EXTS, VIDEO_EXTS,
    DOCX_EXTS, PPTX_EXTS, XLSX_EXTS, ODT_EXTS, ODP_EXTS, ODS_EXTS,
    EPUB_EXTS, RTF_EXTS, LEGACY_OFFICE_EXTS, TEXT_EXTS,
    bind_active_workspace, reset_active_workspace, describe_fs_error,
    workspace_root, safe_resolve, rel_to_workspace, guess_mime, detect_kind,
)
from .preview import extract_document_preview
from .io import (
    list_entries, read_file, read_text, write_text, write_bytes,
)
from .search import search_workspace
from .mutate import create_entry, delete_entry, rename_entry, move_entry, reveal_in_os

__all__ = [
    "SKIP_NAMES", "IMAGE_EXTS", "PDF_EXTS", "AUDIO_EXTS", "VIDEO_EXTS",
    "DOCX_EXTS", "PPTX_EXTS", "XLSX_EXTS", "ODT_EXTS", "ODP_EXTS", "ODS_EXTS",
    "EPUB_EXTS", "RTF_EXTS", "LEGACY_OFFICE_EXTS", "TEXT_EXTS",
    "bind_active_workspace", "reset_active_workspace", "describe_fs_error",
    "workspace_root", "safe_resolve", "rel_to_workspace", "guess_mime", "detect_kind",
    "extract_document_preview",
    "list_entries", "read_file", "read_text", "write_text", "write_bytes",
    "search_workspace",
    "create_entry", "delete_entry", "rename_entry", "move_entry", "reveal_in_os",
]
