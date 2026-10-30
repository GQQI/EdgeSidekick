from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from metateam.runtime.approval import (
    APPROVAL_TOOLS,
    DANGEROUS_SHELL_SCOPE,
    OUTSIDE_WORKSPACE_SCOPE,
    SHELL_OUTSIDE_SCOPE,
    ApprovalGate,
    approval_required,
    approval_scope,
    summarize_tool_call,
    tool_needs_approval,
)
from metateam.runtime.tool_registry import Tool
from metateam.services.app_prefs import load_app_prefs, set_auto_approve_tools


def _dummy_tool(name: str, *, requires_approval: bool) -> Tool:
    return Tool(name, "d", {"type": "object"}, lambda: "ok", requires_approval=requires_approval)


def test_table_covers_browser_and_mcp() -> None:
    assert "browser_navigate" in APPROVAL_TOOLS
    assert "browser_click" in APPROVAL_TOOLS
    assert tool_needs_approval("write_file")
    assert tool_needs_approval("str_replace")
    assert tool_needs_approval("mcp_github_create_issue")
    assert not tool_needs_approval("read_file")
    assert not tool_needs_approval("browser_screenshot")


def test_approval_required_uses_tool_flag() -> None:
    click = _dummy_tool("browser_click", requires_approval=True)
    shot = _dummy_tool("browser_screenshot", requires_approval=False)
    assert approval_required("browser_click", click)
    assert not approval_required("browser_screenshot", shot)
    assert approval_required("mcp_x", None)


def test_serialize_ui_one_pending_at_a_time() -> None:
    gate = ApprovalGate(timeout_sec=5)
    started = threading.Event()

    def first() -> None:
        with gate.serialize_ui():
            started.set()
            gate.request("a", "write_file", {}, "a")

    def second() -> None:
        started.wait(timeout=2)
        with gate.serialize_ui():
            gate.request("b", "run_shell", {}, "b")

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    for _ in range(80):
        if any(p["id"] == "a" for p in gate.pending()):
            break
        time.sleep(0.025)
    t2.start()
    time.sleep(0.08)
    assert [p["id"] for p in gate.pending()] == ["a"]
    gate.decide("a", True)
    for _ in range(80):
        if any(p["id"] == "b" for p in gate.pending()):
            break
        time.sleep(0.025)
    assert any(p["id"] == "b" for p in gate.pending())
    gate.decide("b", True)
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert not t1.is_alive() and not t2.is_alive()


def test_patch_merges_into_original_args() -> None:
    gate = ApprovalGate(timeout_sec=5)
    args = {"path": "a.txt", "content": "old"}
    result: dict[str, bool] = {}

    def worker() -> None:
        result["ok"] = gate.request("id1", "write_file", args, "write a.txt")

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(80):
        if gate.pending():
            break
        time.sleep(0.025)
    assert gate.pending()
    gate.decide("id1", True, patch_args={"content": "accepted hunk"})
    t.join(timeout=2)
    assert result.get("ok") is True
    assert args["content"] == "accepted hunk"
    assert args["path"] == "a.txt"


def test_inside_read_skips_approval(tmp_path: Path) -> None:
    assert not approval_required(
        "read_file",
        args={"path": "notes.md"},
        workspace=tmp_path,
    )
    assert not approval_required(
        "list_dir",
        args={"path": "."},
        workspace=tmp_path,
    )
    assert approval_required(
        "write_file",
        args={"path": "notes.md", "content": "x"},
        workspace=tmp_path,
    )


def test_outside_file_ops_require_approval(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"secret-{tmp_path.name}.txt"
    outside.write_text("nope", encoding="utf-8")
    assert approval_scope("read_file", {"path": str(outside)}, tmp_path) == OUTSIDE_WORKSPACE_SCOPE
    assert approval_required("read_file", args={"path": str(outside)}, workspace=tmp_path)
    assert approval_required("write_file", args={"path": str(outside), "content": "x"}, workspace=tmp_path)
    assert approval_required("list_dir", args={"path": str(outside.parent)}, workspace=tmp_path)
    summary = summarize_tool_call("read_file", {"path": str(outside)}, workspace=tmp_path)
    assert summary.startswith("工作区外 ·")
    inside = summarize_tool_call("read_file", {"path": "a.txt"}, workspace=tmp_path)
    assert not inside.startswith("工作区外")


def test_remember_outside_workspace_does_not_preapprove_inside_writes(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"out-{tmp_path.name}.txt"
    outside.write_text("x", encoding="utf-8")
    gate = ApprovalGate(timeout_sec=5)
    result: dict[str, bool] = {}

    def worker() -> None:
        result["ok"] = gate.request(
            "out1",
            "read_file",
            {"path": str(outside)},
            "工作区外 · 读取",
            scope=OUTSIDE_WORKSPACE_SCOPE,
        )

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(80):
        if gate.pending():
            break
    assert gate.pending()
    assert gate.pending()[0]["tool"] == "read_file"
    gate.decide("out1", True, remember=True)
    t.join(timeout=2)
    assert result.get("ok") is True
    assert gate.is_preapproved(OUTSIDE_WORKSPACE_SCOPE)
    assert not gate.is_preapproved("write_file")
    assert not gate.is_preapproved("read_file")
    assert gate.is_preapproved(approval_scope("write_file", {"path": str(outside)}, tmp_path))


def test_outside_shell_uses_separate_scope(tmp_path: Path) -> None:
    if os.name == "nt":
        cmd = r"& 'C:\Windows\System32\notepad.exe'"
        outside_token = r"C:\Windows\System32\notepad.exe"
    else:
        cmd = "cat /etc/passwd"
        outside_token = "/etc/passwd"
    args = {"command": cmd}
    assert approval_scope("run_shell", args, tmp_path) == SHELL_OUTSIDE_SCOPE
    assert approval_scope("verify_run", args, tmp_path) == SHELL_OUTSIDE_SCOPE
    assert approval_scope("run_shell", {"command": "echo hi"}, tmp_path) == "run_shell"
    summary = summarize_tool_call("run_shell", args, workspace=tmp_path)
    assert summary.startswith("工作区外 ·")
    assert outside_token.split("\\")[-1].split("/")[-1] in summary or "shell" in summary
    inside = summarize_tool_call("run_shell", {"command": "echo hi"}, workspace=tmp_path)
    assert not inside.startswith("工作区外")


def test_remember_shell_does_not_preapprove_outside_shell(tmp_path: Path) -> None:
    if os.name == "nt":
        cmd = r"type C:\Windows\System32\drivers\etc\hosts"
    else:
        cmd = "cat /etc/passwd"
    gate = ApprovalGate(timeout_sec=5)
    gate.remember_tool("run_shell")
    assert gate.is_preapproved("run_shell")
    assert not gate.is_preapproved(SHELL_OUTSIDE_SCOPE)
    assert approval_scope("run_shell", {"command": cmd}, tmp_path) == SHELL_OUTSIDE_SCOPE


def test_allow_all_skips_dialog() -> None:
    gate = ApprovalGate(timeout_sec=1)
    gate.set_allow_all(True)
    assert gate.is_allow_all()
    assert gate.is_preapproved("write_file")
    assert gate.is_preapproved(OUTSIDE_WORKSPACE_SCOPE)
    assert gate.request("x", "write_file", {"path": "a.txt"}, "写入 a.txt") is True
    assert not gate.pending()


def test_set_allow_all_releases_pending() -> None:
    gate = ApprovalGate(timeout_sec=5)
    result: dict[str, bool] = {}

    def worker() -> None:
        result["ok"] = gate.request("pending1", "run_shell", {"command": "echo hi"}, "shell")

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(80):
        if gate.pending():
            break
        time.sleep(0.025)
    assert gate.pending()
    gate.set_allow_all(True)
    t.join(timeout=2)
    assert result.get("ok") is True
    assert not gate.pending()
    assert gate.is_allow_all()


def test_dangerous_shell_uses_separate_scope(tmp_path: Path) -> None:
    args = {"command": "rm -rf /"}
    assert approval_scope("run_shell", args, tmp_path) == DANGEROUS_SHELL_SCOPE
    assert approval_scope("verify_run", args, tmp_path) == DANGEROUS_SHELL_SCOPE
    assert approval_required("run_shell", args=args, workspace=tmp_path)
    summary = summarize_tool_call("run_shell", args, workspace=tmp_path)
    assert summary.startswith("危险删除 ·")
    gate = ApprovalGate(timeout_sec=5)
    gate.remember_tool("run_shell")
    gate.remember_tool(SHELL_OUTSIDE_SCOPE)
    assert not gate.is_preapproved(DANGEROUS_SHELL_SCOPE)


def test_social_session_stays_approved_when_the_safety_switch_is_off(monkeypatch) -> None:
    monkeypatch.setattr("metateam.services.app_prefs.auto_approve_tools", lambda: False)
    from metateam.services.store import SessionStore

    store = SessionStore()
    social = store.create(social=True)
    desk = store.create(social=False)
    assert social.agent.approval.is_allow_all()
    assert desk.agent.approval.is_allow_all() is False
    store.apply_auto_approve_tools(False, user_id=social.user_id)
    assert social.agent.approval.is_allow_all()
    assert desk.agent.approval.is_allow_all() is False


def test_auto_approve_pref_persists(tmp_path: Path, monkeypatch) -> None:
    from metateam.services import app_prefs

    monkeypatch.setattr(app_prefs, "tenant_prefs_path", lambda user_id=None: tmp_path / "prefs.json")
    assert load_app_prefs()["auto_approve_tools"] is False
    assert set_auto_approve_tools(True) is True
    assert load_app_prefs()["auto_approve_tools"] is True
    assert set_auto_approve_tools(False) is False
