from __future__ import annotations

import os
import time
from pathlib import Path

from metateam.core.config import Settings
from metateam.runtime.tool_registry import ToolRegistry
from metateam.runtime.tools.context import ToolContext
from metateam.runtime.tools.shell import register_shell_tools
from metateam.services.shell_jobs import JOBS, format_job_result


def _sleep_cmd(seconds: float, echo: str = "") -> str:
    if os.name == "nt":
        extra = f"; Write-Output {echo}" if echo else ""
        return f"Start-Sleep -Seconds {seconds}{extra}"
    extra = f"; echo {echo}" if echo else ""
    return f"sleep {seconds}{extra}"


def test_train_command_is_not_a_daemon() -> None:
    from metateam.runtime.shell_policy import is_daemon_command, is_long_running_command

    assert not is_daemon_command("python train.py --epochs 10")
    assert not is_long_running_command("python train.py --epochs 10")
    assert not is_daemon_command("torchrun --nproc_per_node=2 train.py")
    assert not is_daemon_command("accelerate launch finetune.py")
    assert not is_daemon_command("npm install vite")
    assert is_daemon_command("npm run dev")
    assert is_daemon_command("python -m http.server 8000")


def test_job_start_log_and_exit(tmp_path: Path) -> None:
    job = JOBS.start(_sleep_cmd(0.4, "job-ok"), cwd=str(tmp_path))
    assert job.id.startswith("job_")
    assert job.pid > 0
    finished = JOBS.wait(job.id, 8.0)
    assert finished is not None
    assert not finished.alive()
    text = format_job_result(finished, background=False)
    assert f"job_id={job.id}" in text
    assert "status=exited" in text
    log, n = finished.log_text(tail=40)
    assert n >= 1
    assert "job-ok" in log


def test_timeout_keeps_process_and_stop(tmp_path: Path) -> None:
    job = JOBS.start(_sleep_cmd(20), cwd=str(tmp_path))
    still = JOBS.wait(job.id, 0.8)
    assert still is not None
    assert still.alive()
    text = format_job_result(still, background=True)
    assert "background=true" in text
    assert "shell_job_log" in text
    stopped = JOBS.stop(job.id)
    assert stopped is not None
    time.sleep(0.3)
    assert not stopped.alive()
    assert stopped.status in {"killed", "exited"}


def test_stop_kills_children_after_the_wrapper_exits(tmp_path: Path) -> None:
    """A dev server can outlive its shell; stop must still free it."""
    if os.name != "nt":
        return
    # The detached child deliberately outlives the wrapper by a wide margin:
    # Start-Process can take several seconds to return on a busy/AV-scanned
    # host, and the point of the test is what happens after the wrapper exits.
    cmd = (
        "Start-Process powershell -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 90' "
        "-WindowStyle Hidden; exit 0"
    )
    job = JOBS.start(cmd, cwd=str(tmp_path))
    deadline = time.monotonic() + 45
    while job.alive() and time.monotonic() < deadline:
        time.sleep(0.25)
    assert not job.alive()
    assert job.group_alive()
    JOBS.stop(job.id)
    time.sleep(0.4)
    assert not job.group_alive()
    assert job.status == "killed"


def test_job_list_filters_user(tmp_path: Path) -> None:
    job = JOBS.start(_sleep_cmd(0.2, "listed"), cwd=str(tmp_path))
    ids = {j.id for j in JOBS.list(include_done=True)}
    assert job.id in ids
    JOBS.wait(job.id, 6.0)


def test_multiple_jobs_run_in_parallel(tmp_path: Path) -> None:
    a = JOBS.start(_sleep_cmd(0.8, "one"), cwd=str(tmp_path))
    b = JOBS.start(_sleep_cmd(0.8, "two"), cwd=str(tmp_path))
    JOBS.mark_released(a)
    JOBS.mark_released(b)
    assert a.id != b.id
    assert a.alive() and b.alive()
    JOBS.wait(a.id, 8.0)
    JOBS.wait(b.id, 8.0)
    assert not a.alive() and not b.alive()
    assert a.exit_code == 0 and b.exit_code == 0


def test_background_job_notifies_once(tmp_path: Path) -> None:
    seen: list[str] = []
    JOBS.on_done(lambda job: seen.append(job.id))
    job = JOBS.start(_sleep_cmd(0.3, "done-note"), cwd=str(tmp_path))
    JOBS.mark_released(job)
    JOBS.wait(job.id, 8.0)
    deadline = time.time() + 4
    while not job.notified and time.time() < deadline:
        time.sleep(0.05)
    assert job.notified
    assert seen.count(job.id) == 1
    JOBS.stop(job.id)
    time.sleep(0.2)
    assert seen.count(job.id) == 1


def _shell_tool(tmp_path: Path, *, shell_timeout: int = 2):
    """Register the real run_shell tool against a throwaway workspace."""
    settings = Settings(workspace=tmp_path, allow_shell=True, shell_sandbox=False)
    settings.shell_timeout = shell_timeout
    reg = ToolRegistry()
    ctx = ToolContext(settings=settings, skills=[])
    register_shell_tools(reg, ctx)
    return reg.get("run_shell").handler


def test_experiment_style_command_auto_backgrounds(tmp_path: Path) -> None:
    """A long 'experiment' run (no recognized keyword) still auto-backgrounds.

    run_shell should not block past settings.shell_timeout: once exceeded it
    hands the still-running process to the background job manager instead of
    killing it, and the caller gets the job_id back immediately.
    """
    run_shell = _shell_tool(tmp_path, shell_timeout=1)
    cmd = _sleep_cmd(4, "experiment-running")

    started = time.monotonic()
    out = run_shell(command=cmd)
    elapsed = time.monotonic() - started

    assert elapsed < 3.5, f"run_shell blocked too long ({elapsed:.1f}s) instead of auto-backgrounding"
    assert "job_id=" in out
    assert "background=true" in out
    assert "Do not re-run the same command" in out

    m = out.split("job_id=", 1)[1].split()[0]
    job = JOBS.get(m)
    assert job is not None
    assert job.background is True
    # Give it time to actually finish, then confirm it was released for
    # notification (not silently dropped) exactly once.
    JOBS.wait(job.id, 8.0)
    deadline = time.time() + 4
    while not job.notified and time.time() < deadline:
        time.sleep(0.05)
    assert job.notified


def test_remove_deletes_finished_job(tmp_path: Path) -> None:
    job = JOBS.start(_sleep_cmd(0.2, "to-remove"), cwd=str(tmp_path))
    JOBS.wait(job.id, 8.0)
    assert not job.alive()
    assert JOBS.remove(job.id) is True
    assert JOBS.get(job.id) is None


def test_remove_refuses_while_running(tmp_path: Path) -> None:
    job = JOBS.start(_sleep_cmd(3), cwd=str(tmp_path))
    assert job.alive()
    assert JOBS.remove(job.id) is False
    assert JOBS.get(job.id) is not None
    JOBS.stop(job.id)


def test_remove_unknown_job_returns_false() -> None:
    assert JOBS.remove("job_does_not_exist") is False


def test_known_long_running_keyword_backgrounds_quickly(tmp_path: Path) -> None:
    """Explicit background=true (servers) must not wait for the full shell_timeout."""
    run_shell = _shell_tool(tmp_path, shell_timeout=90)
    if os.name == "nt":
        cmd = "python -c \"import time; time.sleep(20); print('done-train')\""
    else:
        cmd = "python3 -c \"import time; time.sleep(20); print('done-train')\""
    # Force the "long running" branch without depending on a real python
    # interpreter's train/experiment keyword match — use background=True,
    # exactly like the agent would for a training/experiment job.
    started = time.monotonic()
    out = run_shell(command=cmd, background=True)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"background=true command should not wait for shell_timeout ({elapsed:.1f}s)"
    assert "job_id=" in out
    assert "background=true" in out


def test_foreground_job_does_not_notify(tmp_path: Path) -> None:
    seen: list[str] = []
    JOBS.on_done(lambda job: seen.append(job.id))
    job = JOBS.start(_sleep_cmd(0.2, "fg"), cwd=str(tmp_path), background=False)
    JOBS.wait(job.id, 8.0)
    time.sleep(0.4)
    assert job.id not in seen
    assert not job.notified
