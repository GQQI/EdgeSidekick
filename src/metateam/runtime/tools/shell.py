"""run_shell and verify_run."""

from __future__ import annotations

import os
import subprocess

from ..shell_policy import (
    has_noninteractive_flags as _has_noninteractive_flags,
    is_daemon_command,
    is_long_running_command as _is_long_running_command,
    looks_interactive_scaffold as _looks_interactive_scaffold,
    strip_output_tail_filter as _strip_output_tail_filter,
)
from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext
from .support import (
    _guard_shell,
    _sandboxed_env,
    _shell_argv,
    _shell_cwd,
    _shell_host_label,
    _subprocess_text_kwargs,
)


def register_shell_tools(reg: ToolRegistry, ctx: ToolContext) -> None:
    settings = ctx.settings
    live_ws = ctx.live_ws

    def verify_run(command: str, timeout_sec: int = 120) -> str:
        """Run a verification command (tests/lint). Requires approval; needs shell enabled."""
        if not settings.allow_shell:
            return (
                "ERROR: shell disabled (META_ALLOW_SHELL=0). "
                "Enable shell to run verify_run, or tell the user the verify command to run locally."
            )
        cmd = (command or "").strip()
        if not cmd:
            return "ERROR: empty command"
        blocked = _guard_shell(cmd, settings=settings, workspace=live_ws())
        if blocked:
            return blocked
        if _is_long_running_command(cmd):
            return "ERROR: verify_run is for one-shot checks, not long-running servers"
        cwd_err, cwd = _shell_cwd(live_ws())
        if cwd_err:
            return cwd_err
        timeout = max(15, min(int(timeout_sec or 120), 600))
        try:
            proc = subprocess.run(
                _shell_argv(cmd),
                cwd=cwd,
                capture_output=True,
                **_subprocess_text_kwargs(),
                timeout=timeout,
                shell=False,
                env=_sandboxed_env(settings),
            )
        except subprocess.TimeoutExpired:
            return f"VERIFY FAIL timeout={timeout}s command={cmd!r}"
        except Exception as exc:  # noqa: BLE001
            return f"VERIFY FAIL error={exc}"
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if len(out) > 12_000:
            out = out[:12_000] + "\n…[truncated]"
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return f"VERIFY {status} exit={proc.returncode}\ncommand={cmd!r}\n---\n{out or '(no output)'}"

    def run_shell(
        command: str,
        background: bool = False,
        stdin_text: str = "",
        timeout_sec: int = 0,
    ) -> str:
        if not settings.allow_shell:
            return "ERROR: shell disabled (set META_ALLOW_SHELL=1 to enable)"
        command, _stripped_tail = _strip_output_tail_filter(command)
        low = command.lower().strip()
        blocked = _guard_shell(command, settings=settings, workspace=live_ws())
        if blocked:
            return blocked

        # Interactive scaffolding CLIs hang with no TTY — steer to non-interactive.
        if _looks_interactive_scaffold(low) and not _has_noninteractive_flags(low):
            return (
                "ERROR: this command looks like an interactive scaffold CLI "
                "(create-vue / create-react-app / angular / etc.). "
                "EdgeSidekick has no TTY for arrow-key menus.\n"
                "Use non-interactive flags instead, for example:\n"
                "  npm create vue@latest my-app -- --default\n"
                "  npm create vue@latest my-app -- --typescript --router --pinia --eslint-with-prettier\n"
                "  npm create vite@latest my-app -- --template vue\n"
                "Or call ask_user to pick options, then re-run with those flags. "
                "Optional: pass stdin_text with newline-separated answers for simple prompts."
            )

        from ...services.shell_jobs import JOBS, format_job_result

        cwd_err, cwd = _shell_cwd(live_ws())
        if cwd_err:
            return cwd_err
        env = _sandboxed_env(settings)
        daemon = is_daemon_command(low)
        long_running = bool(background) or daemon
        try:
            want = int(timeout_sec or 0)
        except (TypeError, ValueError):
            want = 0
        # Foreground default 20s (Cursor-like): still running → background, not kill.
        timeout = max(15, min(want, 600)) if want > 0 else int(settings.shell_timeout or 20)

        job = JOBS.start(command, cwd=cwd, env=env, stdin_text=stdin_text or "")
        if long_running:
            # Servers/watchers never finish; collect a short burst of startup logs.
            # Explicit background=true gets a slightly longer window for first lines.
            JOBS.wait(job.id, 8.0 if background else 3.0)
            if job.alive():
                JOBS.mark_released(job)
                kind = "server/watch" if daemon and not background else "background"
                return format_job_result(
                    job,
                    background=True,
                    note=f"Started in background ({kind}). Poll with shell_job_log.",
                )
            return format_job_result(job, background=False, note="Finished during startup window.")

        JOBS.wait(job.id, timeout)
        if job.alive():
            JOBS.mark_released(job)
            return format_job_result(
                job,
                background=True,
                note=(
                    f"Still running after {timeout}s — moved to background instead of killing it. "
                    "Use shell_job_wait / shell_job_log / shell_job_stop with this job_id. "
                    "Do not re-run the same command."
                ),
            )
        log, _n = job.log_text(tail=200)
        out = (log or "").strip()
        if len(out) > 14_000:
            out = out[:14_000] + "\n…[truncated]"
        return f"job_id={job.id} status=exited exit={job.exit_code}\n{out}"

    reg.register(
        Tool(
            "verify_run",
            "Run a one-shot verification command (tests/lint). Requires approval and META_ALLOW_SHELL=1. "
            "Prefer this over open-ended shell for acceptance checks. "
            "If shape_contract.verify_command is set, run that before claiming done. "
            f"Host shell: {_shell_host_label()} — write the command for that dialect.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_sec": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
            verify_run,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    if settings.allow_shell:
        _shell_desc = (
            "Run a shell command. cwd is ALWAYS the WORKSPACE root — use relative "
            "paths in the command (e.g. `Get-ChildItem .\\src`), not another machine's "
            "absolute path. Prefer read_file for reading files. "
            f"Host shell: {_shell_host_label()}. "
            "IMPORTANT: one-shot commands (ls, tests, short scripts) run in the "
            "foreground. If they are still running after ~20s they are moved to "
            "the background instead of being killed (training jobs, long scripts). "
            "Dev servers and watchers (npm run dev, --watch) go to background "
            "immediately — or pass background=true. "
            "The tool returns job_id + early logs; the process keeps running. "
            "Then use shell_job_log / shell_job_wait / shell_job_stop. "
            "Do NOT call run_shell again with the same command while that job is running. "
            "Scaffold CLIs (create-vue / create-vite / create-next-app) have NO TTY — "
            "always use non-interactive flags, e.g. "
            "`npm create vue@latest my-app -- --default` or "
            "`npm create vite@latest my-app -- --template vue`. "
            "Call ask_user first if the user must pick TypeScript/Router/etc., then encode as flags. "
            "Optional stdin_text pipes line-based answers (prefer flags)."
        )
        if os.name == "nt":
            _shell_desc += (
                " On Windows the default dialect is PowerShell (located by full path). "
                "Use PowerShell syntax for ordinary commands: mkdir path; "
                "New-Item -ItemType Directory -Force; curl.exe or Invoke-WebRequest; "
                "use ';' instead of bash '&&' / 'mkdir -p'. "
                "To run a .sh / bash script, invoke `bash script.sh` — Git Bash is used "
                "when installed; do not wrap with powershell.exe."
            )
        else:
            _shell_desc += (
                " On Linux / 麒麟 use POSIX/bash (mkdir -p, curl, python3). "
                "Do not use PowerShell or Windows drive letters."
            )
        reg.register(
            Tool(
                "run_shell",
                _shell_desc,
                {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "background": {
                            "type": "boolean",
                            "description": (
                                "If true, start as a background job immediately "
                                "(servers, watchers). Training/long scripts can omit this: "
                                "they auto-background after ~20s if still running."
                            ),
                        },
                        "stdin_text": {
                            "type": "string",
                            "description": (
                                "Optional stdin for simple prompts (newline-separated). "
                                "Prefer non-interactive CLI flags for scaffolds."
                            ),
                        },
                        "timeout_sec": {
                            "type": "integer",
                            "description": (
                                "Optional timeout in seconds for foreground commands "
                                f"(default {settings.shell_timeout}s, then auto-background; max 600). "
                                "Ignored when background=true or the command is a dev server/watcher."
                            ),
                        },
                    },
                    "required": ["command"],
                },
                run_shell,
                parallel_safe=False,
                requires_approval=True,
            )
        )

        def shell_job_list(include_done: bool = True) -> str:
            from ...services.shell_jobs import JOBS

            jobs = JOBS.list(include_done=bool(include_done))
            if not jobs:
                return "no shell jobs"
            lines = [f"{len(jobs)} job(s):"]
            for j in jobs:
                snap = j.snapshot(tail=0)
                lines.append(
                    f"- {snap['job_id']} pid={snap['pid']} {snap['status']} "
                    f"{snap['elapsed_sec']}s {j.command!r}"
                )
            return "\n".join(lines)

        def shell_job_log(job_id: str = "", tail: int = 80) -> str:
            from ...services.shell_jobs import JOBS, format_job_result

            job = JOBS.get(job_id)
            if not job:
                return f"ERROR: unknown job_id={job_id!r}. Call shell_job_list."
            return format_job_result(job, background=job.alive(), tail=max(10, min(int(tail or 80), 400)))

        def shell_job_wait(job_id: str = "", timeout_sec: int = 60) -> str:
            from ...services.shell_jobs import JOBS, format_job_result

            job = JOBS.wait(job_id, max(1, min(int(timeout_sec or 60), 600)))
            if not job:
                return f"ERROR: unknown job_id={job_id!r}. Call shell_job_list."
            note = "still running" if job.alive() else "finished"
            return format_job_result(job, background=job.alive(), note=note)

        def shell_job_stop(job_id: str = "") -> str:
            from ...services.shell_jobs import JOBS, format_job_result

            job = JOBS.stop(job_id)
            if not job:
                return f"ERROR: unknown job_id={job_id!r}. Call shell_job_list."
            return format_job_result(job, background=False, note="stop requested")

        reg.register(
            Tool(
                "shell_job_list",
                "List background shell jobs started by run_shell (training, servers, long scripts).",
                {
                    "type": "object",
                    "properties": {
                        "include_done": {"type": "boolean", "default": True},
                    },
                    "required": [],
                },
                shell_job_list,
                parallel_safe=True,
            )
        )
        reg.register(
            Tool(
                "shell_job_log",
                "Read recent logs from a background shell job. Pass job_id from run_shell.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "tail": {"type": "integer", "default": 80},
                    },
                    "required": ["job_id"],
                },
                shell_job_log,
                parallel_safe=True,
            )
        )
        reg.register(
            Tool(
                "shell_job_wait",
                "Wait up to timeout_sec for a background shell job to exit, then return logs. "
                "Does not kill the job if it is still running.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "timeout_sec": {"type": "integer", "default": 60},
                    },
                    "required": ["job_id"],
                },
                shell_job_wait,
                parallel_safe=False,
            )
        )
        reg.register(
            Tool(
                "shell_job_stop",
                "Stop a background shell job (taskkill / SIGTERM).",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
                shell_job_stop,
                parallel_safe=False,
            )
        )
