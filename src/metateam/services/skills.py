"""Load SKILL.md packages from disk."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str = ""
    _body_loaded: bool = field(default=False, repr=False)

    def read_body(self) -> str:
        """Lazy-load SKILL.md body (keeps session create fast)."""
        if self._body_loaded:
            return self.body
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            self._body_loaded = True
            return self.body
        _, body = _parse_frontmatter(raw)
        self.body = body
        self._body_loaded = True
        return self.body


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


def _safe_rel(path: Path, root: Path) -> Path:
    rel = path.resolve().relative_to(root.resolve())
    if not rel.parts or ".." in rel.parts:
        raise ValueError(f"unsafe path: {path}")
    return rel


def _iter_skill_mds(src: Path) -> list[Path]:
    found: list[Path] = []
    direct = src / "SKILL.md"
    if direct.is_file():
        return [direct]
    for path in sorted(src.rglob("SKILL.md")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in path.relative_to(src).parts[:-1]):
            continue
        found.append(path)
        if len(found) > 20:
            raise ValueError("too many SKILL.md files in this folder")
    return found


def _copy_skill_package(src_root: Path, dest: Path, *, skip: Path) -> None:
    total = 0
    count = 0
    skip_res = skip.resolve()
    for path in src_root.rglob("*"):
        if not path.is_file():
            continue
        rel = _safe_rel(path, src_root)
        if any(part in _SKIP_DIR_NAMES for part in rel.parts):
            continue
        if path.name.lower() in _SKIP_FILE_NAMES:
            continue
        if path.resolve() == skip_res:
            continue
        if path.suffix.lower() not in _SKILL_FILE_SUFFIXES:
            continue
        size = path.stat().st_size
        if size > _MAX_PACKAGE_FILE:
            raise ValueError(f"{rel} is too large")
        total += size
        count += 1
        if count > _MAX_PACKAGE_FILES or total > _MAX_PACKAGE_TOTAL:
            raise ValueError("skill package is too large")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def import_skill_dir(
    skills_dir: Path,
    src: Path,
    *,
    overwrite: bool = False,
) -> list[Skill]:
    root = Path(src).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise ValueError("invalid directory") from exc
    if not root.is_dir():
        raise ValueError("not a directory")
    mds = _iter_skill_mds(root)
    if not mds:
        raise ValueError("directory has no SKILL.md")
    imported: list[Skill] = []
    for md in mds:
        text = md.read_text(encoding="utf-8")
        sk = import_skill_markdown(
            skills_dir,
            text,
            overwrite=overwrite,
            fallback_name=md.parent.name,
        )
        _copy_skill_package(md.parent, sk.path.parent, skip=md)
        imported.append(sk)
    invalidate_skills_cache(skills_dir)
    return imported


def import_skill_markdown(
    skills_dir: Path,
    text: str,
    *,
    overwrite: bool = False,
    fallback_name: str = "",
) -> Skill:
    parsed = validate_skill_text(text)
    name = str(parsed["name"] or slug_skill_name(fallback_name))
    desc = str(parsed["description"] or "")
    body = str(parsed["body"] or "")
    if not parsed["ok"] and name and desc and body:
        parsed = validate_skill_text(compose_skill_markdown(name, desc, body))
    if not parsed["ok"] and name and not desc:
        raise ValueError("; ".join(str(x) for x in (parsed["errors"] or [])))
    if not parsed["ok"]:
        raise ValueError("; ".join(str(x) for x in (parsed["errors"] or [])))
    return write_skill(
        skills_dir,
        name=str(parsed["name"] or name),
        description=str(parsed["description"] or desc),
        content=str(parsed["body"] or body),
        overwrite=overwrite,
    )




def skill_package_dir(sk: Skill) -> Path:
    """Directory that holds SKILL.md (the skill package root)."""
    return Path(sk.path).parent


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_skill_package_path(sk: Skill, rel_path: str) -> Path:
    """Resolve a relative path under the skill package; reject traversal."""
    root = skill_package_dir(sk).resolve()
    raw = (rel_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or raw.startswith("~"):
        raise ValueError("invalid path")
    # Drive-letter / absolute Windows paths
    if len(raw) >= 2 and raw[1] == ":":
        raise ValueError("absolute path rejected")
    parts = Path(raw).parts
    if not parts or ".." in parts:
        raise ValueError("path traversal rejected")
    if any(p in _SKIP_DIR_NAMES for p in parts[:-1]):
        raise ValueError("path not allowed")
    target = (root / Path(*parts)).resolve()
    if not _is_within(target, root):
        raise ValueError("path outside skill package")
    return target


def list_skill_package_files(sk: Skill) -> list[str]:
    """Flat list of relative POSIX paths under the skill package."""
    root = skill_package_dir(sk)
    try:
        root_res = root.resolve()
    except OSError:
        return []
    if not root_res.is_dir():
        return []
    files: list[str] = []
    total = 0
    for path in sorted(root_res.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root_res)
        except (ValueError, OSError):
            continue
        if any(part in _SKIP_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if path.name.lower() in _SKIP_FILE_NAMES:
            continue
        hidden_ok = {".gitignore", ".editorconfig", ".env.example"}
        if any(part.startswith(".") and part not in hidden_ok for part in rel.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_PACKAGE_FILE:
            continue
        total += size
        if len(files) >= _MAX_PACKAGE_FILES or total > _MAX_PACKAGE_TOTAL:
            break
        files.append(rel.as_posix())
    prefer = None
    for cand in ("SKILL.md", "skill.md"):
        if cand in files:
            prefer = cand
            break
    if prefer:
        files = [prefer] + [f for f in files if f != prefer]
    return files


_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".epub",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav", ".bin",
    ".pyc", ".pyo", ".class", ".o", ".a",
}


def read_skill_package_file(sk: Skill, rel_path: str) -> dict[str, object]:
    target = resolve_skill_package_path(sk, rel_path)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ValueError("cannot stat file") from exc
    if size > _MAX_PACKAGE_FILE:
        raise ValueError("file too large")
    root = skill_package_dir(sk).resolve()
    rel = target.relative_to(root).as_posix()
    suffix = target.suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        return {"path": rel, "binary": True, "size": size}
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("cannot read file") from exc
    if b"\x00" in raw[:8192]:
        return {"path": rel, "binary": True, "size": size}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "binary": True, "size": size}
    return {
        "path": rel,
        "content": text,
        "encoding": "utf-8",
        "binary": False,
        "size": size,
    }


def write_skill_package_file(
    sk: Skill,
    rel_path: str,
    content: str,
    *,
    skills_dir: Path | None = None,
) -> dict[str, object]:
    """Write a UTF-8 text file inside the skill package (no binary)."""
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if "\x00" in content:
        raise ValueError("binary content not allowed")
    target = resolve_skill_package_path(sk, rel_path)
    suffix = target.suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        raise ValueError("cannot write binary file as text")
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_PACKAGE_FILE:
        raise ValueError("file too large")
    root = skill_package_dir(sk).resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not _is_within(parent.resolve(), root):
        raise ValueError("path outside skill package")
    target.write_text(content, encoding="utf-8", newline="\n")
    if skills_dir is not None:
        invalidate_skills_cache(skills_dir)
    elif target.name.lower() == "skill.md":
        try:
            invalidate_skills_cache(root.parent)
        except Exception:
            pass
    rel = target.relative_to(root).as_posix()
    return {"path": rel, "encoding": "utf-8", "size": len(encoded), "binary": False}



def _package_stats(sk: Skill) -> tuple[int, int]:
    """Return (file_count, total_bytes) for files under the skill package."""
    root_dir = skill_package_dir(sk)
    try:
        root_res = root_dir.resolve()
    except OSError:
        return 0, 0
    if not root_res.is_dir():
        return 0, 0
    count = 0
    total = 0
    for path in root_res.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root_res)
        except (ValueError, OSError):
            continue
        if any(part in _SKIP_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if path.name.lower() in _SKIP_FILE_NAMES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        count += 1
        total += size
    return count, total


def create_skill_package_entry(
    sk: Skill,
    rel_path: str,
    *,
    entry_type: str = "file",
    content: str = "",
    skills_dir: Path | None = None,
) -> dict[str, object]:
    """Create an empty UTF-8 file or a folder under the skill package root."""
    kind = (entry_type or "file").strip().lower()
    if kind not in ("file", "folder"):
        raise ValueError("type must be file or folder")
    target = resolve_skill_package_path(sk, rel_path)
    root_dir = skill_package_dir(sk).resolve()
    if target.exists():
        raise ValueError("path already exists")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not _is_within(parent.resolve(), root_dir):
        raise ValueError("path outside skill package")
    count, total = _package_stats(sk)
    if kind == "folder":
        if count >= _MAX_PACKAGE_FILES:
            raise ValueError("skill package has too many files")
        target.mkdir(parents=False, exist_ok=False)
        if not _is_within(target.resolve(), root_dir):
            raise ValueError("path outside skill package")
        rel = target.relative_to(root_dir).as_posix()
        if skills_dir is not None:
            invalidate_skills_cache(skills_dir)
        return {
            "path": rel,
            "type": "folder",
            "files": list_skill_package_files(sk),
        }
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if "\x00" in content:
        raise ValueError("binary content not allowed")
    suffix = target.suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        raise ValueError("cannot create binary file as text")
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_PACKAGE_FILE:
        raise ValueError("file too large")
    if count >= _MAX_PACKAGE_FILES or total + len(encoded) > _MAX_PACKAGE_TOTAL:
        raise ValueError("skill package is too large")
    target.write_text(content, encoding="utf-8", newline="\n")
    if skills_dir is not None:
        invalidate_skills_cache(skills_dir)
    rel = target.relative_to(root_dir).as_posix()
    return {
        "path": rel,
        "type": "file",
        "encoding": "utf-8",
        "size": len(encoded),
        "binary": False,
        "files": list_skill_package_files(sk),
    }


def delete_skill_package_file(
    sk: Skill,
    rel_path: str,
    *,
    skills_dir: Path | None = None,
) -> dict[str, object]:
    """Delete a non-SKILL.md file (or empty folder) inside the skill package."""
    target = resolve_skill_package_path(sk, rel_path)
    root_dir = skill_package_dir(sk).resolve()
    rel = target.relative_to(root_dir).as_posix()
    if target.name.lower() == "skill.md":
        raise ValueError("cannot delete SKILL.md")
    if not target.exists():
        raise FileNotFoundError(rel_path)
    if target.is_dir():
        try:
            next(target.iterdir())
        except StopIteration:
            target.rmdir()
        else:
            raise ValueError("folder is not empty")
    elif target.is_file():
        target.unlink()
    else:
        raise ValueError("not a file or folder")
    if skills_dir is not None:
        invalidate_skills_cache(skills_dir)
    return {"path": rel, "files": list_skill_package_files(sk)}


def is_skill_markdown(path: Path) -> bool:
    return path.name.lower() == "skill.md"


def skill_package_root(file_path: Path, workspace: Path) -> Path | None:
    """Nearest folder that is a skill package, or None if this write is unrelated."""
    try:
        fp = Path(file_path).resolve()
        ws = Path(workspace).resolve()
    except OSError:
        return None
    start = fp.parent if fp.is_file() or is_skill_markdown(fp) else fp
    cur = start
    for _ in range(8):
        if (cur / "SKILL.md").is_file() or (cur / "skill.md").is_file():
            return cur
        if cur == ws or cur.parent == cur:
            break
        try:
            cur.relative_to(ws)
        except ValueError:
            break
        cur = cur.parent
    if is_skill_markdown(fp):
        return fp.parent
    return None


def sync_workspace_skill(
    file_path: Path,
    workspace: Path,
    skills_dir: Path,
    *,
    overwrite: bool = True,
) -> str:
    """Copy a workspace skill package into the library /skills can load.

    Workspace-root SKILL.md is imported as markdown only (no whole-tree copy).
    A subdirectory that contains SKILL.md is imported as a package, including
    companion scripts. Sidecar writes refresh that same package.
    """
    root = skill_package_root(file_path, workspace)
    if root is None:
        return ""
    try:
        ws = Path(workspace).resolve()
        pkg = root.resolve()
    except OSError:
        return ""
    try:
        if is_skill_markdown(Path(file_path)) and pkg == ws:
            text = Path(file_path).read_text(encoding="utf-8")
            sk = import_skill_markdown(
                skills_dir,
                text,
                overwrite=overwrite,
                fallback_name=Path(file_path).parent.name or "imported-skill",
            )
            return (
                f"installed skill '{sk.name}' into the skill library "
                f"({sk.path}). /skills can load it on the next turn."
            )
        if pkg == ws:
            return ""
        imported = import_skill_dir(skills_dir, pkg, overwrite=overwrite)
        if not imported:
            return ""
        names = ", ".join(sk.name for sk in imported)
        dest = imported[0].path.parent
        return (
            f"installed skill(s) {names} into the skill library ({dest}). "
            "/skills can load them on the next turn."
        )
    except FileExistsError as exc:
        return f"note: skill already exists in the library ({exc}); pass overwrite or skill_save"
    except (ValueError, OSError) as exc:
        return f"note: wrote the file but did not install a skill ({exc})"
