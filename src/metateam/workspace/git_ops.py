"""Git helpers for workspace-scoped agent tools (no shell=True)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _run_git(workspace: Path, args: list[str], *, timeout: float = 60.0) -> tuple[int, str, str]:
    ws = workspace.resolve()
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ws),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout}s"


def _fail(code: int, out: str, err: str, prefix: str) -> str:
    detail = (err or out).strip() or "unknown error"
    return f"ERROR: {prefix} failed ({code}): {detail}"


def validate_branch_name(name: str) -> str:
    n = (name or "").strip().replace("\\", "/")
    if (
        not n
        or n in {".", "..", "HEAD"}
        or n.startswith("-")
        or n.startswith("/")
        or n.startswith("refs/")
        or n.endswith("/")
        or n.endswith(".lock")
        or ".." in n
        or "//" in n
        or "@{" in n
        or not _BRANCH_RE.fullmatch(n)
    ):
        raise ValueError("invalid branch name")
    return n


def validate_remote_url(url: str) -> str:
    u = (url or "").strip()
    if not u or len(u) > 500 or any(c in u for c in " \n\r\t;|&$`()"):
        raise ValueError("invalid remote url")
    if not (u.startswith(("https://", "http://", "ssh://", "git@"))):
        raise ValueError("invalid remote url")
    return u


def is_git_repo(workspace: Path) -> bool:
    code, out, _ = _run_git(workspace, ["rev-parse", "--is-inside-work-tree"], timeout=10)
    return code == 0 and out.strip().lower() == "true"


def git_status(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    code, out, err = _run_git(workspace, ["status", "--short", "--branch"])
    if code != 0:
        return f"ERROR: git status failed ({code}): {err.strip() or out.strip()}"
    return out.strip() or "(clean)"


def git_diff(workspace: Path, *, staged: bool = False, path: str = "") -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    args = ["diff"]
    if staged:
        args.append("--cached")
    rel = (path or "").strip()
    if rel:
        args.extend(["--", rel])
    code, out, err = _run_git(workspace, args, timeout=90)
    if code != 0 and not out.strip():
        return f"ERROR: git diff failed ({code}): {err.strip() or out.strip()}"
    text = out.strip()
    if len(text) > 24_000:
        text = text[:24_000] + "\n…[diff truncated]"
    return text or "(no diff)"


def git_log(workspace: Path, *, limit: int = 12) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    n = max(1, min(int(limit), 40))
    code, out, err = _run_git(
        workspace,
        ["log", f"-{n}", "--oneline", "--decorate"],
        timeout=30,
    )
    if code != 0:
        return f"ERROR: git log failed ({code}): {err.strip() or out.strip()}"
    return out.strip() or "(no commits)"


def git_branch(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    code, out, err = _run_git(workspace, ["branch", "-vv"])
    if code != 0:
        return f"ERROR: git branch failed ({code}): {err.strip() or out.strip()}"
    return out.strip() or "(no branches)"


def git_commit(workspace: Path, message: str) -> str:
    """Stage tracked modifications + create commit. Does not force-add untracked files."""
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    msg = (message or "").strip()
    if not msg:
        return "ERROR: empty commit message"
    if len(msg) > 2000:
        return "ERROR: commit message too long"

    code, out, err = _run_git(workspace, ["add", "-u"])
    if code != 0:
        return f"ERROR: git add -u failed ({code}): {err.strip() or out.strip()}"

    code, out, err = _run_git(workspace, ["commit", "-m", msg], timeout=60)
    if code != 0:
        detail = (err or out).strip()
        return f"ERROR: git commit failed ({code}): {detail or 'nothing to commit?'}"
    return (out or err).strip() or "committed"


def _rel_paths(workspace: Path, paths: list[str]) -> list[str]:
    ws = workspace.resolve()
    out: list[str] = []
    for raw in paths:
        text = str(raw or "").strip().replace("\\", "/")
        if not text or text.startswith("-"):
            continue
        p = Path(text)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(ws)
            except ValueError as exc:
                raise ValueError(f"path outside workspace: {text}") from exc
            text = rel.as_posix()
        else:
            text = text.lstrip("/")
        if text:
            out.append(text)
    return out


def current_branch(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return ""
    code, out, _ = _run_git(workspace, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
    if code != 0:
        return ""
    return out.strip()


def _file_kind(xy: str) -> str:
    if xy == "??":
        return "untracked"
    letters = xy.replace(" ", "")
    if "D" in letters and "A" not in letters:
        return "deleted"
    if "A" in letters:
        return "added"
    if "R" in letters:
        return "renamed"
    return "modified"


def _parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for line in (text or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        if " => " in path:
            path = path.split(" => ", 1)[-1].strip().strip("{}")
        path = path.strip().strip('"')
        if not path:
            continue
        added = 0 if added_s == "-" else int(added_s or 0)
        deleted = 0 if deleted_s == "-" else int(deleted_s or 0)
        stats[path] = (added, deleted)
    return stats


def _count_text_lines(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if len(data) > 2_000_000:
        data = data[:2_000_000]
    if b"\0" in data[:8192]:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def line_stats(workspace: Path) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    code, out, _ = _run_git(workspace, ["diff", "--numstat", "HEAD"], timeout=30)
    if code == 0:
        stats.update(_parse_numstat(out))
    code, out, _ = _run_git(workspace, ["ls-files", "--others", "--exclude-standard"], timeout=20)
    if code != 0:
        return stats
    ws = workspace.resolve()
    for rel in (out or "").splitlines():
        rel = rel.strip().strip('"')
        if not rel or rel in stats:
            continue
        stats[rel] = (_count_text_lines(ws / rel), 0)
    return stats


def porcelain_entries(workspace: Path) -> list[dict[str, Any]]:
    if not is_git_repo(workspace):
        return []
    code, out, err = _run_git(workspace, ["status", "--porcelain=v1", "-uall"], timeout=30)
    if code != 0:
        raise RuntimeError(err.strip() or out.strip() or "git status failed")
    stats = line_stats(workspace)
    items: list[dict[str, Any]] = []
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[-1]
        path = path.strip().strip('"')
        staged = xy[0] not in (" ", "?")
        unstaged = xy[1] != " "
        untracked = xy == "??"
        added, deleted = stats.get(path, (0, 0))
        items.append(
            {
                "path": path,
                "xy": xy,
                "staged": staged and not untracked,
                "unstaged": unstaged or untracked,
                "untracked": untracked,
                "kind": _file_kind(xy),
                "added": added,
                "deleted": deleted,
            }
        )
    return items


def stage_paths(workspace: Path, paths: list[str]) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    tokens = {str(p or "").strip().replace("\\", "/") for p in paths if str(p or "").strip()}
    if tokens and tokens <= {".", "*"}:
        code, out, err = _run_git(workspace, ["add", "-A"])
        if code != 0:
            return f"ERROR: git add failed ({code}): {err.strip() or out.strip()}"
        return "ok"
    rels = _rel_paths(workspace, paths)
    if not rels:
        return "ERROR: no paths"
    code, out, err = _run_git(workspace, ["add", "--", *rels])
    if code != 0:
        return f"ERROR: git add failed ({code}): {err.strip() or out.strip()}"
    return "ok"


def unstage_paths(workspace: Path, paths: list[str]) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    rels = _rel_paths(workspace, paths)
    if not rels:
        return "ERROR: no paths"
    code, out, err = _run_git(workspace, ["restore", "--staged", "--", *rels])
    if code != 0:
        code, out, err = _run_git(workspace, ["reset", "HEAD", "--", *rels])
        if code != 0:
            return f"ERROR: git unstage failed ({code}): {err.strip() or out.strip()}"
    return "ok"


def commit_staged(workspace: Path, message: str) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    msg = (message or "").strip()
    if not msg:
        return "ERROR: empty commit message"
    if len(msg) > 2000:
        return "ERROR: commit message too long"
    code, out, err = _run_git(workspace, ["commit", "-m", msg], timeout=60)
    if code != 0:
        detail = (err or out).strip()
        return f"ERROR: git commit failed ({code}): {detail or 'nothing to commit?'}"
    return (out or err).strip() or "committed"


def list_remotes(workspace: Path) -> list[dict[str, str]]:
    code, out, _ = _run_git(workspace, ["remote", "-v"], timeout=10)
    if code != 0:
        return []
    seen: dict[str, str] = {}
    items: list[dict[str, str]] = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        if name in seen:
            continue
        seen[name] = url
        items.append({"name": name, "url": url})
    return items


def tracking_info(workspace: Path) -> dict[str, Any]:
    code, out, _ = _run_git(
        workspace,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        timeout=10,
    )
    upstream = out.strip() if code == 0 else ""
    ahead = behind = 0
    if upstream:
        code, out, _ = _run_git(
            workspace,
            ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            timeout=15,
        )
        if code == 0:
            parts = (out or "").strip().split()
            if len(parts) >= 2:
                try:
                    ahead, behind = int(parts[0]), int(parts[1])
                except ValueError:
                    ahead = behind = 0
    return {"upstream": upstream, "ahead": ahead, "behind": behind}


def list_branches(workspace: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    local_names: set[str] = set()
    code, out, _ = _run_git(
        workspace,
        ["for-each-ref", "--format=%(refname:short)\t%(HEAD)", "refs/heads/"],
        timeout=15,
    )
    if code == 0:
        for line in (out or "").splitlines():
            name, _, head = line.partition("\t")
            name = name.strip()
            if not name:
                continue
            local_names.add(name)
            items.append({"name": name, "current": head.strip() == "*", "remote": False})
    code, out, _ = _run_git(
        workspace,
        ["for-each-ref", "--format=%(refname:short)", "refs/remotes/"],
        timeout=15,
    )
    if code == 0:
        for name in (out or "").splitlines():
            name = name.strip()
            if not name or name.endswith("/HEAD"):
                continue
            short = name.split("/", 1)[-1] if "/" in name else name
            if short in local_names:
                continue
            items.append({"name": name, "current": False, "remote": True})
    if not items:
        current = current_branch(workspace)
        if current:
            items.append({"name": current, "current": True, "remote": False})
    return items


def checkout_branch(workspace: Path, name: str, *, create: bool = False) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    raw = (name or "").strip().replace("\\", "/")
    if raw.startswith("remotes/"):
        raw = raw[len("remotes/") :]
    if "/" in raw and raw.split("/", 1)[0] in {r["name"] for r in list_remotes(workspace)}:
        remote_ref = validate_branch_name(raw)
        local = validate_branch_name(remote_ref.split("/", 1)[-1])
        existing = {b["name"] for b in list_branches(workspace) if not b.get("remote")}
        args = ["switch", "--", local] if local in existing else ["switch", "-c", local, "--track", remote_ref]
    else:
        branch = validate_branch_name(raw)
        args = ["switch", "-c", branch] if create else ["switch", "--", branch]
    code, out, err = _run_git(workspace, args, timeout=30)
    if code != 0:
        if "--track" in args and "-c" in args:
            local = args[args.index("-c") + 1]
            track = args[args.index("--track") + 1]
            fallback = ["checkout", "-b", local, "--track", track]
        elif create or "-c" in args:
            name = args[args.index("-c") + 1] if "-c" in args else validate_branch_name(raw)
            fallback = ["checkout", "-b", name]
        else:
            fallback = ["checkout", args[-1]]
        code2, out2, err2 = _run_git(workspace, fallback, timeout=30)
        if code2 != 0:
            return _fail(code, out, err, "git switch")
        return (out2 or err2).strip() or f"switched to {raw}"
    return (out or err).strip() or f"switched to {raw}"


def fetch_remote(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    code, out, err = _run_git(workspace, ["fetch", "--all", "--prune"], timeout=180)
    if code != 0:
        return _fail(code, out, err, "git fetch")
    return (out or err).strip() or "fetched"


def pull_remote(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    code, out, err = _run_git(workspace, ["pull", "--no-edit"], timeout=180)
    if code != 0:
        return _fail(code, out, err, "git pull")
    return (out or err).strip() or "pulled"


def push_remote(workspace: Path) -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    code, _, _ = _run_git(
        workspace,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        timeout=10,
    )
    if code == 0:
        args = ["push"]
    else:
        remotes = list_remotes(workspace)
        if not remotes:
            return "ERROR: no remote configured"
        remote_name = remotes[0]["name"]
        args = ["push", "-u", remote_name, "HEAD"]
    code, out, err = _run_git(workspace, args, timeout=180)
    if code != 0:
        return _fail(code, out, err, "git push")
    return (out or err).strip() or "pushed"


def set_remote_url(workspace: Path, url: str, *, name: str = "origin") -> str:
    if not is_git_repo(workspace):
        return "ERROR: not a git repository"
    remote = (name or "origin").strip() or "origin"
    if not _REMOTE_NAME_RE.fullmatch(remote):
        return "ERROR: invalid remote name"
    target = validate_remote_url(url)
    code, _, _ = _run_git(workspace, ["remote", "get-url", remote], timeout=10)
    args = ["remote", "set-url", remote, target] if code == 0 else ["remote", "add", remote, target]
    code, out, err = _run_git(workspace, args, timeout=20)
    if code != 0:
        return _fail(code, out, err, "git remote")
    return (out or err).strip() or f"{remote} -> {target}"


def review_panel_snapshot(
    workspace: Path, session_id: Optional[str] = None
) -> dict[str, Any]:
    """Composer review: files changed in this conversation, not git porcelain."""
    from . import fs_undo

    empty_totals = {"files": 0, "added": 0, "deleted": 0}
    review = fs_undo.review_snapshot(workspace, session_id=session_id)
    repo = is_git_repo(workspace)
    return {
        "is_repo": repo,
        "branch": current_branch(workspace) if repo else "",
        "files": review.get("files") or [],
        "status": "",
        "branches": [],
        "ahead": 0,
        "behind": 0,
        "upstream": "",
        "remote": "",
        "remote_url": "",
        "remotes": [],
        "totals": review.get("totals") or empty_totals,
    }


def panel_snapshot(workspace: Path) -> dict[str, Any]:
    empty_totals = {"files": 0, "added": 0, "deleted": 0}
    repo = is_git_repo(workspace)
    if not repo:
        from . import fs_undo

        review = fs_undo.review_snapshot(workspace)
        return {
            "is_repo": False,
            "branch": "",
            "files": review.get("files") or [],
            "status": "",
            "branches": [],
            "ahead": 0,
            "behind": 0,
            "upstream": "",
            "remote": "",
            "remote_url": "",
            "remotes": [],
            "totals": review.get("totals") or empty_totals,
        }
    try:
        files = porcelain_entries(workspace)
    except RuntimeError as exc:
        return {
            "is_repo": True,
            "branch": current_branch(workspace),
            "files": [],
            "error": str(exc),
            "branches": [],
            "totals": empty_totals,
        }
    added_total = sum(int(f.get("added") or 0) for f in files)
    deleted_total = sum(int(f.get("deleted") or 0) for f in files)
    remotes = list_remotes(workspace)
    origin = next((r for r in remotes if r["name"] == "origin"), remotes[0] if remotes else None)
    tracking = tracking_info(workspace)
    return {
        "is_repo": True,
        "branch": current_branch(workspace),
        "files": files,
        "status": git_status(workspace),
        "branches": list_branches(workspace),
        "ahead": tracking["ahead"],
        "behind": tracking["behind"],
        "upstream": tracking["upstream"],
        "remote": origin["name"] if origin else "",
        "remote_url": origin["url"] if origin else "",
        "remotes": remotes,
        "totals": {"files": len(files), "added": added_total, "deleted": deleted_total},
    }


def format_git_snapshot(workspace: Path) -> dict[str, Any]:
    repo = is_git_repo(workspace)
    return {
        "is_repo": repo,
        "status": git_status(workspace) if repo else "",
        "branch": git_branch(workspace) if repo else "",
    }


def _read_workspace_text(path: Path, limit: int = 2_000_000) -> tuple[str, bool]:
    try:
        data = path.read_bytes()
    except OSError:
        return "", False
    if not data:
        return "", False
    if b"\0" in data[:8192]:
        return "", True
    if len(data) > limit:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), False


def file_change_pair(
    workspace: Path, raw_path: str, session_id: Optional[str] = None
) -> dict[str, Any]:
    """Before/after texts for the composer review panel (conversation undo, then git)."""
    rels = _rel_paths(workspace, [raw_path])
    if not rels:
        raise ValueError("invalid path")
    rel = rels[0]
    from . import fs_undo

    try:
        return fs_undo.file_review_pair(workspace, rel, session_id=session_id)
    except ValueError:
        pass
    if not is_git_repo(workspace):
        raise ValueError(f"not a changed file: {rel}")

    abs_path = workspace.resolve() / rel
    code, old, _ = _run_git(workspace, ["show", f"HEAD:{rel}"], timeout=20)
    in_head = code == 0
    old_text = old if in_head else ""
    if in_head and "\0" in old_text[:8192]:
        return {
            "path": rel,
            "old": "",
            "new": "",
            "kind": "modified",
            "is_new": False,
            "binary": True,
        }
    exists = abs_path.is_file()
    if exists:
        new_text, binary = _read_workspace_text(abs_path)
        if binary:
            return {
                "path": rel,
                "old": "",
                "new": "",
                "kind": "modified",
                "is_new": not in_head,
                "binary": True,
            }
    else:
        new_text = ""
    if not in_head and exists:
        kind = "added"
        is_new = True
    elif in_head and not exists:
        kind = "deleted"
        is_new = False
    else:
        kind = "modified"
        is_new = False
    return {
        "path": rel,
        "old": old_text,
        "new": new_text,
        "kind": kind,
        "is_new": is_new,
        "is_deleted": kind == "deleted",
        "binary": False,
    }
