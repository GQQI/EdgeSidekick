"""Heuristics for agent shell commands: long-running, interactive, denylist."""

from __future__ import annotations

import re

_DAEMON_RE = re.compile(
    r"("
    r"npm\s+run\s+(dev|start|serve)|"
    r"yarn\s+(dev|start)|"
    r"pnpm\s+(dev|start)|"
    r"(?:^|[\n;&|])\s*(?:npx\s+)?vite\b|"
    r"webpack-dev-server|"
    r"next\s+dev|"
    r"uvicorn\b.*(--reload|\breload\b)|"
    r"flask\s+run|"
    r"django(-admin)?\s+runserver|"
    r"python(?:3)?\s+-m\s+http\.server|"
    r"npx\s+serve|"
    r"nodemon\b|"
    r"tail\s+-f|"
    r"--watch\b"
    r")",
    re.I,
)


def is_daemon_command(command: str) -> bool:
    """True for processes that are not expected to exit (dev servers, watchers)."""
    return bool(_DAEMON_RE.search(command or ""))


def is_long_running_command(command: str) -> bool:
    """Alias used by verify_run / older call sites — daemons only, not training jobs."""
    return is_daemon_command(command)


_TAIL_FILTER_RE = re.compile(
    r"\s*\|\s*(?:"
    r"Select-Object\s+-Last\s+\d+"
    r"|select\s+-Last\s+\d+"
    r"|tail\s+(?:-n\s*)?\d+"
    r")\s*$",
    re.I,
)


def strip_output_tail_filter(command: str) -> tuple[str, bool]:
    """Drop `| Select-Object -Last N` / `tail` — they hide logs until the command exits."""
    cmd = (command or "").rstrip()
    new, n = _TAIL_FILTER_RE.subn("", cmd, count=1)
    return new.strip() or cmd, n > 0


_INTERACTIVE_SCAFFOLD_RE = re.compile(
    r"("
    r"npm\s+create\s+vue|"
    r"npm\s+init\s+vue|"
    r"yarn\s+create\s+vue|"
    r"pnpm\s+create\s+vue|"
    r"npm\s+create\s+vite|"
    r"yarn\s+create\s+vite|"
    r"pnpm\s+create\s+vite|"
    r"create-react-app\b|"
    r"npx\s+create-react-app\b|"
    r"ng\s+new\b|"
    r"vue\s+create\b|"
    r"npx\s+@vue/cli\b|"
    r"npm\s+create\s+next-app|"
    r"npx\s+create-next-app|"
    r"npm\s+create\s+svelte|"
    r"npm\s+create\s+astro"
    r")",
    re.I,
)


def looks_interactive_scaffold(command: str) -> bool:
    return bool(_INTERACTIVE_SCAFFOLD_RE.search(command or ""))


def has_noninteractive_flags(command: str) -> bool:
    low = (command or "").lower()
    markers = (
        "--default",
        "--template",
        "--typescript",
        "--ts",
        "--javascript",
        "--js",
        "--router",
        "--pinia",
        "--with-tests",
        "--eslint",
        "--yes",
        " -y",
        "--ci",
        "--use-npm",
        "--use-pnpm",
        "--use-yarn",
        "--tailwind",
        "--app",
        "--src-dir",
    )
    if any(m in low for m in markers):
        return True
    if re.search(r"\s--\s+--", command or ""):
        return True
    return False


_DANGEROUS_SHELL_RE = re.compile(
    r"("
    r"rm\s+-rf\s+/|"
    r"rm\s+-rf\s+~|"
    r"format\s+c:|"
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;|"
    r"del\s+/s\s+/q\s+c:|"
    r"rd\s+/s\s+/q\s+c:|"
    r"mkfs\.|"
    r"dd\s+if=.*of=/dev/|"
    r">\s*/dev/sd|"
    r"\bshutdown\b|"
    r"\breboot\b"
    r")",
    re.I,
)

# Remove-Item -Recurse of a drive/home/unix *root* only — not project folders.
_REMOVE_ITEM_ROOT_RE = re.compile(
    r"remove-item\b(?=.*-recurse).*(?:"
    r"['\"][a-z]:[\\/]*['\"]"
    r"|[a-z]:[\\/]*(?:\s|$)"
    r"|['\"]~['\"]|(?<=\s)~(?:\s|$)"
    r"|['\"]/[\\/]*['\"]|(?<=\s)/(?:\s|$)"
    r"|\$home(?:\s|$|['\"])"
    r"|\$env:userprofile(?:\s|$|['\"])"
    r")",
    re.I,
)


def is_dangerous_shell(command: str) -> bool:
    low = (command or "").lower()
    if _DANGEROUS_SHELL_RE.search(low):
        return True
    return bool(_REMOVE_ITEM_ROOT_RE.search(low))


# Leading command of a pipe segment that only reads/lists — never mutates.
_READONLY_LEAD_RE = re.compile(
    r"^(?:"
    r"ls|dir|gci|get-childitem|"
    r"pwd|get-location|"
    r"cat|type|gc|get-content|"
    r"head|tail|"
    r"find|where|"
    r"grep|rg|findstr|select-string|"
    r"echo|write-output|write-host|"
    r"git\s+(?:status|log|diff|branch|show|remote(?:\s+-v)?)|"
    r"npm\s+(?:list|ls|outdated|view)|"
    r"pip\s+(?:list|show|freeze)|"
    r"(?:python3?|node|npm|pip|git)\s+(?:--version|-v|-V)\b|"
    r"whoami|hostname|uname|"
    r"test-path|"
    r"tree|"
    r"wc|sort|uniq|more|less"
    r")\b",
    re.I,
)

# Non-leading pipe segments that only reformat/filter — never mutate.
_READONLY_FILTER_RE = re.compile(
    r"^(?:"
    r"select-object|format-table|format-list|sort-object|where-object|measure-object|"
    r"convertto-json|convertto-csv|out-string|out-host|"
    r"grep|rg|findstr|select-string|wc|sort|uniq|head|tail|more|less"
    r")\b",
    re.I,
)

# Anywhere in the command — any of these means it can mutate; block outright.
_MUTATING_ANYWHERE_RE = re.compile(
    r"(\brm\s|\bdel\s|remove-item|new-item|set-content|add-content|out-file|"
    r"copy-item|move-item|rename-item|\bmkdir\b|\brmdir\b|\btouch\s|"
    r"git\s+(?:commit|push|reset|checkout|merge|rebase|apply|clean|add)|"
    r"npm\s+(?:install|i\s|uninstall|update)|pip\s+(?:install|uninstall)|"
    r"yarn\s+(?:add|remove)|pnpm\s+(?:add|remove|install)|"
    r"chmod|chown|\bkill\s|taskkill|shutdown|reboot|\bformat\s|"
    r"\bdd\b|mkfs|sed\s+-i|"
    r"set-location|\bcd\s"
    r")",
    re.I,
)


def is_readonly_shell_command(command: str) -> bool:
    """Conservative allowlist: true only for commands that cannot mutate anything.

    Used to let the agent inspect the workspace with ``run_shell`` (e.g. a
    directory listing) during plan-prep / explore-only phases, the same way
    ``list_dir``/``read_file`` are already allowed there.
    """
    text = (command or "").strip()
    if not text or ">" in text:
        return False
    if _MUTATING_ANYWHERE_RE.search(text):
        return False
    statements = re.split(r"[;\n]|&&|\|\|", text)
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        segments = stmt.split("|")
        for i, seg in enumerate(segments):
            seg = seg.strip()
            if not seg:
                return False
            if _READONLY_LEAD_RE.match(seg):
                continue
            if i > 0 and _READONLY_FILTER_RE.match(seg):
                continue
            return False
    return True
