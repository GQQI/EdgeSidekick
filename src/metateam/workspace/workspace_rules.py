"""Load versioned project rules from the workspace (injected into the system prompt)."""

from __future__ import annotations

from pathlib import Path

# First existing file wins. Team-shared conventions belong in git, not MEMORY.md.
CANDIDATES = (
    ".edgesidekick/rules.md",
    ".sidekick/rules.md",
    "AGENTS.md",
)

MAX_CHARS = 8_000


def find_rules_file(workspace: Path) -> Path | None:
    root = workspace.resolve()
    for rel in CANDIDATES:
        path = root / rel
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def load_workspace_rules(workspace: Path, *, max_chars: int = MAX_CHARS) -> str:
    path = find_rules_file(workspace)
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    rel = path.relative_to(workspace.resolve()).as_posix()
    body = text if len(text) <= max_chars else text[: max_chars - 20].rstrip() + "\n…[truncated]"
    return f"## Project rules ({rel})\nFollow these workspace conventions:\n\n{body}"
