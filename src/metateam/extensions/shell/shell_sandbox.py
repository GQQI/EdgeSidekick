"""Shell sandbox: real disk, cwd stays in the workspace (not a copy FS).

Commands still run with cwd=workspace on the host. Paths outside the
workspace are not hard-blocked — they go through the approval dialog.
Only empty commands and a cwd that already left the workspace are rejected
here. Destructive denylist lives in the shell tool, not this scanner.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ...core.pathutil import is_relative_to, resolve_path as _norm

# Absolute / drive / UNC / home-relative path-like tokens in a shell command.
# Drive letters use a lookbehind so `https://…` does not match as `s:/…`.
_PATH_TOKEN_RE = re.compile(
    r"(?:"
    r'(?P<q>["\'])(?P<qp>(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\|~/|/)[^"\']*)(?P=q)'
    r"|"
    r"(?P<u>(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\|~/|/)[^\s\"';|&<>]+)"
    r")"
)

# http(s)/ftp/… URLs are not filesystem paths — mask before path scanning.
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'|&<>]+", re.IGNORECASE)

# Relative segments that climb out of cwd when resolved.
_DOTDOT_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")

# System browsers may be launched by absolute path to open a URL/file.
# Only well-known install locations + exe names (not arbitrary Program Files).
_WIN_BROWSER_EXES = frozenset(
    {
        "msedge.exe",
        "chrome.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
    }
)
# Note: raw strings cannot end with a single backslash (r"...\") — omit trailing \.
_WIN_BROWSER_PATH_NEEDLES = (
    r"\program files\microsoft\edge",
    r"\program files (x86)\microsoft\edge",
    r"\program files\google\chrome",
    r"\program files (x86)\google\chrome",
    r"\program files\mozilla firefox",
    r"\program files (x86)\mozilla firefox",
    r"\local\google\chrome",
    r"\local\microsoft\edge",
    r"\local\bravesoftware\brave-browser",
    r"\local\programs\opera",
)
_POSIX_BROWSER_NAMES = frozenset(
    {
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "firefox",
        "microsoft-edge",
        "microsoft-edge-stable",
        "brave-browser",
        "opera",
        "xdg-open",
        "open",  # macOS
    }
)


def _is_allowed_browser_exe(path: Path) -> bool:
    """True if path is a typical system browser binary (launch-only exception)."""
    name = path.name.lower()
    if os.name == "nt":
        if name not in _WIN_BROWSER_EXES:
            return False
        s = str(_norm(path)).lower().replace("/", "\\")
        return any(n in s for n in _WIN_BROWSER_PATH_NEEDLES)
    return name in _POSIX_BROWSER_NAMES


@dataclass(frozen=True)
class ShellSandboxPolicy:
    """Writable/readable roots for sandboxed shell (host paths)."""

    roots: tuple[Path, ...]
    enabled: bool = True

    @classmethod
    def for_workspace(
        cls,
        workspace: Path,
        *,
        extra: Optional[Iterable[Path]] = None,
        enabled: bool = True,
    ) -> "ShellSandboxPolicy":
        roots: list[Path] = []
        try:
            roots.append(workspace.expanduser().resolve())
        except OSError:
            roots.append(workspace.expanduser())
        try:
            roots.append(Path(tempfile.gettempdir()).resolve())
        except OSError:
            roots.append(Path(tempfile.gettempdir()))
        for p in extra or ():
            try:
                roots.append(Path(p).expanduser().resolve())
            except OSError:
                continue
        # Dedupe
        seen: set[str] = set()
        uniq: list[Path] = []
        for r in roots:
            key = str(r).lower() if os.name == "nt" else str(r)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        return cls(roots=tuple(uniq), enabled=enabled)


def path_allowed(path: Path, policy: ShellSandboxPolicy) -> bool:
    target = _norm(path)
    return any(is_relative_to(target, root) for root in policy.roots)


def _mask_urls(command: str) -> str:
    """Replace URL spans so path heuristics do not treat them as file paths."""
    return _URL_RE.sub(" ", command or "")


def _is_url_like(token: str) -> bool:
    t = (token or "").strip().strip("'\"")
    if not t:
        return False
    if _URL_RE.match(t):
        return True
    # Mis-parsed remnant of https://host → s://host
    if re.match(r"^[a-z]://", t, re.IGNORECASE):
        return True
    return False


def _extract_path_candidates(command: str) -> list[str]:
    found: list[str] = []
    for m in _PATH_TOKEN_RE.finditer(_mask_urls(command)):
        raw = m.group("qp") or m.group("u") or ""
        raw = raw.strip()
        if raw and not _is_url_like(raw):
            found.append(raw)
    return found


def _token_leaves_sandbox(
    token: str,
    *,
    cwd: Path,
    policy: ShellSandboxPolicy,
) -> bool:
    """True if this path token resolves outside the workspace/temp roots."""
    expanded = token
    if expanded.startswith("~/") or expanded.startswith("~\\"):
        expanded = str(Path.home() / expanded[2:])
    p = Path(expanded)
    cwd_r = _norm(cwd)
    if not p.is_absolute():
        if not _DOTDOT_RE.search(token.replace("/", os.sep)):
            return False
        try:
            resolved = (cwd_r / p).resolve()
        except OSError:
            return True
        return not path_allowed(resolved, policy) and not _is_allowed_browser_exe(resolved)
    return not path_allowed(p, policy) and not _is_allowed_browser_exe(p)


def outside_shell_paths(
    command: str,
    *,
    cwd: Path,
    policy: ShellSandboxPolicy,
) -> list[str]:
    """Path tokens (or `cd ..`) that leave the workspace. Empty if none."""
    if not policy.enabled:
        return []
    cmd = (command or "").strip()
    if not cmd:
        return []
    found: list[str] = []
    if re.search(r"(?:^|[;&|]\s*)cd\s+\.\.(?:\s|$|[;&|])", cmd, re.IGNORECASE):
        found.append("cd ..")
    for token in _extract_path_candidates(cmd):
        if _token_leaves_sandbox(token, cwd=cwd, policy=policy):
            found.append(token)
    return found


def check_command(
    command: str,
    *,
    cwd: Path,
    policy: ShellSandboxPolicy,
) -> Optional[str]:
    """Hard-block only empty commands and a cwd that already left the workspace.

    Outside-workspace *paths inside the command* are not rejected here — the
    approval gate asks the user instead.
    """
    if not policy.enabled:
        return None
    cmd = (command or "").strip()
    if not cmd:
        return "ERROR: empty command"

    cwd_r = _norm(cwd)
    if not path_allowed(cwd_r, policy):
        return f"ERROR: shell cwd outside sandbox: {cwd_r}"

    return None


def sandbox_env(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Env for sandboxed subprocess — keep PATH/HOME, drop obvious secrets noise optional."""
    from ...core.hostinfo import augment_executable_path

    src = augment_executable_path(dict(base or os.environ))
    # Always force UTF-8 for Python / console child output (avoid Windows GBK crashes)
    src["PYTHONIOENCODING"] = "utf-8"
    src["PYTHONUTF8"] = "1"
    src["PYTHONUNBUFFERED"] = "1"
    src.setdefault("PIP_PROGRESS_BAR", "off")
    src.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    if not src.get("PLAYWRIGHT_DOWNLOAD_HOST"):
        src["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright"
    if not src.get("PIP_INDEX_URL"):
        src["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
    if not src.get("PIP_TRUSTED_HOST"):
        src["PIP_TRUSTED_HOST"] = "pypi.tuna.tsinghua.edu.cn"
    if os.name == "nt":
        # Hint many CLIs / PowerShell toward UTF-8 instead of OEM/GBK
        src.setdefault("LANG", "en_US.UTF-8")
    src["SIDEKICK_SHELL_SANDBOX"] = "1"
    return src


def describe_policy(policy: ShellSandboxPolicy) -> str:
    if not policy.enabled:
        return "shell sandbox: off"
    roots = ", ".join(str(r) for r in policy.roots)
    return f"shell sandbox: on · workspace=[{roots}]（区外路径需确认）"
