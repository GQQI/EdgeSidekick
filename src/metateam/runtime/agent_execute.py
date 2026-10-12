"""Tool-call execution, approval, and parallel batches."""

from __future__ import annotations

import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..core.events import new_id
from .approval import approval_required, approval_scope, summarize_tool_call
from .llm import parse_tool_args
from .shell_policy import is_readonly_shell_command
from .tools import missing_required_args, plan_parallel_batches, prepare_tool_args

_INCOMPLETE_WRITE_TOOLS = frozenset({"write_file", "str_replace"})
_FILE_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "str_replace",
        "delete_file",
        "list_dir",
        "search_text",
    }
)
_BLOCKER_RETRY = (
    "\n\nDo not continue as if this succeeded. Retry the same file tool with "
    "the exact filename, or stop and report the blocker."
)
_PAYLOAD_RETRY = (
    "\n\nDo not continue as if this succeeded. Retry the same file tool with "
    "the exact filename and a complete payload, or stop and report the blocker."
)
_MISSING_PATH_RETRY = (
    "\n\nDo not continue as if this succeeded. If the path is wrong, look it up "
    "with list_dir or search_text and retry with that exact path. If it is "
    "already gone, stop and report that. Do not invent file contents for this call."
)
_TOOL_ALIASES = {
    "search": "web_search",
    "search_web": "web_search",
    "google_search": "web_search",
    "bing_search": "web_search",
    "internet_search": "web_search",
    "browser_get_page_content": "browser_get_page_content",
}


def _tool_arg_error(name: str, tool: Any, args: dict[str, Any]) -> str | None:
    if name in _INCOMPLETE_WRITE_TOOLS and args.get("_incomplete"):
        return (
            f"ERROR: {name} arguments were cut off before the payload was complete. "
            "Retry the same call with the complete arguments."
        )
    if tool is None:
        return None
    prepared = prepare_tool_args(name, tool.handler, args)
    missing = missing_required_args(tool.handler, prepared)
    if not missing:
        return None
    keys = sorted(str(k) for k in args if not str(k).startswith("_")) or ["(none)"]
    return (
        f"ERROR: {name} missing required argument(s): {', '.join(missing)}. "
        f"Received keys: {', '.join(keys)}. "
        "Call the same tool again with every required field. "
        "Do not continue as if the file was written or read."
    )


def drop_unready_tool_calls(
    registry: Any, tool_calls: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop calls that arrived as {} before required fields were generated."""
    ready: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str((fn or {}).get("name") or "")
        args = parse_tool_args((fn or {}).get("arguments") or "{}")
        tool = registry.get(name) if name else None
        keys = [k for k in (args or {}) if not str(k).startswith("_")] if isinstance(args, dict) else []
        if tool is not None and not keys and missing_required_args(tool.handler, args if isinstance(args, dict) else {}):
            dropped.append(tc)
            continue
        ready.append(tc)
    return ready, dropped


def _alias_tool_name(name: str) -> str:
    raw = (name or "").strip()
    return _TOOL_ALIASES.get(raw, raw)


def _is_blocker_result(content: str) -> bool:
    text = str(content or "")
    return text.startswith("ERROR") or text.startswith("WARNING")


def _annotate_file_blocker(name: str, content: str) -> str:
    if name not in _FILE_TOOLS or not _is_blocker_result(content):
        return content
    if "Do not continue as if this succeeded" in content:
        return content
    low = content.lower()
    payload_problem = name in _INCOMPLETE_WRITE_TOOLS and any(
        mark in low for mark in ("cut off", "missing required", "incomplete", "payload")
    )
    path_missing = any(
        mark in low for mark in ("not found", "no such file", "cannot find", "找不到")
    )
    if payload_problem:
        suffix = _PAYLOAD_RETRY
    elif path_missing or name == "delete_file":
        suffix = _MISSING_PATH_RETRY
    else:
        suffix = _BLOCKER_RETRY
    return f"{content}{suffix}"


def file_tool_results_blocked(results: list[dict[str, Any]]) -> bool:
    for item in results:
        name = str(item.get("name") or "")
        if name in _FILE_TOOLS and _is_blocker_result(str(item.get("content") or "")):
            return True
    return False


class AgentExecuteMixin:
    def _emit_browser_open_preview(self, args: dict[str, Any]) -> bool:
        """Reveal the in-app Browser panel before Playwright/CDP finishes."""
        raw = str((args or {}).get("url") or "")
        if not raw.strip():
            return False
        try:
            from pathlib import Path

            from ..services.browser_sandbox import coerce_navigate_target

            settings = getattr(self, "settings", None)
            raw_ws = getattr(settings, "workspace", None) if settings is not None else None
            ws = Path(raw_ws) if raw_ws else None
            preview = coerce_navigate_target(raw, workspace=ws)
        except Exception:
            preview = ""
        if preview:
            self._emit("browser_open", {"url": preview, "tool": "browser_navigate", "session_id": getattr(self, "session_id", None) or ""})
            return True
        return False

    def _execute_one(self, tc: dict[str, Any]) -> dict[str, Any]:
        fn = tc.get("function") or {}
        raw_name = fn.get("name") or ""
        name = _alias_tool_name(raw_name)
        if getattr(self, "_block_delegate", False) and name in {"delegate_task", "delegate_dialogue", "ask_agent"}:
            call_id = str(tc.get("id") or new_id("call"))
            content = (
                "ERROR: this turn is inside a group collaboration harness. "
                "Finish the assigned part yourself. Do not spawn or ask another agent."
            )
            self._emit(
                "tool_start",
                {
                    "name": name,
                    "args": {},
                    "call_id": call_id,
                    "needs_approval": False,
                    "summary": content,
                    "message": f"→ {name}",
                },
            )
            self._emit(
                "tool_end",
                {
                    "name": name,
                    "call_id": call_id,
                    "ok": False,
                    "preview": content[:400],
                    "result": content,
                    "message": f"← {name} blocked (group harness)",
                },
            )
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
                "ts": time.time(),
            }
        args = parse_tool_args(fn.get("arguments") or "{}")
        tool = self.registry.get(name)
        call_id = str(tc.get("id") or new_id("call"))
        workspace = getattr(getattr(self, "settings", None), "workspace", None)
        arg_error = _tool_arg_error(name, tool, args)
        previewed = False
        scope = approval_scope(name, args, workspace)
        needs_ok = (not arg_error) and approval_required(
            name, tool, args=args, workspace=workspace
        )
        summary = summarize_tool_call(name, args, workspace=workspace)
        mutating = bool(needs_ok) or name in ("delegate_task", "delegate_dialogue")
        # Read-only shell exploration (dir listing, git status, cat, ...) is
        # exploration, not a mutation — allow it during plan-prep the same
        # way list_dir/read_file already are. Still goes through the normal
        # approval gate below; only the "gathering info" block is skipped.
        if (
            mutating
            and name == "run_shell"
            and is_readonly_shell_command(str(args.get("command") or ""))
        ):
            mutating = False
        if mutating and not getattr(self, "_allow_mutating_tools", True):
            content = (
                f"ERROR: still gathering information for the plan; {name} needs "
                "approval and mutating tools are unavailable during this gathering "
                "step. Use read_file / list_dir / search_text / codebase_* / "
                "a read-only run_shell command (e.g. listing/inspecting, no writes), "
                "or ask_user — then stop with a short briefing."
            )
            self._emit(
                "tool_start",
                {
                    "name": name,
                    "args": args,
                    "call_id": call_id,
                    "needs_approval": False,
                    "summary": summary,
                    "message": f"→ {name}",
                },
            )
            self._emit(
                "tool_end",
                {
                    "name": name,
                    "args": args,
                    "call_id": call_id,
                    "ok": False,
                    "preview": content[:400],
                    "result": content,
                    "message": f"← {name} blocked (plan prep)",
                },
            )
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": content,
                "ts": time.time(),
            }
        preapproved = bool(needs_ok and self.approval.is_preapproved(scope))
        self._emit(
            "tool_start",
            {
                "name": name,
                "args": args,
                "call_id": call_id,
                "needs_approval": needs_ok and not preapproved,
                "summary": summary,
                "message": f"→ {name}",
            },
        )

        if needs_ok:
            if preapproved:
                self._emit(
                    "approval_auto",
                    {
                        "call_id": call_id,
                        "name": name,
                        "summary": summary,
                        "message": f"本轮已放行：{summary}",
                    },
                )
            else:
                with self.approval.serialize_ui():
                    if self.cancelled():
                        content = f"ERROR: cancelled — {summary}"
                        self.guard.after(name, args, content)
                        self._emit(
                            "tool_end",
                            {
                                "name": name,
                                "args": args,
                                "call_id": call_id,
                                "ok": False,
                                "preview": content[:400],
                                "result": content,
                                "message": f"← {name} cancelled",
                            },
                        )
                        return {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": content,
                            "ts": time.time(),
                        }
                    approval_id = new_id("appr")
                    self._emit(
                        "approval_request",
                        {
                            "approval_id": approval_id,
                            "call_id": call_id,
                            "name": name,
                            "args": args,
                            "summary": summary,
                            "message": f"等待确认：{summary}",
                        },
                    )
                    approved = self.approval.request(
                        approval_id, name, args, summary, scope=scope
                    )
                    self._emit(
                        "approval_resolved",
                        {
                            "approval_id": approval_id,
                            "call_id": call_id,
                            "name": name,
                            "approved": approved,
                            "message": "已批准" if approved else "已拒绝或超时",
                        },
                    )
                if not approved:
                    content = f"ERROR: user rejected or approval timed out — {summary}"
                    self.guard.after(name, args, content)
                    self._emit(
                        "tool_end",
                        {
                            "name": name,
                            "args": args,
                            "call_id": call_id,
                            "ok": False,
                            "preview": content[:400],
                            "result": content,
                            "message": f"← {name} rejected",
                        },
                    )
                    return {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": content,
                        "ts": time.time(),
                    }
            self._emit(
                "tool_start",
                {
                    "name": name,
                    "args": args,
                    "call_id": call_id,
                    "needs_approval": False,
                    "summary": summary,
                    "message": f"→ {name}",
                },
            )

        blocked = self.guard.before(name, args)
        if blocked:
            content = blocked
        elif not tool:
            known = ", ".join(self.registry.names()[:18])
            from ..core.hostinfo import network_available

            net_hint = (
                "Use web_search(query=...) for the public internet, "
                "search_text for workspace grep, or browser_navigate for a URL. "
                if network_available()
                else (
                    "This host is offline — do not call web_search. "
                    "Use search_text for workspace grep or read_file/list_dir. "
                )
            )
            content = (
                f"ERROR: unknown tool {raw_name or name}. "
                f"{net_hint}"
                f"Known tools include: {known}."
            )
        else:
            from ..services import fs_undo

            actor = "sub" if self.is_subagent else "main"
            if self.is_subagent:
                actor_label = (self.goal or "").strip().split("\n")[0][:80] or (
                    self.role or "sub"
                )
            else:
                actor_label = "main"
            fs_undo.set_mutation_meta(
                actor=actor,
                actor_label=actor_label,
                why=summary,
                tool=name,
                call_id=call_id,
            )
            try:
                if arg_error:
                    content = arg_error
                    previewed = False
                else:
                    previewed = False
                    if name == "browser_navigate":
                        previewed = bool(self._emit_browser_open_preview(args))
                    served, adjusted = self.guard.dedup_read(name, args)
                    if served is not None:
                        # File-space hit: identical lines are already in context.
                        content = served
                    else:
                        run_args = adjusted if adjusted is not None else args
                        if adjusted is not None:
                            args = adjusted  # tool_end/UI show the real served range
                        content = tool.handler(
                            **prepare_tool_args(name, tool.handler, run_args)
                        )
            except TypeError as exc:
                content = (
                    f"ERROR: bad arguments for {name}: {exc}. "
                    "Retry with explicit path/content fields; do not skip ahead."
                )
                previewed = False
            except Exception as exc:  # noqa: BLE001
                content = f"ERROR: {name} failed: {exc}"
                previewed = False
            finally:
                fs_undo.clear_mutation_meta()

        # Clip BEFORE recording coverage: the guard must only remember ranges
        # the model actually received. Recording the pre-clip trailer made the
        # dedupe ledger claim a whole file was in context when the tail had
        # been truncated away — the model then probed past EOF in a loop.
        if len(content) > self.settings.tool_result_cap:
            from ..core.textutil import safe_clip

            content = safe_clip(
                content, self.settings.tool_result_cap, ellipsis="\n…[truncated]"
            )
        if not blocked:
            self.guard.after(name, args, content)
        content = _annotate_file_blocker(name, content)

        if (
            name.startswith("browser_")
            and content
            and not str(content).lstrip().startswith("ERROR")
            and not (name == "browser_navigate" and previewed)
        ):
            # Only browser_navigate should drive loadURL. Other browser_* tools
            # used to re-emit the page URL here, which made the UI/Electron
            # navigate again — often rewinding a search-results / article page
            # back to the site homepage mid-task. Reveal the panel only.
            nav_url = ""
            if name == "browser_navigate":
                nav_url = str(args.get("url") or "")
                if not nav_url:
                    try:
                        payload = (
                            json.loads(content)
                            if str(content).lstrip().startswith("{")
                            else {}
                        )
                        if isinstance(payload, dict) and payload.get("url"):
                            nav_url = str(payload.get("url") or "")
                    except Exception:
                        pass
            self._emit(
                "browser_open",
                {
                    "url": nav_url,
                    "tool": name,
                    "session_id": getattr(self, "session_id", None) or "",
                    # Non-navigate browser_* must only reveal the panel. Emitting
                    # an empty/stale url made the UI loadURL the first homepage
                    # again after search/results had already loaded.
                    "reveal_only": not bool(nav_url),
                },
            )

        if name in {
            "run_shell",
            "shell_job_list",
            "shell_job_log",
            "shell_job_wait",
            "shell_job_stop",
        } and content and not str(content).lstrip().startswith("ERROR"):
            import re as _re

            mid = _re.search(r"job_id=([A-Za-z0-9_]+)", str(content))
            st = _re.search(r"status=([a-z]+)", str(content))
            self._emit(
                "shell_job",
                {
                    "job_id": mid.group(1) if mid else "",
                    "status": st.group(1) if st else "running",
                    "command": str(args.get("command") or ""),
                    "tool": name,
                },
            )

        self._ingest_workspace_fact(name, args, content)
        self._emit_coherence_tool_events(name, args, content)

        blocked_result = _is_blocker_result(content)
        if not blocked_result:
            if name in ("write_file", "str_replace", "delete_file"):
                self._turn_mutated = True
            if name == "verify_run":
                self._turn_verified = True
        from ..core.textutil import safe_clip

        self._emit(
            "tool_end",
            {
                "name": name,
                "args": args,
                "call_id": call_id,
                "ok": not blocked_result,
                "preview": safe_clip(content, 400),
                "result": safe_clip(content, 12_000, ellipsis="\n…[truncated]")
                if len(content) > 12_000
                else content,
                "message": f"← {name} ({len(content)} chars)",
            },
        )
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": content,
            "ts": time.time(),
        }

    def _emit_coherence_tool_events(
        self, name: str, args: dict[str, Any], content: str
    ) -> None:
        """Surface Anti-Piling / verify signals to the UI."""
        if self.is_subagent:
            return
        if name == "codebase_find_similar":
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = {}
            matches = data.get("matches") if isinstance(data, dict) else None
            top = []
            if isinstance(matches, list):
                for m in matches[:5]:
                    if isinstance(m, dict) and m.get("path"):
                        top.append(
                            {
                                "path": str(m.get("path")),
                                "score": m.get("score"),
                                "symbols": m.get("symbols") or [],
                            }
                        )
            self._emit(
                "coherence_align",
                {
                    "query": str(args.get("query") or data.get("query") or ""),
                    "match_count": int(data.get("match_count") or len(top)),
                    "matches": top,
                    "message": f"对齐检索：{data.get('match_count', len(top))} 个候选",
                },
            )
        elif name == "coherence_checklist":
            self._emit(
                "coherence_pile",
                {
                    "status": "checklist_issued",
                    "message": "已下发检堆砌清单，请对照证据作答",
                },
            )
        elif name == "verify_run":
            passed = content.startswith("VERIFY PASS")
            self._emit(
                "verify_result",
                {
                    "ok": passed,
                    "command": str(args.get("command") or ""),
                    "preview": content[:500],
                    "message": "验收通过" if passed else "验收未通过",
                },
            )

    def _execute_tools(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for batch in plan_parallel_batches(tool_calls, self.registry):
            if len(batch) == 1:
                results.append(self._execute_one(batch[0]))
                continue
            self._emit("parallel_batch", {"size": len(batch)})
            ordered: list[dict[str, Any] | None] = [None] * len(batch)
            with ThreadPoolExecutor(max_workers=min(8, len(batch))) as pool:
                futs = {
                    pool.submit(
                        contextvars.copy_context().run, self._execute_one, tc
                    ): i
                    for i, tc in enumerate(batch)
                }
                for fut in as_completed(futs):
                    ordered[futs[fut]] = fut.result()
            results.extend(r for r in ordered if r is not None)
        return results

    def _maybe_auto_verify(self) -> str:
        """Run a detected test/lint once after file mutations if the model skipped it."""
        if self.is_subagent or getattr(self, "_turn_verified", False):
            return ""
        if not getattr(self, "_turn_mutated", False):
            return ""
        from ..core.events import new_id
        from ..core.textutil import safe_clip
        from ..services.verify_detect import detect_verify_command

        cmd = detect_verify_command(self.settings.workspace)
        if not cmd:
            return ""
        if not self.settings.allow_shell:
            return (
                f"\n\n---\n未自动验收：shell 未开启。建议本地运行：`{cmd}`"
            )
        tool = self.registry.get("verify_run")
        if not tool:
            return ""
        args = {"command": cmd}
        summary = summarize_tool_call("verify_run", args)
        call_id = new_id("call")
        self._emit(
            "tool_start",
            {
                "name": "verify_run",
                "args": args,
                "call_id": call_id,
                "needs_approval": False,
                "summary": summary,
                "message": "→ verify_run (auto)",
            },
        )
        try:
            content = str(tool.handler(command=cmd) or "")
        except Exception as exc:  # noqa: BLE001
            content = f"ERROR: verify_run failed: {exc}"
        self._turn_verified = True
        passed = content.startswith("VERIFY PASS")
        self._emit(
            "tool_end",
            {
                "name": "verify_run",
                "args": args,
                "call_id": call_id,
                "ok": passed,
                "preview": safe_clip(content, 400),
                "result": safe_clip(content, 12_000, ellipsis="\n…[truncated]")
                if len(content) > 12_000
                else content,
                "message": f"← verify_run ({len(content)} chars)",
            },
        )
        self._emit(
            "verify_result",
            {
                "ok": passed,
                "command": cmd,
                "preview": content[:500],
                "auto": True,
                "message": "验收通过" if passed else "验收未通过",
            },
        )
        head = "\n".join(content.strip().splitlines()[:8])[:800]
        status = "通过" if passed else "未通过"
        return f"\n\n---\n自动验收 `{cmd}`：{status}\n{head}"
