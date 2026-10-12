"History compression, context budget, and tool-result squeezing."

from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Callable, Optional

from .ask import (
    AskGate,
    MIN_ASK_OPTIONS,
    build_ask_options,
    normalize_option_labels,
    should_keep_as_markdown,
    try_parse_inline_ask,
)
from ..core.config import Settings, get_settings
from .context import (
    COMPACTION_MARK,
    cheap_compact,
    context_budget_tokens,
    current_turn_start,
    debug_dump_budget,
    ensure_fit,
    messages_tokens,
    park_overflow,
    schemas_tokens,
    squeeze_fresh_tool_results,
)
from ..core.events import EventBus, emit, new_id
from ..core.guardrails import Guardrails, repeating_tool_sequence, tool_call_signature
from .llm import LLM, parse_tool_args, streaming_text_looped
from .plan import (
    PLAN_PREP_HINT,
    PLAN_PREP_MAX_ROUNDS,
    PLAN_PREP_RETRY_HINT,
    PlanGate,
    apply_confirmed_plan,
    digest_plan_prep,
    extract_plan_goal,
    format_plan_markdown,
    generate_plan,
    goal_ready_to_plan,
    is_multi_agent_request,
    needs_plan,
    snapshot_plan_tasks,
)
from .prompts import (
    apply_response_language,
    build_system_prompt,
    language_directive,
    language_pin_messages,
)
from .coherence import inject_contract_into_goal, shape_contract_from_plan
from .review import run_review
from ..services.skills import Skill, invalidate_skills_cache, load_skills
from .tool_registry import drop_delegate_schemas, filter_readonly_schemas, skill_tool_name
from .tools import ToolRegistry, build_registry
from .tools.support import _skill_as_tool
from .agent_execute import drop_unready_tool_calls, file_tool_results_blocked

class AgentCompressMixin:
    def _assistant_tool_args_incomplete(self, msg: dict[str, Any]) -> bool:
        """True when write/edit payloads were truncated to a path-only placeholder."""
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            args = parse_tool_args(str((fn or {}).get("arguments") or "{}"))
            if not isinstance(args, dict):
                continue
            if args.get("_incomplete"):
                return True
            if name in {"write_file", "str_replace"}:
                body = str(args.get("content") or args.get("new_string") or args.get("new_str") or "").strip()
                if not body:
                    return True
        return False

    @staticmethod
    def _tool_call_sig_set(msg: dict[str, Any]) -> frozenset[str]:
        """Signatures for complete tool calls only (truncated writes are path-only and would false-trip)."""
        sigs: list[str] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            if not name:
                continue
            args = parse_tool_args(str((fn or {}).get("arguments") or "{}"))
            if isinstance(args, dict) and args.get("_incomplete"):
                continue
            if name in {"write_file", "str_replace"} and isinstance(args, dict):
                body = str(args.get("content") or args.get("new_string") or args.get("new_str") or "").strip()
                if not body:
                    continue
            sigs.append(tool_call_signature(name, args if isinstance(args, dict) else {}))
        return frozenset(sigs)

    @staticmethod
    def _live_browser_repeat(msg: dict[str, Any]) -> bool:
        """Same args on a live page are a refresh, not a stuck file/search call."""
        names: list[str] = []
        for tc in (msg or {}).get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            if name:
                names.append(name)
        return bool(names) and all(name.startswith("browser_") for name in names)

    @staticmethod
    def _browser_repeat_after_failure(agent: Any, prev: dict[str, Any]) -> bool:
        """True when the previous identical browser call failed, or its result is gone."""
        from ..core.guardrails import looks_failed

        seen = False
        saw_browser = False
        for m in getattr(agent, "messages", None) or []:
            if m is prev:
                seen = True
                continue
            if not seen or m.get("role") != "tool":
                continue
            name = str(m.get("name") or "")
            if not name.startswith("browser_"):
                continue
            saw_browser = True
            if looks_failed(str(m.get("content") or "")):
                return True
        return not saw_browser

    @staticmethod
    def _prior_needs_human(text: str) -> bool:
        low = (text or "").lower()
        return any(
            key in low
            for key in ("captcha", "needs_human", "challenge", "安全验证", "人机验证")
        )

    def _find_recent_tool_content(self, name: str, *, max_chars: int = 1200) -> str:
        """Best-effort recall of a prior tool payload still sitting in messages."""
        name = str(name or "")
        for m in reversed(self.messages):
            if m.get("role") != "tool":
                continue
            if name and str(m.get("name") or "") not in {"", name}:
                continue
            text = str(m.get("content") or "").strip()
            if not text:
                continue
            if text.startswith("[cleared tool result:"):
                continue
            if text.startswith("[already in context]"):
                continue
            if len(text) > max_chars:
                return text[:max_chars].rstrip() + "\n…"
            return text
        return ""

    def _prior_results_for_tool_calls(self, assistant: dict[str, Any]) -> str:
        """Rebuild substance for repeated tool calls from digests / history."""
        parts: list[str] = []
        seen: set[str] = set()
        for tc in assistant.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str((fn or {}).get("name") or "")
            if not name:
                continue
            args = parse_tool_args(str((fn or {}).get("arguments") or "{}"))
            if not isinstance(args, dict):
                args = {}
            sig = tool_call_signature(name, args)
            if sig in seen:
                continue
            seen.add(sig)
            digest = ""
            try:
                digest = str(self.guard.digest_for_signature(sig) or "").strip()
            except Exception:
                digest = ""
            if not digest:
                digest = self._find_recent_tool_content(name)
            if digest:
                parts.append(f"### {name}\n{digest}")
            else:
                parts.append(
                    f"### {name}\n（未找到可回放的正文；请换参数继续，或基于你已得出的结论推进。）"
                )
        return "\n\n".join(parts)

    def _collapse_repeated_assistant(self, assistant: dict[str, Any]) -> bool:
        """Drop copy-pasted turns. True = stop the loop (identical tool replay)."""

        def _norm(text: str) -> str:
            return re.sub(r"\s+", " ", (text or "").strip())

        prev: Optional[dict[str, Any]] = None
        for m in reversed(self.messages[:-1]):
            if m.get("role") == "assistant":
                prev = m
                break
        if not prev:
            return False
        text = _norm(str(assistant.get("content") or ""))
        prev_text = _norm(str(prev.get("content") or ""))
        text_repeats = bool(text) and len(text) >= 24 and (
            text == prev_text
            or (len(prev_text) >= 40 and prev_text in text)
            or (len(text) >= 40 and text in prev_text)
        )
        has_tools = bool(assistant.get("tool_calls"))
        fp = self._tool_call_sig_set(assistant)
        same_tools = bool(fp) and fp == self._tool_call_sig_set(prev)
        # A live page can need the same control again after a successful action.
        # A failed identical call is still a stuck replay.
        browser_refresh = (
            same_tools
            and AgentCompressMixin._live_browser_repeat(assistant)
            and not AgentCompressMixin._browser_repeat_after_failure(self, prev)
        )
        if text_repeats:
            assistant["content"] = ""
            if browser_refresh:
                return False
            if same_tools or not has_tools:
                assistant["tool_calls"] = []
                return True
            return False
        if browser_refresh:
            return False
        if same_tools:
            assistant["tool_calls"] = []
            return True
        return False

    def _compress_msg_limit(self, schemas: Optional[list[dict[str, Any]]] = None) -> int:
        schemas = schemas if schemas is not None else self.registry.schemas()
        limit = self.settings.context_limit
        gen_reserve = int(getattr(self.settings, "max_tokens", 0) or 0)
        if self.is_subagent and gen_reserve <= 0:
            gen_reserve = 2048
        return max(4000, limit - schemas_tokens(schemas) - 256 - gen_reserve)

    def _sync_read_coverage(self) -> None:
        """Drop read dedupe for lines whose bytes left the window."""
        try:
            self.guard.reconcile_read_coverage(self.messages)
        except Exception:
            pass

    def _archive_dropped(self, meta: dict[str, Any]) -> None:
        """Keep whole messages that compaction removed, for display/persistence."""
        dropped = meta.get("dropped_messages") or []
        if dropped:
            self._archived_messages.extend(dropped)
            # Bytes left the window — dedup must not tell the model that an
            # earlier read is "already in context" when it may be gone.
            self._sync_read_coverage()
        if dropped or meta.get("compressed"):
            self._write_recoverable_history()

    def _write_recoverable_history(self) -> None:
        """Cursor-style: compacted history lives as a workspace file the agent can search."""
        try:
            ws = Path(self.settings.workspace)
            out_dir = ws / ".sidekick" / "context"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "history.md"
            lines = [
                "# Session history (recover details after compaction)",
                "",
                "This file is **not** in the live model window. If the compaction summary",
                "omitted something, `search_text` or `read_file` this path, then re-read",
                "the original workspace files — do not guess.",
                "",
            ]
            for m in self.full_transcript():
                role = m.get("role")
                if role == "system":
                    continue
                text = str(m.get("content") or "").replace("\r\n", "\n")
                if role == "tool":
                    name = str(m.get("name") or "tool")
                    if len(text) > 500:
                        text = text[:500] + "\n…"
                    lines.append(f"### tool {name}")
                    lines.append(text)
                    lines.append("")
                elif role == "user":
                    if text.startswith(COMPACTION_MARK):
                        continue
                    clip = text if len(text) <= 2000 else text[:2000] + "…"
                    lines.append(f"### user")
                    lines.append(clip)
                    lines.append("")
                elif role == "assistant":
                    clip = text if len(text) <= 1500 else text[:1500] + "…"
                    if clip.strip():
                        lines.append("### assistant")
                        lines.append(clip)
                        lines.append("")
            path.write_text("\n".join(lines), encoding="utf-8")
            rel = ".sidekick/context/history.md"
            for m in self.messages:
                if m.get("role") == "user" and str(m.get("content") or "").startswith(COMPACTION_MARK):
                    body = str(m.get("content") or "")
                    if rel not in body:
                        m["content"] = (
                            body.rstrip()
                            + f"\n\n## Recover\nEarlier turns: `{rel}` — "
                            "search_text/read_file if this summary omitted a detail."
                        )
                    break
        except Exception as exc:  # noqa: BLE001
            from ..core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), "write recoverable history failed", exc)

    def full_transcript(self) -> list[dict[str, Any]]:
        """Complete chronological history for display/persistence.

        ``self.messages`` is the live LLM working set and may have been
        compacted (old turns folded into a synthetic summary). This stitches
        the archived originals back in and drops the summary stub, so the UI
        and disk history never lose turns to context compression.
        """
        if not self.messages:
            return list(self._archived_messages)
        system = self.messages[0] if self.messages[0].get("role") == "system" else None
        body = self.messages[1:] if system else self.messages
        display_body = [
            m for m in body if not str(m.get("content") or "").startswith(COMPACTION_MARK)
        ]
        out: list[dict[str, Any]] = []
        if system:
            out.append(system)
        out.extend(self._archived_messages)
        out.extend(display_body)
        from .llm import align_tool_chains

        # Archive + live can contain the same tool call twice after a compact
        # merge. Persisting both makes the next request 400.
        return align_tool_chains(out)

    def _ensure_mids(self) -> None:
        """Stamp messages so a later compact can tell copies from new turns."""
        seq = int(getattr(self, "_mid_seq", 0) or 0)
        for bucket in (getattr(self, "_archived_messages", None) or [], self.messages):
            for m in bucket:
                if not isinstance(m, dict):
                    continue
                mid = m.get("_mid")
                if not isinstance(mid, int) or mid <= 0:
                    seq += 1
                    m["_mid"] = seq
                elif mid > seq:
                    seq = mid
        self._mid_seq = seq

    def _apply_ready_compress(self) -> None:
        """Swap in a finished background compaction without blocking the loop."""
        job = self._compress_job
        if not job:
            return
        done: threading.Event = job["done"]
        if not done.is_set():
            return
        self._compress_job = None
        rebuilt, meta = job["box"].get("ok") or (None, {})
        if not rebuilt:
            return
        # Match on a stable id, not the Python object id. Compaction copies
        # messages; treating those copies as new appended the same tool
        # results a second time and the provider rejected the chain.
        self._ensure_mids()
        snap_mids = {mid for mid in (job.get("snap_mids") or []) if isinstance(mid, int)}
        have = {
            m.get("_mid")
            for m in rebuilt
            if isinstance(m, dict) and isinstance(m.get("_mid"), int)
        }
        extras = [
            m
            for m in self.messages
            if not (
                isinstance(m, dict)
                and isinstance(m.get("_mid"), int)
                and (m.get("_mid") in snap_mids or m.get("_mid") in have)
            )
        ]
        self.messages = list(rebuilt) + extras
        self._archive_dropped(meta)
        if meta.get("compressed"):
            self._last_compressed = True
        after = context_budget_tokens(self.messages, self.registry.schemas())
        self._emit(
            "compress",
            {
                "before": job.get("before") or 0,
                "after": after,
                "limit": self.settings.context_limit,
                "meta": meta,
                "phase": "done",
                "background": True,
                "blocking": False,
                "message": f"上下文已在后台整理 {job.get('before') or 0}→{after}",
            },
        )

    def _run_ensure_fit(self, snapshot: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return ensure_fit(
            snapshot,
            context_limit=self._compress_msg_limit(),
            keep_recent_tokens=self.settings.keep_recent_tokens,
            trigger_ratio=self.settings.compress_trigger_ratio,
            max_attempts=1,
            llm=self.compress_llm,
        )

    def _start_background_compress(self, before: int) -> None:
        if self._compress_job and not self._compress_job["done"].is_set():
            return
        self._ensure_mids()
        snapshot = copy.deepcopy(self.messages)
        snap_mids = [m.get("_mid") for m in self.messages if isinstance(m, dict)]
        box: dict[str, Any] = {}
        done = threading.Event()
        job = {"done": done, "box": box, "snap_mids": snap_mids, "before": before}
        self._compress_job = job

        def work() -> None:
            try:
                box["ok"] = self._run_ensure_fit(snapshot)
            except Exception as exc:  # noqa: BLE001
                box["ok"] = (None, {"error": str(exc)})
            finally:
                done.set()

        threading.Thread(target=work, name="sidekick-compress", daemon=True).start()
        self._emit(
            "compress_start",
            {
                "before": before,
                "limit": self.settings.context_limit,
                "attempt": 1,
                "max_attempts": 1,
                "phase": "background",
                "background": True,
                "blocking": False,
                "message": "后台整理上下文（对话继续）…",
            },
        )

    def _emit_context_usage(self, schemas: list[dict[str, Any]] | None = None) -> int:
        """Publish the *current* window usage; returns the estimate."""
        schemas = schemas if schemas is not None else self.registry.schemas()
        tokens = context_budget_tokens(self.messages, schemas)
        limit = self.settings.context_limit
        try:
            from ..core.config import infer_context_limit as _infer_window
            window_limit = int(_infer_window(getattr(self.settings, "model", "") or "") or 0)
        except Exception:
            window_limit = 0
        self._emit(
            "context_usage",
            {
                "tokens": tokens,
                "limit": limit,
                "window_limit": window_limit or None,
                "ratio": round(tokens / max(1, limit), 4),
                "messages_tokens": messages_tokens(self.messages),
                "schemas_tokens": schemas_tokens(schemas),
            },
        )
        return tokens

    def _squeeze_tool_batch(self, start: int) -> None:
        """Keep a fresh batch of tool results from overflowing the window.

        The LLM-backed compressor only runs at the top of the next iteration;
        without this, one parallel batch of large reads could report
        "77k / 64k" to the UI and 400 on the provider before anything reacts.
        """
        schemas = self.registry.schemas()
        limit = self.settings.context_limit
        # Leave room for the model's reply and the schemas we send every turn.
        gen_reserve = int(getattr(self.settings, "max_tokens", 0) or 0) or 2048
        target = int(limit * 0.85) - schemas_tokens(schemas) - 256 - gen_reserve
        target = max(2000, target)
        if messages_tokens(self.messages) <= target:
            return
        changed = False
        # Old history is what overflows a restored chat. Parking it keeps the
        # reads from this batch intact — trimming those reads is what makes
        # the model fetch the same lines again.
        if 0 < start < len(self.messages):
            fresh = self.messages[start:]
            prefix_budget = max(3000, target // 2)
            keep = int(self.settings.keep_recent_tokens or 0) or prefix_budget
            live, archived = park_overflow(
                self.messages[:start],
                context_limit=prefix_budget,
                keep_recent_tokens=min(keep, prefix_budget),
            )
            if archived or messages_tokens(live) < messages_tokens(self.messages[:start]):
                if archived:
                    self._archived_messages.extend(archived)
                self.messages = live + fresh
                start = len(live)
                changed = True
        squeezed, did = squeeze_fresh_tool_results(
            self.messages, start, target_tokens=target
        )
        if did:
            self.messages = squeezed
            changed = True
        if changed:
            self._sync_read_coverage()
            self._last_compressed = True
            self._emit_context_usage(schemas)

    def _maybe_compress(self) -> None:
        schemas = self.registry.schemas()
        self._apply_ready_compress()
        before = context_budget_tokens(self.messages, schemas)
        limit = self.settings.context_limit
        trigger = int(limit * self.settings.compress_trigger_ratio)
        # Instant shrink of PREVIOUS turns' fat tool dumps — never waits on an
        # LLM, and never touches the active turn (clearing a read_file result
        # the agent is still using forces it into a re-read loop).
        if before >= int(limit * 0.55):
            compacted, cheap_did = cheap_compact(
                self.messages, protect_from=current_turn_start(self.messages)
            )
            if cheap_did:
                self.messages = compacted
                self._sync_read_coverage()
                self._last_compressed = True
                before = context_budget_tokens(self.messages, schemas)
        if before < trigger:
            self._emit_context_usage(schemas)
            return

        emergency = before >= int(limit * 0.90)
        if not emergency:
            self._emit_context_usage(schemas)
            self._start_background_compress(before)
            return

        # Over the hard line: one sync pass so the next provider call cannot 400.
        job = self._compress_job
        if job and not job["done"].is_set():
            job["done"].wait(timeout=8.0)
            self._apply_ready_compress()
            before = context_budget_tokens(self.messages, schemas)
            if before < trigger:
                self._emit_context_usage(schemas)
                return
        self._compress_job = None

        self._emit(
            "compress_start",
            {
                "before": before,
                "limit": limit,
                "attempt": 1,
                "max_attempts": 1,
                "phase": "emergency",
                "blocking": True,
                "message": "上下文接近上限，正在快速整理…",
            },
        )
        self.messages, meta = self._run_ensure_fit(self.messages)
        self._archive_dropped(meta)
        after = context_budget_tokens(self.messages, schemas)
        if meta.get("compressed"):
            self._last_compressed = True
        self._emit(
            "compress",
            {
                "before": before,
                "after": after,
                "limit": limit,
                "meta": meta,
                "phase": "done",
                "blocking": True,
                "message": f"上下文已整理 {before}→{after}",
            },
        )
        self._emit(
            "context_usage",
            {
                "tokens": after,
                "limit": limit,
                "ratio": round(after / max(1, limit), 4),
                "messages_tokens": messages_tokens(self.messages),
                "schemas_tokens": schemas_tokens(schemas),
            },
        )

