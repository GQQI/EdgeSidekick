"""List / read / write workspace files."""
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

from .paths import (
    SKIP_NAMES, IMAGE_EXTS, PDF_EXTS, AUDIO_EXTS, VIDEO_EXTS,
    DOCX_EXTS, PPTX_EXTS, XLSX_EXTS, ODT_EXTS, ODP_EXTS, ODS_EXTS,
    EPUB_EXTS, RTF_EXTS, LEGACY_OFFICE_EXTS, TEXT_EXTS,
    workspace_root, safe_resolve, rel_to_workspace, detect_kind, guess_mime,
    describe_fs_error,
)
from .preview import extract_document_preview

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
        from ....core.textcodec import decode_path

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

    from ....core.textcodec import encode_text

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
    from metateam.workspace import fs_undo

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
            from ....core.textcodec import bom_encoding_of

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
    from metateam.workspace import fs_undo

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
