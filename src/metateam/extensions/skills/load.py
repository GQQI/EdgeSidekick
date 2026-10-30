"""Load / cache / validate SKILL.md packages."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .models import Skill

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

# Cache: skills_dir -> (fingerprint, skills)
_SKILLS_CACHE: dict[str, tuple[str, list[Skill]]] = {}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line:
            i += 1
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        # YAML block scalar: description: |  / >
        if val in ("|", ">", "|-", ">-", "|+", ">+") or val.startswith("|") or val.startswith(">"):
            block: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
                block.append(
                    nxt[2:] if nxt.startswith("  ") else nxt.lstrip("\t") if nxt.startswith("\t") else nxt
                )
                i += 1
            meta[key] = "\n".join(block).strip()
            continue
        meta[key] = val.strip("\"'")
        i += 1
    return meta, m.group(2).lstrip("\n")


def _read_skill_head(path: Path) -> tuple[dict[str, str], bool]:
    """Parse frontmatter without loading the full skill body when possible.

    Returns (meta, fully_read) — if fully_read, caller may also have body via a second read.
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            chunks: list[str] = []
            dashes = 0
            total = 0
            while total < 64_000:
                line = f.readline()
                if not line:
                    break
                chunks.append(line)
                total += len(line)
                if line.strip() == "---":
                    dashes += 1
                    if dashes >= 2:
                        break
            head = "".join(chunks)
    except OSError:
        return {}, False

    if dashes >= 2:
        meta, _ = _parse_frontmatter(head + "\n")
        return meta, False

    # No proper frontmatter fence — fall back to full file for meta only
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}, False
    meta, _ = _parse_frontmatter(raw)
    return meta, False


def _skills_fingerprint(skills_dir: Path) -> str:
    """Cheap invalidation key: count + latest mtime of SKILL.md files."""
    latest = 0.0
    count = 0
    try:
        for p in skills_dir.rglob("SKILL.md"):
            count += 1
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return "0:0"
    return f"{count}:{latest:.6f}"


def load_skills(skills_dir: Path, *, with_body: bool = False) -> list[Skill]:
    """Load skill packages. Bodies are lazy by default for fast session create."""
    if not skills_dir.exists():
        return []

    key = str(skills_dir.resolve())
    fp = _skills_fingerprint(skills_dir)
    cached = _SKILLS_CACHE.get(key)
    if cached and cached[0] == fp:
        skills = cached[1]
        if with_body:
            for s in skills:
                s.read_body()
        return skills

    skills: list[Skill] = []
    for path in sorted(skills_dir.rglob("SKILL.md")):
        meta, _ = _read_skill_head(path)
        name = meta.get("name") or path.parent.name
        desc = meta.get("description") or ""
        sk = Skill(name=name, description=desc, path=path, body="")
        if with_body:
            sk.read_body()
        skills.append(sk)

    # Prefer unique names; first wins
    seen: set[str] = set()
    uniq: list[Skill] = []
    for s in skills:
        if s.name in seen:
            continue
        seen.add(s.name)
        uniq.append(s)

    _SKILLS_CACHE[key] = (fp, uniq)
    return uniq


def get_skill(skills: list[Skill], name: str) -> Optional[Skill]:
    name = name.strip()
    for s in skills:
        if s.name == name:
            return s
    return None


def invalidate_skills_cache(skills_dir: Path | None = None) -> None:
    if skills_dir is None:
        _SKILLS_CACHE.clear()
        return
    try:
        key = str(Path(skills_dir).resolve())
    except OSError:
        key = str(skills_dir)
    _SKILLS_CACHE.pop(key, None)


def slug_skill_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "").lower()).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe[:64]


def compose_skill_markdown(name: str, description: str, body: str) -> str:
    slug = slug_skill_name(name)
    desc = " ".join((description or "").split())
    content = (body or "").strip()
    return f"---\nname: {slug}\ndescription: {desc}\n---\n\n{content}\n"


def validate_skill_text(text: str) -> dict[str, object]:
    """Return {ok, errors, warnings, name, description, body}."""
    errors: list[str] = []
    warnings: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return {
            "ok": False,
            "errors": ["内容为空"],
            "warnings": [],
            "name": "",
            "description": "",
            "body": "",
        }
    if len(raw) > 200_000:
        errors.append("内容超过 200KB")
    meta, body = _parse_frontmatter(raw)
    name = slug_skill_name(str(meta.get("name") or ""))
    if not name and not meta:
        warnings.append("缺少 YAML frontmatter（--- name / description ---），将无法被稳定识别")
    if not name:
        errors.append("name 无效：请使用 2–64 位字母、数字、连字符或下划线")
    elif len(name) < 2:
        errors.append("name 至少 2 个字符")
    desc = str(meta.get("description") or "").strip()
    if not desc:
        errors.append("description 不能为空")
    elif len(desc) < 8:
        errors.append("description 太短，请写清这个技能何时该被调用")
    body = (body or "").strip()
    if not body:
        errors.append("正文为空：需要可执行的步骤说明")
    elif len(body) < 20:
        errors.append("正文太短，请写明步骤")
    if body and "#" not in body:
        warnings.append("正文没有 Markdown 标题，建议用 ## 分段（何时使用 / 步骤）")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "name": name,
        "description": desc,
        "body": body,
    }


def _skill_dir_for(skills_dir: Path, name: str) -> Path:
    slug = slug_skill_name(name)
    if not slug:
        raise ValueError("invalid skill name")
    return Path(skills_dir) / slug


def find_skill(skills_dir: Path, name: str) -> Optional[Skill]:
    want = (name or "").strip()
    slug = slug_skill_name(want)
    for sk in load_skills(skills_dir):
        if sk.name == want or slug_skill_name(sk.name) == slug:
            return sk
        if skill_tool_match(sk.name, want):
            return sk
    return None


def skill_tool_match(skill_name: str, raw: str) -> bool:
    from ..runtime.tool_registry import skill_tool_name

    token = (raw or "").strip()
    return skill_tool_name(skill_name) == token or skill_tool_name(skill_name) == f"skill_{token}"


def write_skill(
    skills_dir: Path,
    *,
    name: str,
    description: str,
    content: str,
    overwrite: bool = True,
    previous: str | None = None,
) -> Skill:
    slug = slug_skill_name(name)
    if len(slug) < 2:
        raise ValueError("invalid skill name")
    check = validate_skill_text(compose_skill_markdown(slug, description, content))
    if not check["ok"]:
        raise ValueError("; ".join(str(x) for x in (check["errors"] or [])))

    dest = _skill_dir_for(skills_dir, slug)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "SKILL.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"skill already exists: {slug}")
    old: Skill | None = None
    old_name = slug_skill_name(previous or "")
    if old_name and old_name != slug:
        old = find_skill(skills_dir, old_name)
    path.write_text(compose_skill_markdown(slug, description, content), encoding="utf-8")
    if old is not None:
        try:
            if old.path.resolve() != path.resolve():
                _remove_skill_path(skills_dir, old.path)
        except OSError:
            pass

    invalidate_skills_cache(skills_dir)
    found = find_skill(skills_dir, slug)
    if found is None:
        raise RuntimeError("skill write failed")
    return found


def delete_skill(skills_dir: Path, name: str) -> str:
    sk = find_skill(skills_dir, name)
    if sk is None:
        raise FileNotFoundError(name)
    removed = sk.name
    _remove_skill_path(skills_dir, sk.path)
    invalidate_skills_cache(skills_dir)
    return removed


def _remove_skill_path(skills_dir: Path, skill_md: Path) -> None:
    root = Path(skills_dir).resolve()
    try:
        target = skill_md.resolve()
    except OSError as exc:
        raise ValueError("invalid skill path") from exc
    if not str(target).startswith(str(root)):
        raise ValueError("skill path outside skills dir")
    folder = target.parent
    if folder == root:
        target.unlink(missing_ok=True)
        return
    import shutil

    shutil.rmtree(folder, ignore_errors=False)


_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    "dist",
    "runtime",
    "wheels",
    "py",
}
_SKIP_FILE_NAMES = {".ds_store", "thumbs.db"}
_SKILL_FILE_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".csv",
    ".tsv",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".xml",
    ".sql",
    ".jinja",
    ".j2",
}
_MAX_PACKAGE_FILE = 2_000_000
_MAX_PACKAGE_TOTAL = 8_000_000
_MAX_PACKAGE_FILES = 80
