"""Host OS / time / network snapshot for prompts and tools."""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


_NET_TTL_SEC = 45.0
_NET_TIMEOUT_SEC = 1.2
# China-reachable first, then global. Port 53/443 only — no HTTP payload.
_NET_PROBES: tuple[tuple[str, int], ...] = (
    ("223.5.5.5", 53),
    ("1.1.1.1", 443),
    ("114.114.114.114", 53),
)

_lock = threading.Lock()
_online_cache: Optional[tuple[float, bool]] = None
_info_cache: Optional["HostInfo"] = None


@dataclass(frozen=True)
class HostInfo:
    os_family: str  # windows | linux | darwin | other
    os_name: str  # Windows / Linux / 麒麟 / macOS
    distro: str
    is_kylin: bool
    kernel: str
    shell: str
    timezone: str
    local_time: str
    online: bool

    @property
    def label(self) -> str:
        if self.is_kylin:
            pretty = self.distro or "麒麟 Linux"
            return f"{pretty} (Kylin / Linux)"
        if self.os_family == "windows":
            return f"{self.distro or self.os_name} (Windows)"
        if self.os_family == "darwin":
            return f"{self.distro or 'macOS'} (Darwin)"
        if self.os_family == "linux":
            return f"{self.distro or 'Linux'} (Linux)"
        return self.distro or self.os_name or platform.system() or "unknown"


def _bool_env(*names: str) -> Optional[bool]:
    for name in names:
        raw = (os.getenv(name) or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    return None


def _read_os_release(path: Path | None = None) -> dict[str, str]:
    target = path or Path("/etc/os-release")
    out: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def parse_os_release(text: str) -> dict[str, str]:
    """Test helper: parse os-release body."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _is_kylin_fields(fields: dict[str, str]) -> bool:
    blob = " ".join(
        str(fields.get(k) or "")
        for k in ("ID", "ID_LIKE", "NAME", "PRETTY_NAME", "VERSION", "VERSION_ID")
    ).lower()
    needles = ("kylin", "麒麟", "neokylin", "galaxykylin", "kylinos", "uos")
    if any(n in blob for n in needles):
        # UOS is related but not always 麒麟; only treat as Kylin when kylin/麒麟 present
        if "uos" in blob and "kylin" not in blob and "麒麟" not in blob:
            return False
        return True
    return False


def _unix_shell_bin() -> str:
    for cand in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return "/bin/sh"


def unix_shell_argv(command: str) -> list[str]:
    """POSIX shell argv. Prefers bash; falls back to sh (麒麟 / minimal Linux)."""
    sh = _unix_shell_bin()
    flag = "-lc" if "bash" in os.path.basename(sh) else "-c"
    return [sh, flag, command]


_win_shell_lock = threading.Lock()
_ps_exe_cache: Optional[str] = None
_bash_exe_cache: Optional[str] = None  # "" = probed, none found
_cmd_exe_cache: Optional[str] = None

_BASH_FIRST_TOKEN_RE = re.compile(
    r"^(?:ba)?sh(?:\.exe)?$|^\S+\.sh$",
    re.IGNORECASE,
)
_BASH_BODY_RE = re.compile(
    r"^(?:set -e(?:uo pipefail)?\b|set -o\b|export \w+=|source\s+|\[\[ )",
)


def looks_like_bash_command(command: str) -> bool:
    """True when the payload should run under bash, not PowerShell."""
    text = (command or "").strip()
    if not text:
        return False
    head = text.split("\n", 1)[0].strip()
    if head.startswith("#!") and re.search(r"(?:ba)?sh\b", head, re.IGNORECASE):
        return True
    first = re.split(r"\s+", text, maxsplit=1)[0].strip().strip("\"'")
    base = os.path.basename(first.replace("\\", "/"))
    if _BASH_FIRST_TOKEN_RE.match(base):
        return True
    if _BASH_BODY_RE.match(text):
        return True
    return False


def _windows_root() -> str:
    return os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"


def windows_system_path_dirs() -> list[str]:
    """Dirs that CreateProcess needs even when a packaged app inherited a thin PATH."""
    root = _windows_root()
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        os.path.join(root, "System32"),
        os.path.join(root, "SysWOW64"),
        os.path.join(root, "System32", "Wbem"),
        os.path.join(root, "System32", "WindowsPowerShell", "v1.0"),
        os.path.join(root, "System32", "OpenSSH"),
        os.path.join(root, "Sysnative", "WindowsPowerShell", "v1.0"),
        os.path.join(pf, "PowerShell", "7"),
        os.path.join(pf, "Git", "cmd"),
        os.path.join(pf, "Git", "bin"),
        os.path.join(pf, "Git", "usr", "bin"),
        os.path.join(pf86, "Git", "cmd"),
        os.path.join(pf86, "Git", "bin"),
        os.path.join(local, "Programs", "Git", "cmd") if local else "",
        os.path.join(local, "Programs", "Git", "bin") if local else "",
        os.path.join(local, "Programs", "Git", "usr", "bin") if local else "",
        os.path.join(os.environ.get("ProgramW6432") or pf, "Git", "cmd"),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        key = os.path.normcase(os.path.normpath(raw))
        if key in seen or not os.path.isdir(raw):
            continue
        seen.add(key)
        out.append(raw)
    return out


def augment_executable_path(env: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Ensure System32 / PowerShell / Git Bash are on PATH for child processes."""
    src = dict(env if env is not None else os.environ)
    if os.name != "nt":
        return src
    path_key = "PATH" if "PATH" in src else next((k for k in src if k.upper() == "PATH"), "PATH")
    parts = [p for p in str(src.get(path_key) or "").split(os.pathsep) if p]
    seen = {os.path.normcase(os.path.normpath(p)) for p in parts}
    for extra in windows_system_path_dirs():
        key = os.path.normcase(os.path.normpath(extra))
        if key in seen:
            continue
        parts.append(extra)
        seen.add(key)
    src[path_key] = os.pathsep.join(parts)
    src.setdefault("SystemRoot", _windows_root())
    src.setdefault("WINDIR", src["SystemRoot"])
    src.setdefault("PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.MSC")
    return src


def windows_powershell_exe() -> str:
    """Absolute powershell.exe / pwsh.exe — packaged apps often lack it on PATH."""
    global _ps_exe_cache
    with _win_shell_lock:
        if _ps_exe_cache:
            return _ps_exe_cache
    root = _windows_root()
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        os.path.join(root, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe"),
        os.path.join(root, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe"),
        os.path.join(root, "System32", "powershell.exe"),
        os.path.join(pf, "PowerShell", "7", "pwsh.exe"),
        os.path.join(local, "Microsoft", "WindowsApps", "pwsh.exe") if local else "",
    ]
    found = ""
    for cand in candidates:
        if cand and os.path.isfile(cand):
            found = cand
            break
    if not found:
        found = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe") or ""
    if not found:
        found = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    with _win_shell_lock:
        _ps_exe_cache = found
    return found


def windows_bash_exe() -> Optional[str]:
    """Git Bash / MSYS bash when present — needed to actually run .sh scripts on Windows."""
    global _bash_exe_cache
    with _win_shell_lock:
        if _bash_exe_cache is not None:
            return _bash_exe_cache or None
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        os.path.join(pf, "Git", "bin", "bash.exe"),
        os.path.join(pf, "Git", "usr", "bin", "bash.exe"),
        os.path.join(pf86, "Git", "bin", "bash.exe"),
        os.path.join(local, "Programs", "Git", "bin", "bash.exe") if local else "",
        os.path.join(local, "Programs", "Git", "usr", "bin", "bash.exe") if local else "",
        r"C:\msys64\usr\bin\bash.exe",
        r"C:\msys32\usr\bin\bash.exe",
    ]
    found = ""
    for cand in candidates:
        if cand and os.path.isfile(cand):
            found = cand
            break
    if not found:
        which = shutil.which("bash") or shutil.which("bash.exe") or ""
        # Ignore the WSL stub at System32\bash.exe unless Git/MSYS is missing AND WSL exists;
        # the stub cannot run workspace .sh files with Windows paths reliably.
        if which:
            norm = os.path.normcase(which)
            if "system32" in norm and os.path.basename(norm) == "bash.exe":
                which = ""
            found = which
    with _win_shell_lock:
        _bash_exe_cache = found
    return found or None


def windows_cmd_exe() -> str:
    global _cmd_exe_cache
    with _win_shell_lock:
        if _cmd_exe_cache:
            return _cmd_exe_cache
    root = _windows_root()
    candidates = [
        os.path.join(root, "System32", "cmd.exe"),
        os.path.join(root, "Sysnative", "cmd.exe"),
        os.path.join(root, "SysWOW64", "cmd.exe"),
    ]
    found = next((c for c in candidates if os.path.isfile(c)), "") or shutil.which("cmd.exe") or "cmd.exe"
    with _win_shell_lock:
        _cmd_exe_cache = found
    return found


def windows_shell_argv(command: str) -> list[str]:
    """Windows argv: Git Bash for .sh, else PowerShell by full path, else cmd.exe."""
    body = command or ""
    if looks_like_bash_command(body):
        bash = windows_bash_exe()
        if bash:
            return [bash, "-lc", body]
    ps = windows_powershell_exe()
    if os.path.isfile(ps):
        wrapped = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            f"{body}"
        )
        return [
            ps,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            wrapped,
        ]
    return [windows_cmd_exe(), "/d", "/s", "/c", body]


def shell_argv(command: str) -> list[str]:
    """Host-appropriate argv for run_shell / verify_run / background jobs."""
    if os.name == "nt":
        return windows_shell_argv(command)
    return unix_shell_argv(command)


def reset_win_shell_cache() -> None:
    global _ps_exe_cache, _bash_exe_cache, _cmd_exe_cache
    with _win_shell_lock:
        _ps_exe_cache = None
        _bash_exe_cache = None
        _cmd_exe_cache = None


def _probe_online() -> bool:
    forced = _bool_env("META_OFFLINE", "SIDEKICK_OFFLINE")
    if forced is True:
        return False
    if _bool_env("META_ONLINE", "SIDEKICK_ONLINE") is True:
        return True
    for host, port in _NET_PROBES:
        try:
            with socket.create_connection((host, port), timeout=_NET_TIMEOUT_SEC):
                return True
        except OSError:
            continue
    return False


def network_available(*, force_refresh: bool = False) -> bool:
    """Cached reachability. META_OFFLINE=1 forces False."""
    global _online_cache
    now = time.monotonic()
    with _lock:
        if (
            not force_refresh
            and _online_cache is not None
            and (now - _online_cache[0]) < _NET_TTL_SEC
        ):
            return _online_cache[1]
    online = _probe_online()
    with _lock:
        _online_cache = (time.monotonic(), online)
    return online


def _os_snapshot() -> tuple[str, str, str, bool, str, str]:
    system = (platform.system() or "").strip()
    kernel = platform.release() or ""
    if os.name == "nt" or system.lower().startswith("win"):
        ver = platform.version() or ""
        distro = f"Windows {platform.release()}".strip()
        if ver:
            distro = f"{distro}".strip()
        return "windows", "Windows", distro, False, kernel, "PowerShell"
    if system.lower() == "darwin":
        return "darwin", "macOS", platform.mac_ver()[0] or "macOS", False, kernel, "bash"
    fields = _read_os_release()
    pretty = fields.get("PRETTY_NAME") or fields.get("NAME") or "Linux"
    kylin = _is_kylin_fields(fields) or Path("/etc/kylin-release").is_file()
    os_name = "麒麟" if kylin else "Linux"
    distro = pretty
    if kylin and "麒麟" not in pretty and "kylin" not in pretty.lower():
        distro = f"麒麟 / {pretty}"
    sh = _unix_shell_bin()
    shell = "bash" if "bash" in os.path.basename(sh) else "sh"
    return "linux", os_name, distro, kylin, kernel, shell


def get_host_info(*, force_refresh: bool = False) -> HostInfo:
    global _info_cache
    online = network_available(force_refresh=force_refresh)
    if _info_cache is not None and not force_refresh:
        if _info_cache.online == online:
            now = datetime.now().astimezone()
            return HostInfo(
                os_family=_info_cache.os_family,
                os_name=_info_cache.os_name,
                distro=_info_cache.distro,
                is_kylin=_info_cache.is_kylin,
                kernel=_info_cache.kernel,
                shell=_info_cache.shell,
                timezone=now.tzname() or now.strftime("%z") or "local",
                local_time=now.strftime("%Y-%m-%d %H:%M:%S %z"),
                online=online,
            )
    family, os_name, distro, is_kylin, kernel, shell = _os_snapshot()
    now = datetime.now().astimezone()
    info = HostInfo(
        os_family=family,
        os_name=os_name,
        distro=distro,
        is_kylin=is_kylin,
        kernel=kernel,
        shell=shell,
        timezone=now.tzname() or now.strftime("%z") or "local",
        local_time=now.strftime("%Y-%m-%d %H:%M:%S %z"),
        online=online,
    )
    _info_cache = info
    return info


def reset_hostinfo_cache() -> None:
    global _online_cache, _info_cache
    with _lock:
        _online_cache = None
        _info_cache = None


def host_prompt_block() -> str:
    """System-prompt section: OS, time, network, shell dialect."""
    info = get_host_info()
    # Status only. The tool-usage rules for OFFLINE live once in CORE, next to
    # web_search / browser_navigate; repeating them here tripled the prompt.
    net = (
        "ONLINE — public internet looks reachable."
        if info.online
        else "OFFLINE — no public internet."
    )
    lines = [
        "## Host environment (CRITICAL)",
        f"OS: {info.label}. Kernel: {info.kernel or '?'}.",
        f"Local time: {info.local_time} ({info.timezone}).",
        f"Network: {net}",
    ]
    if info.os_family == "windows":
        bash = windows_bash_exe()
        bash_line = (
            f"- Git Bash is available at `{bash}`. To run a .sh file, call "
            "`bash path/to/script.sh` (or `./script.sh`). Do NOT wrap with powershell.exe."
            if bash
            else (
                "- Git Bash is NOT installed. Do not emit bash/.sh scripts — write PowerShell. "
                "If the user insists on bash, tell them to install Git for Windows."
            )
        )
        lines.extend(
            [
                "Shell executor: Windows PowerShell (resolved by full path, not PATH).",
                bash_line,
            ]
        )
    else:
        sh = _unix_shell_bin()
        family = "麒麟 / Linux" if info.is_kylin else info.os_name
        lines.append(f"Shell executor: `{sh}` ({info.shell}). Host family: {family}.")
        if info.is_kylin:
            lines.append(
                "- 麒麟: treat as Linux. bash if present, otherwise sh. "
                "Use the distro's own package manager; do not assume apt/yum blindly."
            )
    lines.append(
        "- If run_shell/verify_run returns shell-disabled, tell the user to set "
        "META_ALLOW_SHELL=1 and restart — do NOT invent OS-specific unavailability."
    )
    return "\n".join(lines)
