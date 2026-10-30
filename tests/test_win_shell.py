from __future__ import annotations

import os

from metateam.core.hostinfo import (
    augment_executable_path,
    looks_like_bash_command,
    shell_argv,
    windows_powershell_exe,
    windows_shell_argv,
)


def test_looks_like_bash_command() -> None:
    assert looks_like_bash_command("bash ./deploy.sh")
    assert looks_like_bash_command("bash.exe -lc 'echo hi'")
    assert looks_like_bash_command("./run.sh --flag")
    assert looks_like_bash_command("#!/usr/bin/env bash\necho hi")
    assert looks_like_bash_command("set -euo pipefail\necho hi")
    assert not looks_like_bash_command("Get-ChildItem .\\src")
    assert not looks_like_bash_command("Test-Path .\\README.md")
    assert not looks_like_bash_command("Get-Content .\\notes.sh")


def test_shell_argv_never_uses_bare_powershell_on_windows() -> None:
    if os.name != "nt":
        argv = shell_argv("echo hi")
        assert argv[0].endswith("sh") or argv[0].endswith("bash")
        return
    argv = windows_shell_argv("Get-Location")
    assert argv[0].lower().endswith("powershell.exe") or argv[0].lower().endswith("pwsh.exe")
    assert os.path.isabs(argv[0])
    assert argv[0].lower() != "powershell.exe"
    assert os.path.isfile(windows_powershell_exe())


def test_bash_script_uses_bash_when_present() -> None:
    if os.name != "nt":
        return
    argv = windows_shell_argv("bash ./hello.sh")
    exe = os.path.basename(argv[0]).lower()
    assert exe in {"bash.exe", "bash", "powershell.exe", "pwsh.exe", "cmd.exe"}
    if exe in {"bash.exe", "bash"}:
        assert argv[1] == "-lc"
        assert "hello.sh" in argv[-1]


def test_augment_path_includes_system32_on_windows() -> None:
    if os.name != "nt":
        return
    env = augment_executable_path({"PATH": r"C:\only-python"})
    blob = env["PATH"].lower()
    assert "system32" in blob
    assert "windowspowershell" in blob
