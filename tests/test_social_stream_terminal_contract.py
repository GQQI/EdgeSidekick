"""Contract tests for social empty-content / browser_open regressions.

These assert the source invariants that caused:
  - UI showing socialEmptyReply after a real reply / error
  - homepage jump when browser_open re-fired without a fresh URL
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_stream_chat_does_not_empty_final_after_error():
    src = _read("ui/src/api/index.ts")
    assert "let sawError = false" in src
    assert "if (!sawFinal && !aborted && !sawError)" in src
    # final must be delivered before reader.cancel
    assert "handlers.onFinal(String(ev.data?.text" in src
    cancel_idx = src.find("await reader.cancel()")
    on_final_idx = src.find("handlers.onFinal(String(ev.data?.text")
    assert 0 < on_final_idx < cancel_idx


def test_social_execution_waits_for_goal_before_closing_tasks():
    social = _read("ui/src/social/SocialShell.tsx")
    assert "shouldAutoCompleteExecution" not in social
    assert 'anyEv?.type === "goal_settled"' in social
    assert "doneTasksForExecution" in social
    agent_dir = ROOT / "src/metateam/runtime/agent"
    agent = "\n".join(p.read_text(encoding="utf-8") for p in sorted(agent_dir.glob("*.py")))
    assert "hold_for_goal" in agent
    assert "turn_requires_completion" not in agent


def test_social_on_final_keeps_streamed_text():
    src = _read("ui/src/social/SocialShell.tsx")
    assert "lastGoodBuf" in src
    assert "paintedKeep" in src
    assert 'slot.stage === "error"' in src
    assert "hasBrowserPreview" in src


def test_browser_open_reveal_only_does_not_renavigate():
    agent = _read("src/metateam/runtime/agent/agent_execute.py")
    assert '"reveal_only": not bool(nav_url)' in agent
    social = _read("ui/src/social/SocialShell.tsx")
    assert "revealOnly" in social
    assert "stale" in social.lower() or "homepage" in social.lower()
    app = _read("ui/src/App.tsx")
    assert "skipRewind" in app or "tgtIsHome" in app


def test_observe_defaults_are_viewport_first():
    obs = _read("src/metateam/browser/observe.py")
    assert "limit: int = 40" in obs
    assert "expand: int = 0" in obs
    assert "in_viewport" in obs
    sandbox = _read("src/metateam/browser/sandbox.py")
    assert "limit: int = 40" in sandbox
    assert "expand: int = 0" in sandbox
