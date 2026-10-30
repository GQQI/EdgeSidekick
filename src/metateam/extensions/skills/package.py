"""Skill package import / file tree helpers."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from .models import Skill
from .load import (
    _parse_frontmatter,
    _read_skill_head,
    find_skill,
    invalidate_skills_cache,
    load_skills,
    slug_skill_name,
    write_skill,
)


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
