"""Detect a one-shot test/lint command for the current workspace."""

from __future__ import annotations

import json
from pathlib import Path


def detect_verify_command(workspace: Path) -> str:
    """Return a suggested verify_run command, or empty if none is obvious."""
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return ""

    py = _python_verify(ws)
    if py:
        return py

    js = _node_verify(ws)
    if js:
        return js

    if (ws / "Cargo.toml").is_file():
        return "cargo test"
    if (ws / "go.mod").is_file():
        return "go test ./..."
    return ""


def grounding_verify_hint(workspace: Path) -> str:
    cmd = detect_verify_command(workspace)
    if not cmd:
        return ""
    return (
        f"Suggested verify_run: `{cmd}` — run it after file edits before claiming done."
    )


def _python_verify(ws: Path) -> str:
    if (ws / "pytest.ini").is_file() or (ws / "conftest.py").is_file():
        return "python -m pytest"
    pyproject = ws / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        if "[tool.pytest" in text or "pytest" in text.lower():
            return "python -m pytest"
    if (ws / "tests").is_dir() and any((ws / "tests").glob("test_*.py")):
        return "python -m pytest"
    if (ws / "tox.ini").is_file():
        return "python -m pytest"
    return ""


def _node_verify(ws: Path) -> str:
    pkg = ws / "package.json"
    scripts: dict[str, str] = {}
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            raw = data.get("scripts") or {}
            if isinstance(raw, dict):
                scripts = {str(k): str(v) for k, v in raw.items()}
    for key in ("test", "lint", "typecheck", "check"):
        cmd = scripts.get(key) or ""
        low = cmd.lower()
        if not cmd:
            continue
        if "no test specified" in low or "error: no test" in low:
            continue
        return f"npm run {key}"
    if (ws / "tsconfig.json").is_file():
        return "npx tsc --noEmit"
    return ""
