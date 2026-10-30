"""Workspace text search that skips junk dirs, gitignore, and binaries."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from .codebase_memory import SKIP_DIRS

_MAX_FILE_BYTES = 2_000_000
_SAMPLE = 8192
_DEFAULT_HITS = 50

_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".wasm",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".pyc",
    ".pyo",
}


@dataclass(frozen=True)
class _Rule:
    negated: bool
    dir_only: bool
    anchored: bool
    pattern: str


def search_text(
    workspace: Path,
    query: str,
    *,
    path: str | Path = ".",
    glob: str = "*",
    regex: bool = False,
    max_hits: int = _DEFAULT_HITS,
    engine: str = "auto",
) -> str:
    """Return `path:line:text` hits, or `no matches` / an ERROR string."""
    q = query if regex else str(query)
    if q == "" or q is None:
        return "ERROR: empty query"
    ws = Path(workspace).resolve()
    root = Path(path)
    if not root.is_absolute():
        root = (ws / root).resolve()
    else:
        root = root.resolve()
    if not root.exists():
        return f"ERROR: not found: {root}"

    cap = max(1, min(int(max_hits or _DEFAULT_HITS), 200))
    pattern: Optional[re.Pattern[str]] = None
    if regex:
        try:
            pattern = re.compile(q)
        except re.error as exc:
            return f"ERROR: invalid regex: {exc}"

    glob_n = _normalize_glob(glob)
    hits = None
    if engine != "walk":
        hits = _try_external(ws, root, q, glob_n, regex=regex, cap=cap)
    # An external miss (ripgrep exit 1, or a Windows path the line parser
    # dropped) must not hide a file the walk would find.
    if not hits:
        hits = list(
            _walk_hits(ws, root, q, glob_n, pattern=pattern, regex=regex, cap=cap)
        )
    if not hits:
        return "no matches"
    if len(hits) > cap:
        hits = hits[:cap]
        hits.append(f"… truncated ({cap} hits)")
    elif len(hits) == cap:
        hits.append(f"… truncated ({cap} hits)")
    return "\n".join(hits)


def _normalize_glob(glob: str) -> str:
    g = (glob or "").strip() or "*"
    # Legacy default treated "has a dot" as all files; keep that as all files.
    if g in ("*", "*.*"):
        return "*"
    return g


def _glob_ok(rel: str, glob: str) -> bool:
    if glob == "*":
        return True
    name = rel.rsplit("/", 1)[-1]
    if "/" not in glob and "**" not in glob:
        return fnmatch.fnmatch(name, glob)
    pat = glob if glob.startswith("**/") or "/" in glob else f"**/{glob}"
    try:
        return PurePosixPath(rel).match(pat)
    except (ValueError, OSError):
        return fnmatch.fnmatch(name, glob)


def _try_external(
    ws: Path,
    root: Path,
    query: str,
    glob: str,
    *,
    regex: bool,
    cap: int,
) -> Optional[list[str]]:
    hits = _try_rg(ws, root, query, glob, regex=regex, cap=cap)
    if hits is not None:
        return hits
    return _try_git_grep(ws, root, query, glob, regex=regex, cap=cap)


def _try_rg(
    ws: Path,
    root: Path,
    query: str,
    glob: str,
    *,
    regex: bool,
    cap: int,
) -> Optional[list[str]]:
    exe = shutil.which("rg")
    if not exe:
        return None
    args = [
        exe,
        "-n",
        "--no-heading",
        "--color",
        "never",
        "-I",
        "--hidden",
        "--max-filesize",
        "2M",
    ]
    for skipped in sorted(SKIP_DIRS):
        args.extend(["--glob", f"!{skipped}/**"])
    if not regex:
        args.append("-F")
    if glob != "*":
        args.extend(["-g", glob])
    args.extend(["--", query, str(root)])
    try:
        proc = subprocess.run(
            args,
            cwd=str(ws),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):
        return None
    return _format_external_lines(ws, proc.stdout, cap)


def _try_git_grep(
    ws: Path,
    root: Path,
    query: str,
    glob: str,
    *,
    regex: bool,
    cap: int,
) -> Optional[list[str]]:
    from .git_ops import _run_git, is_git_repo

    try:
        if not is_git_repo(ws):
            return None
    except Exception:
        return None
    args = ["grep", "-n", "-I", "--untracked", "--exclude-standard"]
    args.append("-E" if regex else "-F")
    args.extend(["-e", query, "--"])
    try:
        rel_root = root.relative_to(ws).as_posix()
    except ValueError:
        return None
    if glob != "*":
        if rel_root in (".", ""):
            spec = f":(glob)**/{glob}" if "/" not in glob else f":(glob){glob}"
        else:
            spec = f":(glob){rel_root}/**/{glob}" if "/" not in glob else f":(glob){rel_root}/{glob}"
        args.append(spec)
    elif rel_root not in (".", ""):
        args.append(rel_root)
    code, out, _err = _run_git(ws, args, timeout=25)
    if code not in (0, 1):
        return None
    return _format_external_lines(ws, out, cap)


_GREP_LINE = re.compile(r"^((?:[A-Za-z]:)?[^:]+):(\d+):(.*)$")


def _format_external_lines(ws: Path, stdout: str, cap: int) -> list[str]:
    hits: list[str] = []
    for raw in stdout.splitlines():
        if not raw.strip():
            continue
        m = _GREP_LINE.match(raw)
        if not m:
            continue
        rel_src, line_s, text = m.group(1), m.group(2), m.group(3)
        try:
            fp = Path(rel_src)
            if not fp.is_absolute():
                fp = (ws / rel_src).resolve()
            rel = fp.relative_to(ws).as_posix()
        except (OSError, ValueError):
            rel = rel_src.replace("\\", "/")
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        hits.append(f"{rel}:{line_s}:{text.strip()[:200]}")
        if len(hits) >= cap:
            break
    return hits


def _walk_hits(
    ws: Path,
    root: Path,
    query: str,
    glob: str,
    *,
    pattern: Optional[re.Pattern[str]],
    regex: bool,
    cap: int,
) -> Iterable[str]:
    files = [root] if root.is_file() else _iter_files(ws, root)
    n = 0
    for fp in files:
        if n >= cap:
            return
        try:
            rel = fp.resolve().relative_to(ws).as_posix()
        except ValueError:
            rel = str(fp)
        if not _glob_ok(rel, glob) and not _glob_ok(fp.name, glob):
            continue
        if fp.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            if fp.stat().st_size > _MAX_FILE_BYTES:
                continue
            if _is_binary(fp):
                continue
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex:
                if pattern is None or not pattern.search(line):
                    continue
            elif query not in line:
                continue
            yield f"{rel}:{i}:{line.strip()[:200]}"
            n += 1
            if n >= cap:
                return


def _iter_files(ws: Path, start: Path) -> Iterable[Path]:
    matcher = _IgnoreStack(ws)
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        dp = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            if name in SKIP_DIRS:
                continue
            child = dp / name
            if matcher.ignored(child, is_dir=True):
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            child = dp / name
            if matcher.ignored(child, is_dir=False):
                continue
            yield child


def _is_binary(fp: Path) -> bool:
    try:
        with fp.open("rb") as fh:
            sample = fh.read(_SAMPLE)
    except OSError:
        return True
    return b"\x00" in sample


class _IgnoreStack:
    def __init__(self, workspace: Path) -> None:
        self.ws = workspace.resolve()
        self._parsed: dict[Path, list[_Rule]] = {}

    def ignored(self, path: Path, *, is_dir: bool) -> bool:
        try:
            rel = path.resolve().relative_to(self.ws).as_posix()
        except ValueError:
            return False
        if any(part in SKIP_DIRS for part in rel.split("/")):
            return True
        ignored = False
        for base, rules in self._layers_for(path.parent):
            if base and not (rel == base or rel.startswith(base + "/")):
                continue
            local = rel[len(base) :].lstrip("/") if base else rel
            for rule in rules:
                if _match_rule(local, is_dir, rule):
                    ignored = not rule.negated
        return ignored

    def _layers_for(self, directory: Path) -> list[tuple[str, list[_Rule]]]:
        layers: list[tuple[str, list[_Rule]]] = []
        try:
            rel = directory.resolve().relative_to(self.ws)
        except ValueError:
            return [("", self._rules(self.ws / ".gitignore"))]
        cur = self.ws
        layers.append(("", self._rules(cur / ".gitignore")))
        acc: list[str] = []
        for part in rel.parts:
            cur = cur / part
            acc.append(part)
            gi = cur / ".gitignore"
            if gi.is_file():
                layers.append(("/".join(acc), self._rules(gi)))
        return layers

    def _rules(self, gi: Path) -> list[_Rule]:
        cached = self._parsed.get(gi)
        if cached is not None:
            return cached
        rules = _parse_gitignore(gi)
        self._parsed[gi] = rules
        return rules


def _parse_gitignore(path: Path) -> list[_Rule]:
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    rules: list[_Rule] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        negated = s.startswith("!")
        if negated:
            s = s[1:]
        dir_only = s.endswith("/")
        s = s.rstrip("/")
        anchored = s.startswith("/")
        if anchored:
            s = s[1:]
        if not s:
            continue
        rules.append(_Rule(negated=negated, dir_only=dir_only, anchored=anchored, pattern=s))
    return rules


def _match_rule(rel: str, is_dir: bool, rule: _Rule) -> bool:
    if rule.dir_only and not is_dir:
        return False
    pat = rule.pattern
    if not rule.anchored and "/" not in pat:
        parts = rel.split("/")
        return any(fnmatch.fnmatch(p, pat) for p in parts)
    if "**" in pat:
        try:
            return PurePosixPath(rel).match(pat) or PurePosixPath(rel).match(f"**/{pat}")
        except (ValueError, OSError):
            return fnmatch.fnmatch(rel, pat)
    if rule.anchored:
        return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel.split("/", 1)[0], pat)
    return fnmatch.fnmatch(rel, pat)
