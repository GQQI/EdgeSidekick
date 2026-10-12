"""Safe workspace filesystem helpers for the files API."""

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

from ..core.config import get_settings
from ..core.pathutil import is_relative_to, normalize_user_path, relative_to_posix, resolve_path

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


def list_entries(rel: str = ".") -> dict[str, Any]:
    base = safe_resolve(rel)
    if not base.exists():
        raise FileNotFoundError(str(base))
    if base.is_file():
        return {
            "path": rel_to_workspace(base),
            "abs_path": str(base),
            "name": base.name,
            "type": "file",
            "entries": [],
        }
    entries: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in SKIP_NAMES or child.name.startswith("."):
            continue
        item: dict[str, Any] = {
            "name": child.name,
            "path": rel_to_workspace(child),
            "abs_path": str(child.resolve()),
            "type": "dir" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else None,
        }
        if child.is_file():
            item["kind"] = detect_kind(child)
        entries.append(item)
    return {
        "path": rel_to_workspace(base) if base != workspace_root() else ".",
        "abs_path": str(base.resolve()),
        "name": base.name,
        "type": "dir",
        "entries": entries,
    }


def _xml_text_nodes(data: bytes) -> list[str]:
    """Collect readable text from OOXML / ODF / XHTML fragments."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: list[str] = []
    # OOXML w:t / a:t; ODF text:p/h/span; HTML p/div/span/… 
    text_tags = {
        "t",
        "a",
        "p",
        "h",
        "span",
        "s",
        "div",
        "li",
        "td",
        "th",
        "title",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
    break_tags = {"p", "h", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br"}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in text_tags and node.text:
            out.append(node.text)
        if node.tail and tag in text_tags | {"r", "span", "s", "a"}:
            out.append(node.tail)
        if tag in break_tags:
            out.append("\n")
    return out


def _zip_collect_texts(
    fp: Path,
    *,
    name_filter,
    max_chars: int,
    joiner: str = "",
    section_title: bool = True,
) -> str:
    parts: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(fp) as zf:
            names = sorted(n for n in zf.namelist() if name_filter(n))
            for name in names:
                try:
                    bits = _xml_text_nodes(zf.read(name))
                except KeyError:
                    continue
                if not bits:
                    continue
                body = joiner.join(bits)
                if not body.strip():
                    continue
                chunk = f"## {Path(name).name}\n{body}" if section_title else body
                parts.append(chunk)
                total += len(chunk)
                if total >= max_chars:
                    break
    except zipfile.BadZipFile:
        return ""
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        return text[:max_chars] + "\n…(truncated)"
    return text


def _extract_xlsx(fp: Path, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(fp) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root:
                    texts = [
                        (t.text or "")
                        for t in si.iter()
                        if str(t.tag).endswith("}t")
                    ]
                    shared.append("".join(texts))

            sheets = sorted(
                n
                for n in zf.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            blocks: list[str] = []
            total = 0
            for sheet in sheets:
                root = ET.fromstring(zf.read(sheet))
                rows_out: list[str] = []
                for row in root.iter():
                    if not str(row.tag).endswith("}row"):
                        continue
                    cells: list[str] = []
                    for c in row:
                        if not str(c.tag).endswith("}c"):
                            continue
                        cell_type = c.attrib.get("t")
                        v_node = None
                        for child in c:
                            if str(child.tag).endswith("}v"):
                                v_node = child
                                break
                        if v_node is None or v_node.text is None:
                            cells.append("")
                            continue
                        raw = v_node.text
                        if cell_type == "s":
                            try:
                                cells.append(shared[int(raw)])
                            except (ValueError, IndexError):
                                cells.append(raw)
                        else:
                            cells.append(raw)
                    if any(x.strip() for x in cells):
                        rows_out.append("\t".join(cells))
                if rows_out:
                    block = f"## {Path(sheet).stem}\n" + "\n".join(rows_out[:500])
                    blocks.append(block)
                    total += len(block)
                    if total >= max_chars:
                        break
            text = "\n\n".join(blocks)
            if len(text) > max_chars:
                return text[:max_chars] + "\n…(truncated)"
            return text
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return ""


def _extract_rtf(fp: Path, max_chars: int) -> str:
    raw = fp.read_text(encoding="utf-8", errors="ignore")
    # Strip common RTF control words; keep readable runs
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
    text = re.sub(r"\\[a-zA-Z]+\d* ?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[:max_chars] + " …(truncated)"
    return text


def extract_document_preview(fp: Path, max_chars: int = 80_000) -> tuple[str, bool]:
    """
    Returns (preview_text, ok).
    ok=False means this format/file cannot be previewed.
    """
    ext = fp.suffix.lower()
    if ext in DOCX_EXTS:
        text = _zip_collect_texts(
            fp,
            name_filter=lambda n: n == "word/document.xml",
            max_chars=max_chars,
            joiner="",
            section_title=False,
        )
        return (text, True) if text.strip() else ("", False)
    if ext in PPTX_EXTS:
        text = _zip_collect_texts(
            fp,
            name_filter=lambda n: n.startswith("ppt/slides/slide") and n.endswith(".xml"),
            max_chars=max_chars,
            joiner="",
            section_title=True,
        )
        return (text, True) if text.strip() else ("", False)
    if ext in XLSX_EXTS:
        text = _extract_xlsx(fp, max_chars)
        return (text, True) if text.strip() else ("", False)
    if ext in ODT_EXTS:
        text = _zip_collect_texts(
            fp,
            name_filter=lambda n: n == "content.xml",
            max_chars=max_chars,
            joiner="",
            section_title=False,
        )
        return (text, True) if text.strip() else ("", False)
    if ext in ODP_EXTS | ODS_EXTS:
        text = _zip_collect_texts(
            fp,
            name_filter=lambda n: n == "content.xml",
            max_chars=max_chars,
            joiner="",
            section_title=False,
        )
        return (text, True) if text.strip() else ("", False)
    if ext in EPUB_EXTS:
        text = _zip_collect_texts(
            fp,
            name_filter=lambda n: n.endswith((".xhtml", ".html", ".htm", ".xml")),
            max_chars=max_chars,
            joiner=" ",
            section_title=True,
        )
        return (text, True) if text.strip() else ("", False)
    if ext in RTF_EXTS:
        text = _extract_rtf(fp, max_chars)
        return (text, True) if text.strip() else ("", False)
    return ("", False)


def read_file(rel: str, max_chars: int = 200_000) -> dict[str, Any]:
    """Return typed file payload for the UI preview panel."""
    fp = safe_resolve(rel)
    if not fp.exists() or not fp.is_file():
        raise FileNotFoundError(rel)
    kind = detect_kind(fp)
    mime = guess_mime(fp)
    size = fp.stat().st_size
    base: dict[str, Any] = {
        "path": rel_to_workspace(fp),
        "name": fp.name,
        "kind": kind,
        "mime": mime,
        "size": size,
        "raw_url": f"/api/files/raw?path={quote(rel_to_workspace(fp), safe='/')}",
        "supported": kind != "unsupported",
        "message": "",
    }
    if kind == "text":
        from ..core.textcodec import decode_path

        decoded = decode_path(fp)
        if not decoded.ok:
            return {
                **base,
                "content": "",
                "preview": "",
                "truncated": False,
                "editable": True,
                "encoding": decoded.encoding,
                "message": decoded.error or "无法按 UTF-8 解码，请指定编码后重试",
            }
        text = decoded.text
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return {
            **base,
            "content": text,
            "preview": "",
            "truncated": truncated,
            "editable": True,
            "encoding": decoded.encoding,
            "message": "",
        }
    if kind == "document":
        preview, ok = extract_document_preview(fp, max_chars=min(max_chars, 100_000))
        if not ok:
            return {
                **base,
                "kind": "unsupported",
                "supported": False,
                "message": "暂不支持预览此文件",
                "content": "",
                "preview": "",
                "truncated": False,
                "editable": False,
            }
        return {
            **base,
            "content": "",
            "preview": preview,
            "truncated": False,
            "editable": False,
            "message": "文本预览（非完整排版）",
        }
    if kind == "unsupported":
        return {
            **base,
            "content": "",
            "preview": "",
            "truncated": False,
            "editable": False,
            "message": "暂不支持预览此文件",
        }
    # image / pdf / audio / video
    return {
        **base,
        "content": "",
        "preview": "",
        "truncated": False,
        "editable": False,
    }


def read_text(rel: str, max_chars: int = 200_000) -> dict[str, Any]:
    """Backward-compatible text reader used by older callers."""
    data = read_file(rel, max_chars=max_chars)
    if data.get("kind") == "text":
        return {
            "path": data["path"],
            "content": data.get("content") or "",
            "truncated": bool(data.get("truncated")),
            "size": data["size"],
            "kind": "text",
            "mime": data.get("mime"),
        }
    preview = data.get("preview") or data.get("message") or ""
    return {
        "path": data["path"],
        "content": preview,
        "truncated": False,
        "size": data["size"],
        "kind": data.get("kind"),
        "mime": data.get("mime"),
    }


def _atomic_write_text(fp: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text portably (Windows / Linux / 麒麟), keeping the caller's encoding."""
    import os as _os

    from ..core.textcodec import encode_text

    text = "" if content is None else str(content)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    payload = encode_text(text, encoding or "utf-8")
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(f".{fp.name}.{_os.getpid()}.tmp")
    try:
        tmp.write_bytes(payload)
        _os.replace(str(tmp), str(fp))
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        fp.write_bytes(payload)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def write_text(
    rel: str,
    content: str,
    *,
    allow_outside: bool = False,
    encoding: str | None = None,
) -> dict[str, Any]:
    from . import fs_undo

    text = "" if content is None else str(content)
    try:
        fp = safe_resolve(rel, allow_outside=allow_outside)
    except Exception as exc:
        raise ValueError(f"invalid path {rel!r}: {exc}") from exc
    if fp.exists() and detect_kind(fp) != "text":
        raise ValueError(f"cannot overwrite non-text file as text: {rel}")
    chosen = encoding
    if not chosen and fp.exists() and fp.is_file():
        try:
            from ..core.textcodec import bom_encoding_of

            chosen = bom_encoding_of(fp.read_bytes()[:4]) or "utf-8"
        except OSError:
            chosen = "utf-8"
    out = rel_to_workspace(fp)
    fs_undo.push_before_write(out, fp)
    try:
        _atomic_write_text(fp, text, encoding=chosen or "utf-8")
    except OSError as exc:
        raise OSError(
            f"write_file failed for {fp}: {exc}. "
            "Use a workspace-relative path that exists on THIS computer "
            "(do not reuse another machine's E:/ or C:\\ path)."
        ) from exc
    return {"path": out, "size": len(text)}


def write_bytes(rel: str, data: bytes) -> dict[str, Any]:
    from . import fs_undo

    fp = safe_resolve(rel)
    out = rel.replace("\\", "/")
    fs_undo.push_before_write(out, fp)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)
    return {
        "path": rel_to_workspace(fp),
        "size": len(data),
        "name": fp.name,
        "kind": detect_kind(fp),
        "mime": guess_mime(fp),
    }


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


def create_entry(rel: str, kind: str = "file") -> dict[str, Any]:
    from . import fs_undo

    fp = safe_resolve(rel)
    if fp.exists():
        raise FileExistsError(rel)
    out = rel.replace("\\", "/")
    fs_undo.push_before_create(out, kind)
    if kind == "dir":
        fp.mkdir(parents=True, exist_ok=False)
        return {"path": rel_to_workspace(fp), "type": "dir"}
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("", encoding="utf-8")
    return {"path": rel_to_workspace(fp), "type": "file"}


def delete_entry(
    rel: str, *, recursive: bool = False, allow_outside: bool = False, source: str = "tool"
) -> dict[str, Any]:
    from . import fs_undo

    fp = safe_resolve(rel, allow_outside=allow_outside)
    root = workspace_root().resolve()
    if fp.resolve() == root:
        raise ValueError("cannot delete workspace root")
    if not fp.exists():
        raise FileNotFoundError(rel)
    out_path = rel_to_workspace(fp)
    if fp.is_dir():
        try:
            next(fp.iterdir())
            nonempty = True
        except StopIteration:
            nonempty = False
        if nonempty and not recursive:
            raise ValueError(f"directory not empty: {rel}")
        fs_undo.push_before_delete(out_path, fp, source=source)
        if nonempty:
            shutil.rmtree(fp)
        else:
            fp.rmdir()
        return {"path": out_path, "type": "dir", "deleted": True}
    fs_undo.push_before_delete(out_path, fp, source=source)
    fp.unlink()
    return {"path": out_path, "type": "file", "deleted": True}


def rename_entry(rel: str, new_name: str) -> dict[str, Any]:
    """Rename a file or directory within the same parent folder."""
    from . import fs_undo

    src = safe_resolve(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    raw = (new_name or "").strip()
    if not raw or "/" in raw or "\\" in raw:
        raise ValueError("invalid name")
    name = raw
    if name in {".", ".."}:
        raise ValueError("invalid name")
    dest = resolve_path(src.parent / name)
    if not is_relative_to(dest, workspace_root()):
        raise ValueError(f"path outside workspace: {dest}")
    from_rel = rel_to_workspace(src)
    if dest == src:
        return {
            "path": rel_to_workspace(dest),
            "from": from_rel,
            "name": dest.name,
            "type": "dir" if dest.is_dir() else "file",
        }
    if dest.exists():
        raise FileExistsError(rel_to_workspace(dest))
    to_rel = rel_to_workspace(dest)
    fs_undo.push_before_rename(from_rel, to_rel)
    src.rename(dest)
    return {
        "path": rel_to_workspace(dest),
        "from": from_rel,
        "name": dest.name,
        "type": "dir" if dest.is_dir() else "file",
    }


def move_entry(rel: str, dest_dir: str) -> dict[str, Any]:
    """Move a file or directory into another directory under the workspace."""
    from . import fs_undo

    src = safe_resolve(rel)
    if not src.exists():
        raise FileNotFoundError(rel)
    root = workspace_root().resolve()
    if src.resolve() == root:
        raise ValueError("cannot move workspace root")

    dest_parent = safe_resolve(dest_dir or ".")
    if not dest_parent.exists() or not dest_parent.is_dir():
        raise ValueError(f"destination is not a directory: {dest_dir}")

    dest = resolve_path(dest_parent / src.name)
    if not is_relative_to(dest, root):
        raise ValueError(f"path outside workspace: {dest}")

    from_rel = rel_to_workspace(src)
    if dest == src.resolve():
        return {
            "path": from_rel,
            "from": from_rel,
            "to_dir": rel_to_workspace(dest_parent),
            "type": "dir" if src.is_dir() else "file",
        }

    # Refuse moving a directory into itself / a descendant
    if src.is_dir():
        src_res = src.resolve()
        if is_relative_to(dest, src_res) and dest != src_res:
            raise ValueError("cannot move a folder into itself")

    if dest.exists():
        raise FileExistsError(rel_to_workspace(dest))

    to_rel = rel_to_workspace(dest)
    fs_undo.push_before_move(from_rel, to_rel)
    shutil.move(str(src), str(dest))
    return {
        "path": to_rel,
        "from": from_rel,
        "to_dir": rel_to_workspace(dest_parent),
        "type": "dir" if dest.is_dir() else "file",
    }


def reveal_in_os(rel: str) -> dict[str, Any]:
    """Open the OS file manager at this workspace path (select file when possible)."""
    import platform
    import subprocess

    fp = safe_resolve(rel)
    if not fp.exists():
        raise FileNotFoundError(str(fp))
    abs_path = str(fp.resolve())
    system = platform.system()

    if system == "Windows":
        # explorer /select, needs a single argument with no space after the comma
        if fp.is_dir():
            subprocess.Popen(["explorer", abs_path], shell=False)  # noqa: S603
        else:
            subprocess.Popen(["explorer", f"/select,{abs_path}"], shell=False)  # noqa: S603
    elif system == "Darwin":
        if fp.is_dir():
            subprocess.Popen(["open", abs_path], shell=False)  # noqa: S603
        else:
            subprocess.Popen(["open", "-R", abs_path], shell=False)  # noqa: S603
    else:
        # Linux: open the containing folder (or the folder itself)
        target = abs_path if fp.is_dir() else str(fp.parent)
        subprocess.Popen(["xdg-open", target], shell=False)  # noqa: S603

    return {
        "status": "ok",
        "path": rel_to_workspace(fp),
        "abs_path": abs_path,
        "type": "dir" if fp.is_dir() else "file",
    }
