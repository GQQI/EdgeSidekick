"""Slice extracted pages into chapters; never dump whole book into one LLM call."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import PageText

# Soft caps for offline distill
DEFAULT_CHAPTER_CHARS = 8_000
MAX_CHAPTERS = 40
MIN_CHAPTER_CHARS = 400

_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,3}\s+.+|"
    r"第[一二三四五六七八九十百千零〇\d]+[章节篇部回卷]\s*.{0,80}|"
    r"Chapter\s+\d+[.:)\]\s].{0,80}|"
    r"CHAPTER\s+\d+[.:)\]\s].{0,80}|"
    r"Part\s+[IVXLC\d]+[.:)\]\s].{0,80})$"
)


@dataclass
class Chapter:
    index: int
    title: str
    text: str
    source_pages: str = ""


def _clean_title(raw: str, fallback: str) -> str:
    title = re.sub(r"^#+\s*", "", (raw or "").strip())
    title = re.sub(r"\s+", " ", title).strip() or fallback
    return title[:120]


def _split_by_headings(full: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(full))
    if len(matches) < 2:
        return []
    parts: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
        title = _clean_title(m.group(0), f"section-{i + 1}")
        body = full[start:end].strip()
        # drop the heading line itself from body start duplication mildly OK
        if len(body) < MIN_CHAPTER_CHARS:
            continue
        parts.append((title, body))
    return parts


def _chunk_text(text: str, *, size: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            # prefer break at paragraph / sentence
            window = text[start:end]
            br = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"), window.rfind(". "))
            if br >= size // 3:
                end = start + br + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end <= start:
            end = start + size
        start = end
    return chunks


def structure_chapters(
    pages: list[PageText],
    *,
    chapter_chars: int = DEFAULT_CHAPTER_CHARS,
    max_chapters: int = MAX_CHAPTERS,
) -> list[Chapter]:
    """Build chapter slices from pages. Caps count and per-chapter size."""
    if not pages:
        return []

    size = max(MIN_CHAPTER_CHARS, int(chapter_chars or DEFAULT_CHAPTER_CHARS))
    cap = max(1, int(max_chapters or MAX_CHAPTERS))

    full = "\n\n".join(p.text for p in pages)
    headed = _split_by_headings(full)

    raw_parts: list[tuple[str, str, str]] = []
    if headed:
        for title, body in headed:
            for j, piece in enumerate(_chunk_text(body, size=size)):
                t = title if j == 0 else f"{title} (cont. {j + 1})"
                raw_parts.append((t, piece, ""))
    else:
        # page-bundle fallback
        buf: list[str] = []
        page_from = pages[0].index
        page_to = pages[0].index
        for p in pages:
            if buf and sum(len(x) for x in buf) + len(p.text) > size:
                raw_parts.append(
                    (
                        f"pages-{page_from}-{page_to}",
                        "\n\n".join(buf),
                        f"{page_from}-{page_to}",
                    )
                )
                buf = []
                page_from = p.index
            buf.append(p.text)
            page_to = p.index
        if buf:
            raw_parts.append(
                (
                    f"pages-{page_from}-{page_to}",
                    "\n\n".join(buf),
                    f"{page_from}-{page_to}",
                )
            )

    # merge tiny leftovers into previous when possible
    merged: list[tuple[str, str, str]] = []
    for title, text, src in raw_parts:
        if merged and len(text) < MIN_CHAPTER_CHARS and len(merged[-1][1]) + len(text) <= size * 1.2:
            pt, pb, ps = merged[-1]
            merged[-1] = (pt, pb + "\n\n" + text, ps)
        else:
            merged.append((title, text, src))

    if len(merged) > cap:
        # keep first (cap-1) and fold the rest into a final "remainder" summary chunk
        head = merged[: cap - 1]
        rest_text = "\n\n".join(t[1] for t in merged[cap - 1 :])
        # further truncate remainder to one chapter budget (caller will distill, not dump all)
        rest_text = rest_text[: size]
        head.append((f"remaining-sections", rest_text, ""))
        merged = head

    chapters: list[Chapter] = []
    for i, (title, text, src) in enumerate(merged, start=1):
        clipped = text if len(text) <= size else text[:size]
        chapters.append(
            Chapter(
                index=i,
                title=_clean_title(title, f"chapter-{i}"),
                text=clipped,
                source_pages=src,
            )
        )
    return chapters
