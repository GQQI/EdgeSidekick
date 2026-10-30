from __future__ import annotations

import os
from pathlib import Path

from metateam.runtime.shell_policy import (
    has_noninteractive_flags,
    is_dangerous_shell,
    is_long_running_command,
    is_readonly_shell_command,
    looks_interactive_scaffold,
    strip_output_tail_filter,
)
from metateam.services.shell_sandbox import (
    ShellSandboxPolicy,
    check_command,
    outside_shell_paths,
)


def test_long_running() -> None:
    assert is_long_running_command("npm run dev")
    assert is_long_running_command("python -m http.server 8000")
    assert not is_long_running_command("python train.py --epochs 3")
    assert not is_long_running_command("npm test")
    assert not is_long_running_command("npm install vite")


def test_interactive_scaffold() -> None:
    assert looks_interactive_scaffold("npm create vue@latest app")
    assert not has_noninteractive_flags("npm create vue@latest app")
    assert has_noninteractive_flags("npm create vue@latest app -- --default")
    assert has_noninteractive_flags("npm create vite@latest app -- --template vue")


def test_readonly_shell_allows_listing() -> None:
    assert is_readonly_shell_command(
        "Get-ChildItem -File | Select-Object Name,Length | Format-Table -AutoSize"
    )
    assert is_readonly_shell_command("ls -la")
    assert is_readonly_shell_command("dir")
    assert is_readonly_shell_command("git status")
    assert is_readonly_shell_command("git log --oneline -5")
    assert is_readonly_shell_command("cat package.json")
    assert is_readonly_shell_command("Get-Content .\\README.md")
    assert is_readonly_shell_command("pwd")
    assert is_readonly_shell_command("node --version")
    assert is_readonly_shell_command("rg TODO src")


def test_readonly_shell_rejects_mutation() -> None:
    assert not is_readonly_shell_command("rm -rf build")
    assert not is_readonly_shell_command("Remove-Item -Recurse .\\dist")
    assert not is_readonly_shell_command("git commit -m x")
    assert not is_readonly_shell_command("npm install lodash")
    assert not is_readonly_shell_command("echo hi > out.txt")
    assert not is_readonly_shell_command("ls; rm -rf /tmp/x")
    assert not is_readonly_shell_command("Get-ChildItem | Remove-Item")
    assert not is_readonly_shell_command("cd ..")
    assert not is_readonly_shell_command("")


def test_dangerous_shell() -> None:
    assert is_dangerous_shell("rm -rf /")
    assert is_dangerous_shell("Remove-Item -Recurse C:\\")
    assert not is_dangerous_shell("Remove-Item -Path .\\login-page -Recurse -Force")
    assert not is_dangerous_shell("pytest -q")


def test_strip_tail_filter() -> None:
    cmd, stripped = strip_output_tail_filter("npm run build | Select-Object -Last 40")
    assert stripped
    assert cmd == "npm run build"
    same, flag = strip_output_tail_filter("npm test")
    assert not flag
    assert same == "npm test"


def test_check_command_empty(tmp_path: Path) -> None:
    policy = ShellSandboxPolicy.for_workspace(tmp_path)
    assert check_command("", cwd=tmp_path, policy=policy)
    assert check_command("echo hi", cwd=tmp_path, policy=policy) is None


def test_check_command_cd_dotdot_is_not_hard_blocked(tmp_path: Path) -> None:
    policy = ShellSandboxPolicy.for_workspace(tmp_path)
    assert check_command("cd ..", cwd=tmp_path, policy=policy) is None
    assert "cd .." in outside_shell_paths("cd ..", cwd=tmp_path, policy=policy)


def test_check_command_masks_urls(tmp_path: Path) -> None:
    policy = ShellSandboxPolicy.for_workspace(tmp_path)
    assert check_command("curl https://example.com/path", cwd=tmp_path, policy=policy) is None


def test_outside_paths_need_approval_not_hard_block(tmp_path: Path) -> None:
    policy = ShellSandboxPolicy.for_workspace(tmp_path)
    if os.name == "nt":
        cmd = r"type C:\Windows\System32\drivers\etc\hosts"
    else:
        cmd = "cat /etc/passwd"
    assert check_command(cmd, cwd=tmp_path, policy=policy) is None
    found = outside_shell_paths(cmd, cwd=tmp_path, policy=policy)
    assert found, "outside path should be flagged for approval"
