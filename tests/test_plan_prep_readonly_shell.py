"""Read-only run_shell calls (dir listing, git status, ...) must not be
blocked by the plan-prep "still gathering information" guard — only actual
mutating shell commands should be.
"""

from __future__ import annotations

import json
from typing import Any

from metateam.core.config import Settings
from metateam.core.guardrails import Guardrails
from metateam.runtime.agent_execute import AgentExecuteMixin
from metateam.runtime.approval import ApprovalGate
from metateam.runtime.tool_registry import Tool, ToolRegistry


class _FakeAgent(AgentExecuteMixin):
    def __init__(self, registry: ToolRegistry, *, allow_mutating: bool) -> None:
        self.registry = registry
        self.settings = Settings()
        self.settings.tool_result_cap = 100_000
        self.approval = ApprovalGate()
        self.guard = Guardrails()
        self.is_subagent = False
        self.goal = ""
        self.role = "leaf"
        self._allow_mutating_tools = allow_mutating
        self._turn_mutated = False
        self._turn_verified = False
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _emit(self, type_: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((type_, data or {}))

    def cancelled(self) -> bool:
        return False

    def _ingest_workspace_fact(self, name: str, args: dict[str, Any], content: str) -> None:
        return None


def _stub_registry() -> ToolRegistry:
    """A registry with a stub run_shell handler — no real subprocess spawned."""
    reg = ToolRegistry()
    reg.register(
        Tool(
            "run_shell",
            "stub",
            {"type": "object", "properties": {"command": {"type": "string"}}},
            lambda command="": f"stub output for: {command}",
            parallel_safe=False,
            requires_approval=True,
        )
    )
    return reg


def _tc(command: str) -> dict[str, Any]:
    return {
        "id": "c1",
        "function": {"name": "run_shell", "arguments": json.dumps({"command": command})},
    }


def test_readonly_shell_allowed_during_plan_prep() -> None:
    agent = _FakeAgent(_stub_registry(), allow_mutating=False)
    agent.approval.request = lambda *a, **k: True  # type: ignore[method-assign]
    result = agent._execute_one(
        _tc("Get-ChildItem -File | Select-Object Name,Length | Format-Table -AutoSize")
    )
    assert "still gathering information" not in result["content"]
    assert "stub output" in result["content"]


def test_mutating_shell_still_blocked_during_plan_prep() -> None:
    agent = _FakeAgent(_stub_registry(), allow_mutating=False)
    result = agent._execute_one(_tc("Remove-Item -Recurse .\\dist"))
    assert "still gathering information" in result["content"]


def test_readonly_shell_still_requires_approval_outside_prep() -> None:
    """The plan-prep exemption must not skip the normal approval gate."""
    agent = _FakeAgent(_stub_registry(), allow_mutating=True)
    agent.approval.request = lambda *a, **k: False  # type: ignore[method-assign]
    result = agent._execute_one(_tc("Get-ChildItem"))
    assert "rejected" in result["content"] or "timed out" in result["content"]
