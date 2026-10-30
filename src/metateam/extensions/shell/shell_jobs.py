"""Tracked background shell jobs (Cursor-style experiment / long script runs)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.events import new_id
from ...core.hostinfo import augment_executable_path, shell_argv
from ...session.tenant_context import get_session_id, get_user_id


def _subprocess_text_kwargs() -> dict[str, Any]:
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


# Windows: a Job Object owns the whole process tree, so stopping a job still
# works after the shell wrapper has exited and a dev server has been reused by
# a grandchild (taskkill /T only follows live parent links).
_win_k32: Any = None
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _win_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _win_k32.CreateJobObjectW.restype = wintypes.HANDLE
    _win_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _win_k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _win_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _win_k32.TerminateJobObject.restype = wintypes.BOOL
    _win_k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _win_k32.QueryInformationJobObject.restype = wintypes.BOOL
    _win_k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _win_k32.OpenProcess.restype = wintypes.HANDLE
    _win_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _win_k32.CloseHandle.restype = wintypes.BOOL
    _win_k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _WinJobAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodUserTime", ctypes.c_int64),
            ("ThisPeriodKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]


def _win_job_create(pid: int) -> Any:
    """Create a job object and put the freshly spawned shell in it."""
    if _win_k32 is None or not pid:
        return None
    handle = _win_k32.CreateJobObjectW(None, None)
    if not handle:
        return None
    # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    proc = _win_k32.OpenProcess(0x0001 | 0x0100, False, int(pid))
    if not proc:
        _win_k32.CloseHandle(handle)
        return None
    ok = _win_k32.AssignProcessToJobObject(handle, proc)
    _win_k32.CloseHandle(proc)
    if not ok:
        _win_k32.CloseHandle(handle)
        return None
    return handle


def _win_job_active(handle: Any) -> int:
    if _win_k32 is None or not handle:
        return 0
    info = _WinJobAccounting()
    ok = _win_k32.QueryInformationJobObject(
        handle, 1, ctypes.byref(info), ctypes.sizeof(info), None  # JobObjectBasicAccountingInformation
    )
    return int(info.ActiveProcesses) if ok else 0


def _win_job_terminate(handle: Any) -> None:
    if _win_k32 is None or not handle:
        return
    _win_k32.TerminateJobObject(handle, 1)


def _win_job_close(handle: Any) -> None:
    if _win_k32 is None or not handle:
        return
    _win_k32.CloseHandle(handle)


def _posix_group_alive(pgid: int) -> bool:
    if not pgid:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except OSError:
        return False


@dataclass
class ShellJob:
    id: str
    pid: int
    command: str
    cwd: str
    user_id: str
    session_id: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    status: str = "running"  # running | exited | killed
    background: bool = False
    released: bool = False
    notified: bool = False
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=4000))
    proc: Any = None
    pgid: int = 0
    job_obj: Any = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append_line(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)

    def log_text(self, *, tail: int = 80, since: int = 0) -> tuple[str, int]:
        with self._lock:
            items = list(self.lines)
        start = max(0, int(since or 0))
        sliced = items[start:]
        if tail > 0:
            sliced = sliced[-int(tail) :]
        return "".join(sliced), len(items)

    def snapshot(self, *, tail: int = 40) -> dict[str, Any]:
        log, n = self.log_text(tail=tail)
        still = self.alive()
        group = still or self.group_alive()
        return {
            "job_id": self.id,
            "pid": self.pid,
            "command": self.command,
            "cwd": self.cwd,
            "session_id": self.session_id,
            "background": self.background,
            "status": "running" if still else self.status,
            "group_alive": group,
            "exit_code": None if still else self.exit_code,
            "started_at": self.started_at,
            "ended_at": None if still else self.ended_at,
            "elapsed_sec": round((time.time() if still else (self.ended_at or time.time())) - self.started_at, 2),
            "log_lines": n,
            "log": log,
        }

    def alive(self) -> bool:
        proc = self.proc
        if proc is None:
            return self.status == "running"
        code = proc.poll()
        if code is None:
            return True
        if self.status == "running":
            self.status = "exited"
            self.exit_code = int(code)
            self.ended_at = time.time()
        return False

    def group_alive(self) -> bool:
        """True while any process from this job runs, even after the shell exited.

        A dev server can outlive its wrapper (``Start-Process``, npm's cmd
        shim, orphaned grandchildren). The web UI uses this to keep a stop
        action available and the stop path uses it to clean those up.
        """
        if self.alive():
            return True
        if self.job_obj:
            return _win_job_active(self.job_obj) > 0
        return _posix_group_alive(self.pgid)


class ShellJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, ShellJob] = {}
        self._done_hooks: list[Any] = []

    def on_done(self, fn: Any) -> None:
        self._done_hooks.append(fn)

    def start(
        self,
        command: str,
        *,
        cwd: str,
        env: Optional[dict[str, str]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        stdin_text: str = "",
        background: bool = False,
    ) -> ShellJob:
        uid = user_id or get_user_id()
        sid = (session_id if session_id is not None else get_session_id()) or ""
        child_env = augment_executable_path(dict(env or os.environ))
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        argv = shell_argv(command)
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            **_subprocess_text_kwargs(),
            "env": child_env,
        }
        if stdin_text:
            popen_kwargs["stdin"] = subprocess.PIPE
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(argv, **popen_kwargs)
        except OSError as exc:
            winerr = int(getattr(exc, "winerror", 0) or 0)
            if winerr not in (2, 3) and not isinstance(exc, FileNotFoundError):
                raise
            job = ShellJob(
                id=new_id("job"),
                pid=0,
                command=command,
                cwd=cwd,
                user_id=uid,
                session_id=sid,
                background=bool(background),
                proc=None,
                status="exited",
                exit_code=127,
                ended_at=time.time(),
            )
            exe = argv[0] if argv else "powershell.exe"
            job.append_line(
                f"ERROR: Win32 cannot start the shell executable {exe!r} ({exc}).\n"
                r"PowerShell is resolved at %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe "
                "(not by a bare PATH lookup). For bash/.sh scripts, install Git for Windows "
                "so bash.exe exists, then re-run as `bash script.sh`.\n"
            )
            with self._lock:
                self._jobs[job.id] = job
                self._gc_locked()
            return job
        if stdin_text and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text)
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass
        pid = int(proc.pid or 0)
        pgid = 0
        if os.name != "nt" and pid:
            try:
                pgid = os.getpgid(pid)
            except OSError:
                pgid = 0
        job = ShellJob(
            id=new_id("job"),
            pid=pid,
            command=command,
            cwd=cwd,
            user_id=uid,
            session_id=sid,
            background=bool(background),
            proc=proc,
            pgid=pgid,
            job_obj=_win_job_create(pid),
        )
        threading.Thread(target=self._pump, args=(job,), name=f"shell-job-{job.id}", daemon=True).start()
        with self._lock:
            self._jobs[job.id] = job
            self._gc_locked()
        return job

    def _pump(self, job: ShellJob) -> None:
        proc = job.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                job.append_line(line)
        except Exception:
            pass
        try:
            code = proc.wait()
        except Exception:
            code = proc.poll()
        job.exit_code = None if code is None else int(code)
        job.ended_at = time.time()
        if job.status == "running":
            job.status = "exited"
        self._fire_done(job)

    def get(self, job_id: str, *, user_id: Optional[str] = None) -> Optional[ShellJob]:
        uid = user_id or get_user_id()
        with self._lock:
            job = self._jobs.get((job_id or "").strip())
        if not job or job.user_id != uid:
            return None
        job.alive()
        return job

    def list(self, *, user_id: Optional[str] = None, include_done: bool = True) -> list[ShellJob]:
        uid = user_id or get_user_id()
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.user_id == uid]
        out = []
        for j in jobs:
            j.alive()
            if include_done or j.status == "running":
                out.append(j)
        out.sort(key=lambda j: j.started_at, reverse=True)
        return out

    def remove(self, job_id: str, *, user_id: Optional[str] = None) -> bool:
        """Delete a finished job. Leftover child processes are stopped first."""
        job = self.get(job_id, user_id=user_id)
        if not job:
            return False
        if job.alive():
            return False
        if job.group_alive():
            self._terminate_group(job)
            time.sleep(0.15)
            if job.alive():
                return False
        with self._lock:
            self._jobs.pop(job.id, None)
        _win_job_close(job.job_obj)
        return True

    def wait(self, job_id: str, timeout: float, *, user_id: Optional[str] = None) -> Optional[ShellJob]:
        job = self.get(job_id, user_id=user_id)
        if not job:
            return None
        deadline = time.monotonic() + max(0.1, float(timeout or 0))
        while job.alive() and time.monotonic() < deadline:
            time.sleep(0.2)
        return job

    def _terminate_group(self, job: ShellJob) -> bool:
        """Kill the wrapper and every descendant. True if anything was alive."""
        if os.name == "nt":
            wrapper = job.proc
            wrapper_alive = bool(wrapper is not None and wrapper.poll() is None)
            if job.job_obj:
                active = _win_job_active(job.job_obj)
                _win_job_terminate(job.job_obj)
                return wrapper_alive or active > 0
            # No job object (assignment refused): fall back to the shell tree.
            if job.pid:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(job.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=8,
                        check=False,
                    )
                except Exception:
                    pass
            if wrapper_alive and wrapper is not None:
                try:
                    wrapper.kill()
                except Exception:
                    pass
            return wrapper_alive

        was_alive = job.alive()
        group_had = _posix_group_alive(job.pgid)
        proc = job.proc
        if job.pgid:
            try:
                os.killpg(job.pgid, signal.SIGTERM)
            except OSError:
                pass
        elif proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        deadline = time.monotonic() + 1.5
        while was_alive and time.monotonic() < deadline:
            if job.pgid and not _posix_group_alive(job.pgid):
                break
            if not job.pgid and not job.alive():
                break
            time.sleep(0.05)
        if job.pgid and _posix_group_alive(job.pgid):
            try:
                os.killpg(job.pgid, signal.SIGKILL)
            except OSError:
                pass
        elif proc is not None and job.alive():
            try:
                proc.kill()
            except Exception:
                pass
        return was_alive or group_had

    def stop(self, job_id: str, *, user_id: Optional[str] = None) -> Optional[ShellJob]:
        job = self.get(job_id, user_id=user_id)
        if not job:
            return None
        # Even an "exited" wrapper can leave the dev server running; the group
        # kill makes stop/close actually free the ports.
        had_work = self._terminate_group(job)
        time.sleep(0.15)
        if had_work or job.alive():
            job.status = "killed"
            job.ended_at = job.ended_at or time.time()
        self._fire_done(job)
        return job

    def mark_released(self, job: ShellJob) -> None:
        """Hand the process to the background — completion will notify the chat."""
        job.background = True
        job.released = True
        if not job.alive():
            self._fire_done(job)

    def _fire_done(self, job: ShellJob) -> None:
        if job.alive() or not job.released or job.notified:
            return
        job.notified = True
        try:
            from ..shell_job_notify import deliver_job_done

            deliver_job_done(job)
        except Exception:
            pass
        for fn in list(self._done_hooks):
            try:
                fn(job)
            except Exception:
                pass

    def _gc_locked(self) -> None:
        done = [j for j in self._jobs.values() if j.status != "running"]
        if len(done) <= 40:
            return
        done.sort(key=lambda j: j.ended_at or 0)
        for j in done[: len(done) - 40]:
            self._jobs.pop(j.id, None)
            _win_job_close(j.job_obj)


JOBS = ShellJobManager()


def format_job_result(job: ShellJob, *, background: bool, note: str = "", tail: int = 60) -> str:
    snap = job.snapshot(tail=tail)
    still = snap["status"] == "running"
    lines = [
        f"job_id={snap['job_id']} pid={snap['pid']} status={snap['status']}"
        + (f" exit={snap['exit_code']}" if snap["exit_code"] is not None else "")
        + (" background=true" if background or still else ""),
        f"command={job.command!r}",
        f"elapsed={snap['elapsed_sec']}s",
    ]
    if still:
        lines.append(
            "Process is running in the background. "
            "Do NOT call run_shell again with the same command. "
            "Use shell_job_log / shell_job_wait / shell_job_stop with this job_id."
        )
    elif snap.get("group_alive"):
        lines.append(
            "The shell exited but child processes from this job are still running "
            "(for example a dev server). Call shell_job_stop to terminate them."
        )
    if note:
        lines.append(note)
    log = (snap.get("log") or "").rstrip() or "(no output yet)"
    lines.append("--- log ---")
    lines.append(log)
    return "\n".join(lines)


def format_job_done_notice(job: ShellJob) -> str:
    snap = job.snapshot(tail=40)
    status = snap["status"]
    if status == "killed":
        title = "后台任务已停止"
    elif snap.get("exit_code") not in (None, 0):
        title = "后台任务结束（失败）"
    else:
        title = "后台任务已结束"
    lines = [
        title,
        "",
        f"- 命令：`{job.command}`",
        f"- 状态：{status}"
        + (f"（exit {snap['exit_code']}）" if snap.get("exit_code") is not None else ""),
        f"- 用时：{snap['elapsed_sec']}s",
        f"- job_id：{job.id}",
    ]
    log = (snap.get("log") or "").rstrip()
    if log:
        lines.append("")
        lines.append("```")
        lines.append(log)
        lines.append("```")
    return "\n".join(lines)
