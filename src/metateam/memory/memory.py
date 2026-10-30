"""Memory library: categories → many notes, tags, user-selected (enabled) injection."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.note_text import compact_note, note_has_body

_LOCK = threading.Lock()
_DEFAULT_CAT_NAME = "通用"
_LIBRARY_NAME = "library.json"
_MAX_INJECT = 8000
_MAX_CATALOG = 6000
_MAX_PIN_BODY = 400
_MAX_SUMMARY = 80
_MAX_TAGS = 16
_MAX_TITLE = 80
_PIN_TAG_NAMES = frozenset({"pin", "always", "置顶"})

# The injected section is delimited so a long-lived session can replace it
# exactly. Note text is arbitrary markdown, so "the next ## heading" is not a
# reliable boundary. The end marker is an HTML comment (invisible in chat).
MEMORY_BLOCK_HEAD = "## Memory"
MEMORY_BLOCK_END = "<!-- /memory -->"
MEMORY_PINNED_HEAD = "## Memory (pinned)"


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _now() -> float:
    return time.time()


def _clean_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,，;；\s]+", raw)
    elif isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        parts = [str(raw)]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip().lstrip("#")[:32]
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= _MAX_TAGS:
            break
    return out


@dataclass
class MemoryItem:
    id: str = ""
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    pin: bool = False
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "enabled": bool(self.enabled),
            "pin": bool(self.pin),
            "updated_at": float(self.updated_at or 0),
        }


@dataclass
class MemoryCategory:
    id: str = ""
    name: str = ""
    memories: list[MemoryItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "memories": [m.to_dict() for m in self.memories],
        }


@dataclass
class MemoryLibrary:
    version: int = 1
    categories: list[MemoryCategory] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version or 1),
            "categories": [c.to_dict() for c in self.categories],
        }


def library_path(memory_file: Path) -> Path:
    return memory_file.parent / _LIBRARY_NAME


def _item_from_dict(raw: dict[str, Any]) -> MemoryItem:
    tags = _clean_tags(raw.get("tags"))
    pin = raw.get("pin") is True or any(t.lower() in _PIN_TAG_NAMES for t in tags)
    return MemoryItem(
        id=str(raw.get("id") or _nid("mem")),
        title=str(raw.get("title") or "")[:_MAX_TITLE],
        content=str(raw.get("content") or ""),
        tags=tags,
        enabled=raw.get("enabled", True) is not False,
        pin=bool(pin),
        updated_at=float(raw.get("updated_at") or 0),
    )


def item_is_pinned(mem: MemoryItem) -> bool:
    if mem.pin:
        return True
    return any(t.lower() in _PIN_TAG_NAMES for t in mem.tags)


def _summary_line(content: str, max_chars: int = _MAX_SUMMARY) -> str:
    flat = " ".join((content or "").split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1] + "…"


def _cat_from_dict(raw: dict[str, Any]) -> MemoryCategory:
    items = raw.get("memories") if isinstance(raw.get("memories"), list) else []
    return MemoryCategory(
        id=str(raw.get("id") or _nid("cat")),
        name=str(raw.get("name") or _DEFAULT_CAT_NAME).strip() or _DEFAULT_CAT_NAME,
        memories=[_item_from_dict(x) for x in items if isinstance(x, dict)],
    )


def _ensure_ids(lib: MemoryLibrary) -> None:
    if not lib.categories:
        lib.categories.append(
            MemoryCategory(id=_nid("cat"), name=_DEFAULT_CAT_NAME, memories=[])
        )
    seen_cat: set[str] = set()
    for cat in lib.categories:
        if not cat.id or cat.id in seen_cat:
            cat.id = _nid("cat")
        seen_cat.add(cat.id)
        if not (cat.name or "").strip():
            cat.name = _DEFAULT_CAT_NAME
        seen_mem: set[str] = set()
        for mem in cat.memories:
            if not mem.id or mem.id in seen_mem:
                mem.id = _nid("mem")
            seen_mem.add(mem.id)
            if not mem.updated_at:
                mem.updated_at = _now()


def _legacy_md_to_library(text: str) -> MemoryLibrary:
    body = (text or "").strip()
    cat = MemoryCategory(id=_nid("cat"), name=_DEFAULT_CAT_NAME, memories=[])
    if body and body not in {
        "# MEMORY",
        "# MEMORY\n\nDurable facts about the user and environment.",
        "# MEMORY\n\nDurable facts about the user and environment.\n\n- Prefer concise notes; no task progress logs.",
    }:
        title = "导入的 MEMORY.md"
        first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
        if first and first.upper() != "MEMORY" and len(first) <= _MAX_TITLE:
            title = first
        cat.memories.append(
            MemoryItem(
                id=_nid("mem"),
                title=title,
                content=body,
                tags=[],
                enabled=True,
                updated_at=_now(),
            )
        )
    return MemoryLibrary(version=1, categories=[cat])


def _sanitize_library(lib: MemoryLibrary) -> bool:
    """Fold stored repetition. Returns True when a note was rewritten."""
    changed = False
    for cat in lib.categories:
        for mem in cat.memories:
            compacted = compact_note(mem.content)
            if compacted != mem.content:
                mem.content = compacted
                changed = True
    return changed


def load_library(memory_file: Path) -> MemoryLibrary:
    path = library_path(memory_file)
    with _LOCK:
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            cats = raw.get("categories") if isinstance(raw, dict) else None
            lib = MemoryLibrary(
                version=int((raw or {}).get("version") or 1) if isinstance(raw, dict) else 1,
                categories=[_cat_from_dict(c) for c in (cats or []) if isinstance(c, dict)],
            )
            _ensure_ids(lib)
            if _sanitize_library(lib):
                # Persist the repair once, so a polluted note cannot keep
                # coming back through every load.
                _write_library_unlocked(memory_file, lib)
            return lib
        md = ""
        if memory_file.is_file():
            try:
                md = memory_file.read_text(encoding="utf-8")
            except OSError:
                md = ""
        lib = _legacy_md_to_library(md)
        _ensure_ids(lib)
        _sanitize_library(lib)
        _write_library_unlocked(memory_file, lib)
        return lib


def _write_library_unlocked(memory_file: Path, lib: MemoryLibrary) -> None:
    _ensure_ids(lib)
    _sanitize_library(lib)
    path = library_path(memory_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(lib.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    enabled = enabled_text(lib, max_chars=200_000)
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(
        (enabled.rstrip() + "\n") if enabled else "# MEMORY\n\n",
        encoding="utf-8",
    )


def save_library(memory_file: Path, lib: MemoryLibrary) -> MemoryLibrary:
    with _LOCK:
        _write_library_unlocked(memory_file, lib)
        return lib


def library_from_payload(payload: dict[str, Any] | MemoryLibrary) -> MemoryLibrary:
    if isinstance(payload, MemoryLibrary):
        lib = payload
    else:
        cats = payload.get("categories") if isinstance(payload, dict) else None
        lib = MemoryLibrary(
            version=int((payload or {}).get("version") or 1) if isinstance(payload, dict) else 1,
            categories=[_cat_from_dict(c) for c in (cats or []) if isinstance(c, dict)],
        )
    _ensure_ids(lib)
    _sanitize_library(lib)
    return lib


def enabled_items(lib: MemoryLibrary) -> list[tuple[MemoryCategory, MemoryItem]]:
    out: list[tuple[MemoryCategory, MemoryItem]] = []
    for cat in lib.categories:
        for mem in cat.memories:
            if mem.enabled and note_has_body(mem.content):
                out.append((cat, mem))
    return out


def enabled_text(lib: MemoryLibrary, max_chars: int = _MAX_INJECT) -> str:
    """Full enabled bodies (legacy MEMORY.md mirror / slash readers)."""
    parts: list[str] = []
    for cat, mem in enabled_items(lib):
        title = (mem.title or "未命名").strip()
        tags = ", ".join(mem.tags) if mem.tags else ""
        head = f"### {cat.name} / {title}"
        if tags:
            head += f"  [{tags}]"
        parts.append(head + "\n" + mem.content.strip())
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[memory truncated]"
    return text


def pinned_items(lib: MemoryLibrary) -> list[tuple[MemoryCategory, MemoryItem]]:
    """Short pinned notes whose full body may sit in the system prompt."""
    out: list[tuple[MemoryCategory, MemoryItem]] = []
    for cat, mem in enabled_items(lib):
        if not item_is_pinned(mem):
            continue
        body = (mem.content or "").strip()
        if len(body) > _MAX_PIN_BODY:
            continue
        out.append((cat, mem))
    return out


def catalog_text(lib: MemoryLibrary, max_chars: int = _MAX_CATALOG) -> str:
    """Index lines for enabled notes — bodies stay out of the prompt."""
    lines: list[str] = []
    for cat, mem in enabled_items(lib):
        title = (mem.title or "未命名").strip()
        tags = ",".join(mem.tags) if mem.tags else "-"
        pin = " pin" if item_is_pinned(mem) else ""
        summary = _summary_line(mem.content)
        lines.append(f"- [{cat.name}] {mem.id}  {title}  tags={tags}{pin}")
        if summary:
            lines.append(f"  summary: {summary}")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[catalog truncated]"
    return text


def pinned_text(lib: MemoryLibrary, max_chars: int = _MAX_INJECT) -> str:
    parts: list[str] = []
    for cat, mem in pinned_items(lib):
        title = (mem.title or "未命名").strip()
        tags = ", ".join(mem.tags) if mem.tags else ""
        head = f"### {cat.name} / {title}"
        if tags:
            head += f"  [{tags}]"
        parts.append(head + "\n" + mem.content.strip())
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[pinned truncated]"
    return text


def format_memory_block(memory_file: Path) -> str:
    """Inject catalog (+ short pinned bodies). Full notes via memory_search/read."""
    lib = load_library(memory_file)
    catalog = catalog_text(lib)
    pinned = pinned_text(lib)
    if not catalog and not pinned:
        return ""
    n = len(enabled_items(lib))
    parts = [
        MEMORY_BLOCK_HEAD,
        f"Memory catalog ({n} enabled). Bodies are NOT auto-injected. "
        "Call memory_search(query) then memory_read(memory_id=…) before using a note. "
        "Do not invent note content from titles alone. Do not store secrets.",
    ]
    if catalog:
        parts.append("### Catalog\n" + catalog)
    if pinned:
        parts.append(
            f"{MEMORY_PINNED_HEAD}\n"
            "Short user-pinned preferences (always in context):\n"
            + pinned
        )
    parts.append(MEMORY_BLOCK_END)
    return "\n".join(parts)


def _query_tokens(query: str) -> list[str]:
    raw = (query or "").strip().lower()
    if not raw:
        return []
    parts = re.split(r"[\s,，;；|]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip()
        if len(t) < 1:
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    if raw not in seen and len(raw) >= 2:
        out.insert(0, raw)
    return out


def _score_memory(mem: MemoryItem, cat: MemoryCategory, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    title = (mem.title or "").lower()
    tags = [t.lower() for t in mem.tags]
    content = (mem.content or "").lower()
    cat_name = (cat.name or "").lower()
    score = 0.0
    for tok in tokens:
        if not tok:
            continue
        if tok == title or tok in title:
            score += 10.0 if tok == title else 5.0
        if any(tok == t or tok in t or t in tok for t in tags):
            score += 8.0
        if tok in cat_name:
            score += 3.0
        if tok in content:
            score += 3.0 + min(2.0, content.count(tok) * 0.25)
    if item_is_pinned(mem):
        score += 0.5
    return score


def search_library(
    path: Path,
    query: str,
    *,
    category: str = "",
    tags: Any = None,
    top_k: int = 5,
    include_disabled: bool = False,
) -> str:
    """Keyword rank enabled (or all) notes; return catalog hits for memory_read."""
    lib = load_library(path)
    tokens = _query_tokens(query)
    if not tokens:
        return "ERROR: empty query"
    want_tags = {t.lower() for t in _clean_tags(tags)}
    cat_filter = find_category(lib, category) if category else None
    if category and not cat_filter:
        return f"ERROR: unknown category {category}"
    scored: list[tuple[float, MemoryCategory, MemoryItem]] = []
    for cat in lib.categories:
        if cat_filter and cat.id != cat_filter.id:
            continue
        for mem in cat.memories:
            if not include_disabled and not mem.enabled:
                continue
            if not note_has_body(mem.content):
                continue
            if want_tags:
                have = {t.lower() for t in mem.tags}
                if not want_tags.issubset(have) and not (want_tags & have):
                    continue
            s = _score_memory(mem, cat, tokens)
            if s > 0:
                scored.append((s, cat, mem))
    scored.sort(key=lambda row: (-row[0], -(row[2].updated_at or 0)))
    k = max(1, min(int(top_k or 5), 20))
    hits = scored[:k]
    if not hits:
        return "(no matching memory — try different keywords or memory_list)"
    lines = [f"Top {len(hits)} for {query!r} (use memory_read with memory_id):"]
    for s, cat, mem in hits:
        pin = " pin" if item_is_pinned(mem) else ""
        flag = "ON" if mem.enabled else "off"
        tag_s = ",".join(mem.tags) if mem.tags else "-"
        lines.append(
            f"- score={s:.1f} [{flag}{pin}] {mem.id}  {cat.name} / {mem.title or '未命名'}  tags={tag_s}"
        )
        summary = _summary_line(mem.content, 120)
        if summary:
            lines.append(f"  summary: {summary}")
    return "\n".join(lines)


def _join_sections(parts: list[str]) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def merge_memory_block(system: str, memory_file: Path) -> str:
    """Replace (or append) the memory section of a system message.

    A session's first system message is reused for the whole conversation, so
    a note edited or deleted later must be swapped out there as well.
    """
    block = format_memory_block(memory_file)
    text = str(system or "")
    start = text.find(MEMORY_BLOCK_HEAD)
    if start < 0:
        return _join_sections([text, block])
    before = text[:start]
    end = text.find(MEMORY_BLOCK_END, start)
    if end >= 0:
        return _join_sections([before, block, text[end + len(MEMORY_BLOCK_END) :]])
    # Sessions created before the end marker existed: fall back to the next
    # runtime section. Any extension block in between is re-added by the same
    # turn's apply_extensions_block pass.
    rest = text[start + len(MEMORY_BLOCK_HEAD) :]
    tail_parts = rest.split("\n## ", 1)
    after = ("## " + tail_parts[1]) if len(tail_parts) == 2 else ""
    return _join_sections([before, block, after])


def find_category(lib: MemoryLibrary, category: str) -> Optional[MemoryCategory]:
    raw = (category or "").strip()
    if not raw:
        return lib.categories[0] if lib.categories else None
    low = raw.lower()
    for cat in lib.categories:
        if cat.id == raw or cat.name.lower() == low:
            return cat
    return None


def ensure_category(lib: MemoryLibrary, category: str) -> MemoryCategory:
    hit = find_category(lib, category)
    if hit:
        return hit
    name = (category or "").strip() or _DEFAULT_CAT_NAME
    cat = MemoryCategory(id=_nid("cat"), name=name, memories=[])
    lib.categories.append(cat)
    return cat


def find_item(
    lib: MemoryLibrary, memory_id: str = "", match: str = ""
) -> Optional[tuple[MemoryCategory, MemoryItem]]:
    mid = (memory_id or "").strip()
    if mid:
        for cat in lib.categories:
            for mem in cat.memories:
                if mem.id == mid:
                    return cat, mem
    needle = (match or "").strip().lower()
    if needle:
        for cat in lib.categories:
            for mem in cat.memories:
                blob = f"{mem.title}\n{mem.content}\n{' '.join(mem.tags)}".lower()
                if needle in blob:
                    return cat, mem
    return None


def list_library_text(memory_file: Path) -> str:
    lib = load_library(memory_file)
    lines: list[str] = []
    for cat in lib.categories:
        lines.append(f"# {cat.name} ({cat.id})")
        if not cat.memories:
            lines.append("  (empty)")
            continue
        for mem in cat.memories:
            flag = "ON" if mem.enabled else "off"
            pin = "+pin" if item_is_pinned(mem) else ""
            tags = ",".join(mem.tags) if mem.tags else "-"
            title = mem.title or "未命名"
            lines.append(f"  [{flag}{pin}] {mem.id}  {title}  tags={tags}")
    return "\n".join(lines) if lines else "(empty library)"


def read_memory(path: Path, max_chars: int = 4000) -> str:
    """Enabled memories as plain text (legacy readers / slash command)."""
    lib = load_library(path)
    return enabled_text(lib, max_chars=max_chars)


def write_memory(path: Path, content: str) -> None:
    """Legacy: replace the first enabled item, or create one in 通用."""
    lib = load_library(path)
    text = compact_note(content if content is not None else "")
    enabled = enabled_items(lib)
    if enabled:
        _cat, mem = enabled[0]
        mem.content = text
        mem.updated_at = _now()
        if not mem.title:
            mem.title = "记忆"
    else:
        cat = ensure_category(lib, _DEFAULT_CAT_NAME)
        cat.memories.append(
            MemoryItem(
                id=_nid("mem"),
                title="记忆",
                content=text,
                tags=[],
                enabled=True,
                updated_at=_now(),
            )
        )
    save_library(path, lib)


def append_memory(
    path: Path,
    note: str,
    *,
    category: str = "",
    title: str = "",
    tags: Any = None,
    enabled: bool = True,
) -> str:
    note = compact_note((note or "").strip())
    if not note:
        return "empty note"
    if not note_has_body(note):
        return "note has no content outside its headings"
    lib = load_library(path)
    cat = ensure_category(lib, category)
    for mem in cat.memories:
        if note in (mem.content or "") or note == (mem.content or "").strip():
            return "already present"
    item = MemoryItem(
        id=_nid("mem"),
        title=(title or "").strip()[:_MAX_TITLE] or (note.splitlines()[0][:_MAX_TITLE]),
        content=note if note.startswith("- ") else note,
        tags=_clean_tags(tags),
        enabled=bool(enabled),
        updated_at=_now(),
    )
    cat.memories.append(item)
    save_library(path, lib)
    return f"saved {item.id} in {cat.name}" + (" (enabled)" if item.enabled else " (disabled)")


def remove_memory(path: Path, match: str = "", memory_id: str = "") -> str:
    if not (memory_id or match).strip():
        return "empty match"
    lib = load_library(path)
    hit = find_item(lib, memory_id=memory_id, match=match)
    if not hit:
        return f"not found: {(memory_id or match)[:80]}"
    cat, mem = hit
    cat.memories = [m for m in cat.memories if m.id != mem.id]
    save_library(path, lib)
    return f"removed {mem.id} ({mem.title or 'untitled'}) from {cat.name}"


def replace_memory(
    path: Path,
    content: str,
    *,
    memory_id: str = "",
    category: str = "",
    title: str = "",
    tags: Any = None,
) -> str:
    raw = content if content is not None else ""
    compacted = compact_note(str(raw))
    if str(raw).strip() and not note_has_body(compacted):
        return "note has no content outside its headings"
    lib = load_library(path)
    hit = find_item(lib, memory_id=memory_id) if memory_id else None
    if hit:
        _cat, mem = hit
        mem.content = compacted
        if title:
            mem.title = title.strip()[:_MAX_TITLE]
        if tags is not None:
            mem.tags = _clean_tags(tags)
        mem.updated_at = _now()
        save_library(path, lib)
        return f"replaced {mem.id}"
    return append_memory(
        path,
        compacted,
        category=category,
        title=title,
        tags=tags,
        enabled=True,
    )


def read_memory_detail(
    path: Path,
    *,
    category: str = "",
    tags: Any = None,
    memory_id: str = "",
    include_disabled: bool = True,
    max_chars: int = 8000,
) -> str:
    lib = load_library(path)
    want_tags = {t.lower() for t in _clean_tags(tags)}
    cat_filter = find_category(lib, category) if category else None
    if category and not cat_filter:
        return f"ERROR: unknown category {category}"
    parts: list[str] = []
    for cat in lib.categories:
        if cat_filter and cat.id != cat_filter.id:
            continue
        for mem in cat.memories:
            if memory_id and mem.id != memory_id:
                continue
            if not include_disabled and not mem.enabled:
                continue
            if want_tags:
                have = {t.lower() for t in mem.tags}
                if not want_tags.issubset(have) and not (want_tags & have):
                    continue
            flag = "ON" if mem.enabled else "off"
            tag_s = ",".join(mem.tags) if mem.tags else "-"
            parts.append(
                f"## [{flag}] {cat.name} / {mem.title or '未命名'} ({mem.id})\n"
                f"tags: {tag_s}\n\n{mem.content.strip()}"
            )
    if not parts:
        return "(no matching memory)"
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[truncated]"
    return text
