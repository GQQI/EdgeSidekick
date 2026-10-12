"""Offline text extraction for books and common documents.

PDF uses pypdf. EPUB, Office, and HTML use the stdlib. Plain text is read directly.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


@dataclass
class PageText:
    index: int
    title: str
    text: str


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "section"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        raw = " ".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return parser.text()


def extract_pdf(path: Path) -> list[PageText]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pypdf is required for PDF extraction. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(str(path))
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = text.strip()
        if not text:
            continue
        pages.append(PageText(index=i + 1, title=f"page-{i + 1}", text=text))
    if not pages:
        raise ValueError(
            "PDF produced no extractable text (may be scanned/image-only; OCR not supported yet)"
        )
    return pages


def _epub_ns(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def extract_epub(path: Path) -> list[PageText]:
    """Best-effort EPUB text via stdlib only (no ebooklib)."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        container = "META-INF/container.xml"
        if container not in names:
            raise ValueError("not a valid EPUB (missing container.xml)")
        root = ET.fromstring(zf.read(container))
        rootfile = None
        for node in root.iter():
            if _epub_ns(node.tag) == "rootfile":
                rootfile = node.attrib.get("full-path")
                break
        if not rootfile or rootfile not in names:
            raise ValueError("EPUB rootfile missing")
        opf = ET.fromstring(zf.read(rootfile))
        opf_dir = str(Path(rootfile).parent).replace("\\", "/")
        if opf_dir == ".":
            opf_dir = ""

        id_to_href: dict[str, str] = {}
        for node in opf.iter():
            if _epub_ns(node.tag) == "item":
                item_id = node.attrib.get("id") or ""
                href = node.attrib.get("href") or ""
                if item_id and href:
                    id_to_href[item_id] = href

        spine_ids: list[str] = []
        for node in opf.iter():
            if _epub_ns(node.tag) == "itemref":
                ref = node.attrib.get("idref") or ""
                if ref:
                    spine_ids.append(ref)

        pages: list[PageText] = []
        for i, item_id in enumerate(spine_ids):
            href = id_to_href.get(item_id)
            if not href:
                continue
            rel = f"{opf_dir}/{href}" if opf_dir else href
            rel = rel.lstrip("/")
            if rel not in names:
                # try URL-decode-ish variants
                alt = rel.replace("%20", " ")
                if alt not in names:
                    continue
                rel = alt
            raw = zf.read(rel)
            try:
                html = raw.decode("utf-8")
            except UnicodeDecodeError:
                html = raw.decode("latin-1", errors="replace")
            text = _html_to_text(html)
            if not text:
                continue
            title = Path(href).stem
            pages.append(PageText(index=i + 1, title=title, text=text))
        if not pages:
            raise ValueError("EPUB produced no extractable text")
        return pages


_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".rst", ".json"}
_HTML_SUFFIXES = {".html", ".htm"}
_OFFICE_SUFFIXES = {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp", ".rtf"}
_LEGACY_OFFICE = {".doc", ".xls", ".ppt"}
SUPPORTED_BOOK_SUFFIXES = {".pdf", ".epub"} | _TEXT_SUFFIXES | _HTML_SUFFIXES | _OFFICE_SUFFIXES
_PAGE_CHARS = 6_000


def _pages_from_text(text: str, *, title: str) -> list[PageText]:
    body = (text or "").strip()
    if not body:
        raise ValueError("document produced no extractable text")
    pages: list[PageText] = []
    start = 0
    n = 1
    while start < len(body):
        end = min(len(body), start + _PAGE_CHARS)
        if end < len(body):
            window = body[start:end]
            br = max(window.rfind("\n\n"), window.rfind("\n"))
            if br >= _PAGE_CHARS // 3:
                end = start + br
        piece = body[start:end].strip()
        if piece:
            pages.append(PageText(index=n, title=title if n == 1 else f"{title}-{n}", text=piece))
            n += 1
        if end <= start:
            end = start + _PAGE_CHARS
        start = end
    if not pages:
        raise ValueError("document produced no extractable text")
    return pages


def extract_plain(path: Path) -> list[PageText]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in _HTML_SUFFIXES:
        raw = _html_to_text(raw)
    return _pages_from_text(raw, title=path.stem or "text")


def extract_office(path: Path) -> list[PageText]:
    from ..fs_api import extract_document_preview

    text, ok = extract_document_preview(path, max_chars=400_000)
    if not ok or not (text or "").strip():
        raise ValueError(f"no extractable text in {path.name}")
    return _pages_from_text(text, title=path.stem or "document")


def extract_document(path: Path) -> list[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".epub":
        return extract_epub(path)
    if suffix in _TEXT_SUFFIXES or suffix in _HTML_SUFFIXES:
        return extract_plain(path)
    if suffix in _OFFICE_SUFFIXES:
        return extract_office(path)
    if suffix in _LEGACY_OFFICE:
        raise ValueError(
            f"{suffix} is an old Office file. Save it as .docx, .xlsx, or .pptx and upload that."
        )
    raise ValueError(
        f"unsupported file type: {suffix or '(none)'} "
        "(use pdf, epub, docx, xlsx, pptx, or txt/markdown)"
    )


def join_pages(pages: Iterable[PageText], *, sep: str = "\n\n") -> str:
    return sep.join(p.text for p in pages if p.text)
