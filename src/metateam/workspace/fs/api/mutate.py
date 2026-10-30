"""Create / delete / rename / move / reveal."""
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

from .paths import workspace_root, safe_resolve, rel_to_workspace, describe_fs_error

def create_entry(rel: str, kind: str = "file") -> dict[str, Any]:
    from metateam.workspace import fs_undo

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
    from metateam.workspace import fs_undo

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
    from metateam.workspace import fs_undo

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
    from metateam.workspace import fs_undo

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
