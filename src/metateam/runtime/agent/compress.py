"""Agent mixin: compress."""
from __future__ import annotations

from typing import Any, Optional

from . import helpers as _h
from .helpers import *  # noqa: F403

class AgentCompressMixin:

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
            from ...core.logutil import get_logger, log_exception

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
        from ..llm import align_tool_chains

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
            self._after_compress_continue()
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
            from ...core.config import infer_context_limit as _infer_window
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
            # Keep current-turn mutations visible: never silently archive them.
            turn_start = current_turn_start(self.messages)
            live, archived = park_overflow(
                self.messages[:start],
                context_limit=prefix_budget,
                keep_recent_tokens=min(keep, prefix_budget),
                protect_from=turn_start if turn_start < start else None,
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

    def _after_compress_continue(self) -> None:
        """Keep the agent loop on the same goal after context compaction.

        Compaction must never look like a stop signal. Repair dangling
        tool_calls (providers 400 otherwise → empty UI round) and inject an
        internal continue nudge so the model does not apologize about
        truncation and exit.
        """
        self.messages = repair_incomplete_tool_chains(self.messages)
        coalesce_internal_nudge(
            self.messages,
            kind="post_compress",
            content=_POST_COMPRESS_CONTINUE_NUDGE,
            role="system",
        )

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
        self.messages = repair_incomplete_tool_chains(self.messages)
        self._archive_dropped(meta)
        after = context_budget_tokens(self.messages, schemas)
        if meta.get("compressed"):
            self._last_compressed = True
            self._after_compress_continue()
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
