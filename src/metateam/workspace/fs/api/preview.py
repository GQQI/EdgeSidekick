"""Document preview extraction."""
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
    DOCX_EXTS, PPTX_EXTS, XLSX_EXTS, ODT_EXTS, ODP_EXTS, ODS_EXTS,
    EPUB_EXTS, RTF_EXTS, PDF_EXTS, TEXT_EXTS, LEGACY_OFFICE_EXTS,
)

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
