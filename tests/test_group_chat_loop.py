"""Group chat reply loop (ui/src/social/groupLoop.ts).

The group orchestration lives in the TypeScript UI, so this runs its node
tests (free-form reply loop, explicit-pipeline detection, prompts) as part of
the Python suite. Skips when a Node with type stripping is not installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"
TESTS = ["groupLoop.test.ts", "routing.test.ts", "harness.test.ts"]


def _node() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        major = int(out.lstrip("v").split(".")[0])
    except Exception:
        return None
    return node if major >= 23 else None


@pytest.mark.skipif(_node() is None, reason="Node >= 23 (type stripping) not available")
def test_group_chat_loop_node_suite() -> None:
    node = _node()
    assert node
    proc = subprocess.run(
        [node, "--import", "./ts-ext-hook.mjs", "--test", *TESTS],
        cwd=UI,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    tail = (proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:]
    assert proc.returncode == 0, tail
    assert "fail 0" in proc.stdout, tail
