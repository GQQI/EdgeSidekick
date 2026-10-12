"""Read / write / search workspace files."""

from __future__ import annotations

import re
from pathlib import Path

from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext
from .support import _needs_codebase_align


def _sync_skill_library(ctx: ToolContext, written: Path) -> str:
    from ...services.skills import load_skills, sync_workspace_skill

    try:
        note = sync_workspace_skill(
            written, ctx.live_ws(), ctx.settings.skills_dir, overwrite=True
        )
    except Exception as exc:
        return f"\nnote: skill library sync failed ({exc})"
    if not note:
        return ""
    try:
        ctx.skills[:] = load_skills(ctx.settings.skills_dir)
    except Exception:
        pass
    return f"\n{note}"


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    match = re.match(r"-?\d+", text)
    if not match:
        return default
    try:
        return int(match.group(0))
    except ValueError:
        return default


_OFFICE_DOC_EXTS = {
    ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".epub", ".rtf",
}
_PDF_EXTS = {".pdf"}
_LEGACY_OFFICE_EXTS = {".doc", ".ppt", ".xls", ".pps"}
_MEDIA_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif", ".tiff",
    ".avif", ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".mp4", ".webm",
    ".mov", ".mkv", ".avi", ".zip", ".7z", ".rar", ".tar", ".gz", ".exe", ".dll",
}


def _read_pdf_text(fp: Path, max_chars: int) -> str | None:
    """Best-effort PDF text via pypdf when it is installed; None if unavailable."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return None
    try:
        reader = PdfReader(str(fp))
        parts: list[str] = []
        total = 0
        for i, page in enumerate(reader.pages):
            chunk = (page.extract_text() or "").strip()
            if not chunk:
                continue
            block = f"## page {i + 1}\n{chunk}"
            parts.append(block)
            total += len(block)
            if total >= max_chars:
                break
        return "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: could not extract text from {fp.name}: {exc}"


def _read_document_text(fp: Path) -> tuple[str | None, str, str]:
    """(text, note, error) for non-plain-text documents.

    text=None and error="" means "not a document — decode as plain text".
    Office/OpenDocument/EPUB/RTF are extracted in-process; PDF needs pypdf.
    Media and archives return a clear error so the model does not fall back
    to unrelated tools (e.g. browser_navigate on a .docx).
    """
    ext = fp.suffix.lower()
    if ext in _OFFICE_DOC_EXTS:
        from ...services.fs_api import extract_document_preview

        text, ok = extract_document_preview(fp, max_chars=400_000)
        if not ok or not text.strip():
            return (
                None,
                "",
                f"ERROR: {fp.name} is a {ext} document but no text could be extracted "
                "(empty, image-only, or corrupted). Ask the user for a text/markdown export.",
            )
        note = (
            f"[document={ext.lstrip('.')} — text extracted from {fp.name}; "
            "layout/images are not included]"
        )
        return text, note, ""
    if ext in _PDF_EXTS:
        text = _read_pdf_text(fp, max_chars=400_000)
        if text is None:
            return (
                None,
                "",
                f"ERROR: {fp.name} is a PDF and this runtime has no PDF text extractor. "
                "Do NOT open it with browser_navigate. Ask the user to export it as "
                ".docx/.txt/.md, or run `pip install pypdf` and retry read_file.",
            )
        if text.startswith("ERROR:"):
            return None, "", text
        if not text.strip():
            return (
                None,
                "",
                f"ERROR: {fp.name} has no extractable text (scanned/image-only PDF). "
                "Ask the user for a text version.",
            )
        return text, f"[document=pdf — text extracted from {fp.name}]", ""
    if ext in _LEGACY_OFFICE_EXTS:
        return (
            None,
            "",
            f"ERROR: {fp.name} is a legacy binary Office file ({ext}); this runtime cannot "
            "read it. Ask the user to re-save it as .docx/.xlsx/.pptx and read that instead.",
        )
    if ext in _MEDIA_EXTS:
        return (
            None,
            "",
            f"ERROR: {fp.name} is binary ({ext}); read_file only returns text. "
            "Use list_dir for metadata, or describe what you need from the user.",
        )
    return None, "", ""


_DEFAULT_READ_CAP = 400  # chunk size when the remaining file is still large
_MAX_LIMIT = 500  # hard cap for one read_file call, even with an explicit limit
# If what's left fits here, return through EOF in one call (avoids a 468-line
# file being split at 400 and the model looping "read from line 401/193…").
_FINISH_IF_REMAINING = 500


def register_file_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    live_ws = ctx.live_ws
    align_state = ctx.align_state

    def read_file(
        path: str,
        offset: int = 1,
        limit: int = 0,
        encoding: str = "",
    ) -> str:
        """Read a text file. limit<=0 reads through EOF when the rest is modest."""
        from ...core.pathutil import nearby_file_names, resolve_existing_tool_path
        from ...core.textcodec import decode_path

        fp = resolve_existing_tool_path(path, live_ws())
        if not fp.exists():
            hint = nearby_file_names(fp)
            extra = f" Nearby files: {', '.join(hint)}" if hint else ""
            return (
                f"ERROR: not found: {fp}.{extra} "
                "Copy the exact name from list_dir/search_text "
                "(keep spaces, parentheses, '#', and punctuation). "
                "Do not simplify the filename or continue as if the file was read."
            )
        if fp.is_dir():
            return f"ERROR: {fp} is a directory; use list_dir"
        doc_text, doc_note, doc_err = _read_document_text(fp)
        if doc_err:
            return doc_err
        if doc_text is not None:
            text = doc_text
            used = "document"
        else:
            decoded = decode_path(fp, encoding=encoding or None)
            if not decoded.ok:
                return f"ERROR: {decoded.error}"
            text = decoded.text
            used = decoded.encoding
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        offset = max(1, _as_int(offset, 1))
        total = len(lines)
        if offset > total:
            return f"EOF: {fp.name} has {total} lines."
        req_limit = _as_int(limit, 0)
        auto_capped = False
        remaining = max(0, total - (offset - 1))
        if req_limit <= 0:
            if remaining <= _FINISH_IF_REMAINING:
                req_limit = remaining
            else:
                req_limit = _DEFAULT_READ_CAP
                auto_capped = True
        elif req_limit > _MAX_LIMIT:
            req_limit = _MAX_LIMIT
            auto_capped = True
        chunk = lines[offset - 1 : offset - 1 + req_limit]
        # Char budget: the trailer must describe what the model ACTUALLY gets.
        # If we served 468 lines but the executor later clipped the tail, the
        # dedupe ledger would claim the whole file is in context while the
        # model never saw the end — it then probes past EOF in a loop.
        cap_chars = max(
            4000, int(getattr(ctx.settings, "tool_result_cap", 18000) or 18000) - 800
        )
        served: list[str] = []
        used_chars = 0
        for idx, line in enumerate(chunk):
            row = f"{offset + idx}|{line}"
            if served and used_chars + len(row) + 1 > cap_chars:
                break
            served.append(row)
            used_chars += len(row) + 1
        body = "\n".join(served)
        actual_end = offset - 1 + len(served)
        char_capped = len(served) < len(chunk)
        meta = f"\n… lines {offset}-{actual_end} of {total}"
        if actual_end >= total:
            if offset == 1:
                meta += " (ENTIRE file)"
            else:
                meta += " (end of file)"
        else:
            meta += f"; next offset {actual_end + 1}"
            if char_capped:
                meta += " (chunk capped by size, not by the file)"
            elif auto_capped:
                meta += " (large file; this chunk is capped)"
        body += meta
        if doc_note:
            return f"{doc_note}\n{body}"
        if (encoding or "").strip() or used not in ("utf-8", "utf-8-sig"):
            return f"[encoding={used}]\n{body}"
        return body

    def write_file(
        path: str,
        content: str,
        force_create: bool = False,
        encoding: str = "",
    ) -> str:
        from ...services import fs_api
        from ...services import codebase_memory as cbm

        try:
            from ...core.pathutil import is_relative_to, relative_to_posix, resolve_existing_tool_path

            target = resolve_existing_tool_path(path, live_ws())
            text = "" if content is None else str(content)
            try:
                rel = relative_to_posix(target, live_ws()) if is_relative_to(target, live_ws()) else str(target)
            except Exception:
                rel = str(target)
            is_new = not target.exists()
            align_note = ""
            if (
                is_new
                and _needs_codebase_align(rel, live_ws())
                and not bool(force_create)
                and not align_state["aligned"]
            ):
                # Auto-align instead of hard-failing — models often skip codebase_find_similar.
                q = f"{Path(rel).stem} {Path(rel).suffix} {text[:240]}".strip()
                try:
                    index = cbm.get_or_build_index(live_ws())
                    hits = cbm.find_similar(index, q, limit=8)
                    align_state["aligned"] = True
                    align_state["queries"].append(q)
                    if hits:
                        paths: list[str] = []
                        for h in hits[:5]:
                            if isinstance(h, dict):
                                paths.append(str(h.get("path") or h.get("file") or h)[:80])
                            else:
                                paths.append(str(h)[:80])
                        align_note = (
                            "\nnote: similar existing files (prefer reuse next time): "
                            + ", ".join(paths)
                        )
                except Exception:
                    align_state["aligned"] = True

            res = fs_api.write_text(
                rel, text, allow_outside=True, encoding=encoding or None
            )
            cbm.invalidate_index(live_ws())
            note = f" encoding={encoding.strip()}" if (encoding or "").strip() else ""
            return (
                f"wrote {res['path']} ({res['size']} chars){note}{align_note}"
                + _sync_skill_library(ctx, target)
            )
        except Exception as exc:
            from ...services.fs_api import describe_fs_error

            return f"ERROR: {describe_fs_error(exc)}"

    def str_replace(
        path: str,
        old_string: str = "",
        new_string: str = "",
        replace_all: bool = False,
        encoding: str = "",
    ) -> str:
        from ...core.pathutil import is_relative_to, relative_to_posix, resolve_existing_tool_path
        from ...core.textcodec import decode_path
        from ...services import codebase_memory as cbm
        from ...services import fs_api
        from ...services.file_edit import apply_str_replace

        try:
            target = resolve_existing_tool_path(path, live_ws())
            if not target.exists() or not target.is_file():
                return f"ERROR: not found: {target}"
            try:
                decoded = decode_path(target, encoding=encoding or None)
                if not decoded.ok:
                    return f"ERROR: {decoded.error}"
                text = decoded.text
            except OSError as exc:
                from ...services.fs_api import describe_fs_error

                return f"ERROR: {describe_fs_error(exc)}"
            try:
                updated, n = apply_str_replace(
                    text, old_string, new_string, replace_all=bool(replace_all)
                )
            except ValueError as exc:
                return f"ERROR: {exc}"
            try:
                rel = (
                    relative_to_posix(target, live_ws())
                    if is_relative_to(target, live_ws())
                    else str(target)
                )
            except Exception:
                rel = str(target)
            res = fs_api.write_text(
                rel, updated, allow_outside=True, encoding=decoded.encoding
            )
            cbm.invalidate_index(live_ws())
            note = (
                f" encoding={decoded.encoding}"
                if decoded.encoding not in ("utf-8", "utf-8-sig")
                else ""
            )
            return (
                f"updated {res['path']} ({n} replacement{'s' if n != 1 else ''}){note}"
                + _sync_skill_library(ctx, target)
            )
        except Exception as exc:
            from ...services.fs_api import describe_fs_error

            return f"ERROR: {describe_fs_error(exc)}"

    def delete_file(path: str) -> str:
        from ...core.pathutil import nearby_file_names, resolve_existing_tool_path
        from ...services import codebase_memory as cbm
        from ...services import fs_api

        try:
            target = resolve_existing_tool_path(path, live_ws())
            if not target.exists():
                hint = nearby_file_names(target)
                extra = f" Nearby files: {', '.join(hint)}" if hint else ""
                return (
                    f"ERROR: not found: {target}.{extra} "
                    "Copy the exact path from list_dir or search_text."
                )
            # Delete the path already resolved. Passing only the relative form
            # makes delete_entry look it up again and report a bare filename
            # when that second lookup misses.
            res = fs_api.delete_entry(str(target), recursive=False, allow_outside=True)
            cbm.invalidate_index(live_ws())
            return f"deleted {res['path']}"
        except Exception as exc:
            from ...services.fs_api import describe_fs_error

            return f"ERROR: {describe_fs_error(exc)}"

    def list_dir(path: str = ".") -> str:
        try:
            from ...core.pathutil import resolve_existing_tool_path

            fp = resolve_existing_tool_path(path, live_ws())
            if not fp.exists():
                return f"ERROR: not found: {fp}"
            if fp.is_file():
                return f"FILE {fp}"
            entries = sorted(fp.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = [f"# {fp}"]
            for e in entries[:240]:
                lines.append(f"{'dir' if e.is_dir() else 'file'}\t{e.name}")
            if len(entries) > 240:
                lines.append(f"… {len(entries) - 240} more")
            return "\n".join(lines) or "(empty)"
        except OSError as exc:
            return f"ERROR: list_dir failed: {exc}"

    def search_text(
        query: str,
        path: str = ".",
        glob: str = "*",
        regex: bool = False,
    ) -> str:
        from ...core.pathutil import resolve_existing_tool_path
        from ...services.repo_search import search_text as repo_search_text

        try:
            base = resolve_existing_tool_path(path, live_ws())
        except OSError as exc:
            return f"ERROR: {exc}"
        return repo_search_text(
            live_ws(),
            query,
            path=base,
            glob=glob,
            regex=bool(regex),
        )

    reg.register(
        Tool(
            "read_file",
            f"Read a workspace file as numbered text. Handles plain text/code AND documents: "
            f".docx/.pptx/.xlsx/.odt/.epub/.rtf are converted to text automatically "
            f"(.pdf when pypdf is installed) — ALWAYS use read_file for local documents; "
            f"never browser_navigate. "
            f"offset and limit select a line range. "
            f"limit=0 reads through EOF when at most {_FINISH_IF_REMAINING} lines remain; "
            f"larger files come in {_DEFAULT_READ_CAP}-line chunks (hard max {_MAX_LIMIT} "
            f"lines per call). "
            "Use the exact filename (spaces and punctuation included). "
            "Default encoding is UTF-8 (or a BOM if the file has one). "
            "If decode fails, retry with encoding= set to another codec; "
            "do not continue as if the file was read. "
            "Paths outside the workspace need user approval, then are read as-is.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {
                        "type": "integer",
                        "default": 0,
                        "description": (
                            f"Lines to read (max {_MAX_LIMIT}). 0 reads onward, "
                            f"capped at {_DEFAULT_READ_CAP} for a large file."
                        ),
                        "maximum": _MAX_LIMIT,
                    },
                    "encoding": {
                        "type": "string",
                        "description": (
                            "Optional Python codec name. Omit for UTF-8 / BOM. "
                            "On ERROR, retry this tool with a different encoding."
                        ),
                    },
                },
                "required": ["path"],
            },
            read_file,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "write_file",
            "Create a new text file or fully rewrite one. Requires user approval. "
            "Both path and content are required — never omit content. "
            "The full content is written (no server-side truncation). "
            "Optional encoding= is a Python codec name (default UTF-8, or the "
            "file BOM when rewriting). "
            "Paths outside the workspace are allowed after the user confirms. "
            "To change an existing file, prefer str_replace (unique old_string). "
            "For NEW code modules, Sidekick auto-checks similar existing files "
            "(prefer codebase_find_similar first when reusing is likely). "
            "force_create=true skips the similarity note path.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {
                        "type": "string",
                        "description": (
                            "Optional Python codec name for the bytes on disk. "
                            "Omit for UTF-8 (or the existing file BOM)."
                        ),
                    },
                    "force_create": {
                        "type": "boolean",
                        "description": (
                            "Optional. Skips auto similarity note when creating a new code file."
                        ),
                    },
                },
                "required": ["path", "content"],
            },
            write_file,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "str_replace",
            "Surgically edit an existing text file by replacing an exact substring. "
            "Requires user approval. Paths outside the workspace are allowed after "
            "the user confirms. old_string must match exactly once unless "
            "replace_all=true. Prefer this over write_file for existing files. "
            "Pass the same encoding= that succeeded on read_file.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find (include enough context to be unique).",
                    },
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence when old_string is not unique.",
                    },
                    "encoding": {
                        "type": "string",
                        "description": (
                            "Optional Python codec name. Omit for UTF-8 / BOM. "
                            "On decode ERROR, retry with a different encoding."
                        ),
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            str_replace,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "delete_file",
            "Delete a file or empty directory. Requires user approval. "
            "Paths outside the workspace (and destructive deletes) are allowed "
            "only after the user confirms.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            delete_file,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "list_dir",
            "List files in a directory. Prefer WORKSPACE-relative paths. "
            "Absolute paths must exist on THIS host — do not reuse another "
            "machine's E:/ or C:\\ path.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
            list_dir,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "search_text",
            "Ripgrep-style recursive search. Skips .git, node_modules, venv, and "
            ".gitignore matches. glob filters by file name (e.g. *.py). "
            "regex=true treats query as a Python/rg regular expression. "
            "Returns path:line:text (capped at 50 hits).",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {
                        "type": "string",
                        "default": "*",
                        "description": "File name glob, e.g. *.py. Default * = all text files.",
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "If true, query is a regular expression.",
                    },
                },
                "required": ["query"],
            },
            search_text,
            parallel_safe=True,
        )
    )
