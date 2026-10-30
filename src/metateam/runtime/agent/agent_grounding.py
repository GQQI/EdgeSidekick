"""Workspace grounding and per-turn coherence policy."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..coherence import format_turn_policy_block, merge_policy_into_system, policy_for_turn


def minimal_grounding(workspace: Path) -> str:
    """Cheap ground-truth block for leaf subagents (no index, no history).

    Leaf workers normally run with zero environment awareness — they invent
    src/ layouts or miss files that exist. This pins the real workspace root
    plus a small top-level listing so the first read/search lands.
    """
    ws = Path(workspace).resolve()
    shown: list[str] = []
    try:
        entries = sorted(ws.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for e in entries[:20]:
            if e.name.startswith("."):
                continue
            shown.append(f"{'dir' if e.is_dir() else 'file'}:{e.name}")
    except OSError:
        pass
    lines = [
        "## Workspace ground truth (authoritative)",
        f"WORKSPACE: {ws}",
        "All tool paths are WORKSPACE-relative (forward slashes). "
        "Use only paths from this list or confirmed by a tool — never invent layouts.",
    ]
    if shown:
        lines.append("Top-level now: " + ", ".join(shown))
    else:
        lines.append("Top-level now: (empty)")
    return "\n".join(lines)


class AgentGroundingMixin:
    _FS_MUTATION_TOOLS = frozenset({"write_file", "str_replace", "delete_file"})

    def _ingest_workspace_fact(self, name: str, args: dict[str, Any], content: str) -> None:
        """Pin layout discoveries and successful FS mutations for the model.

        Layout notes stay small. Mutations are a durable ledger (path + action
        + success) refreshed into the system message so early deletes remain
        visible after squeeze/park — no product-specific hardcoding.
        """
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        # Successful FS mutations: durable pin, refreshed immediately.
        if name in self._FS_MUTATION_TOOLS:
            body = (content or "").strip()
            if not body or body.upper().startswith("ERROR"):
                return
            path = str(args.get("path") or "").strip() or "?"
            first = body.split("\n", 1)[0][:160]
            note = f"OK {name}({path}): {first}"
            prefix = f"OK {name}({path}):"
            self.workspace_facts = [
                f for f in self.workspace_facts if not str(f).startswith(prefix)
            ]
            self.workspace_facts.append(note)
            if len(self.workspace_facts) > 16:
                self.workspace_facts = self.workspace_facts[-16:]
            self._refresh_mutation_ledger()
            return

        key_tools = {
            "list_dir",
            "codebase_overview",
            "codebase_find_similar",
            "codebase_impact",
        }
        if name not in key_tools:
            if name == "read_file" and content.startswith("ERROR: not found"):
                path = str(args.get("path") or "")
                note = f"read_file missing: {path}"
            else:
                return
        else:
            preview = content.strip().replace("\r\n", "\n")
            if len(preview) > 240:
                preview = preview[:240] + "…"
            path_hint = str(args.get("path") or args.get("query") or args.get("symbol_or_path") or ".")
            note = f"{name}({path_hint}): {preview}"

        self.workspace_facts.append(note)
        if len(self.workspace_facts) > 16:
            self.workspace_facts = self.workspace_facts[-16:]

    def _refresh_mutation_ledger(self) -> None:
        """Diff-style FS mutation pin in the system message (not a full tree reprint)."""
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return
        marker = "## FS mutations (this session)"
        content = str(self.messages[0].get("content") or "")
        if marker in content:
            head, _, rest = content.partition(marker)
            next_sec = rest.find("\n## ")
            tail = rest[next_sec:] if next_sec >= 0 else ""
            content = (head.rstrip() + ("\n" + tail.lstrip("\n") if tail else "")).rstrip()
        facts = [f for f in (self.workspace_facts or []) if str(f).startswith("OK ")]
        if not facts:
            self.messages[0]["content"] = content.strip()
            return
        block = marker + "\n" + "\n".join(f"- {f}" for f in facts[-12:])
        self.messages[0]["content"] = (content.rstrip() + "\n\n" + block).strip()

    def _refresh_workspace_grounding(self) -> None:
        """Rewrite pinned ground-truth in the system message each user turn."""
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return

        ws = self.settings.workspace.resolve()
        lines = [
            "## Workspace ground truth (authoritative)",
            f"Root: {ws}",
        ]
        indexed_paths: list[str] = []
        if not getattr(self, "social", False):
            lines.append(
                "Do NOT invent paths like src/, app/, components/ unless listed below or just confirmed by a tool this turn."
            )
            lines.append(
                "If a previous tool showed only certain files (e.g. index.html), treat that as current reality until tools say otherwise."
            )
            try:
                from ...services import codebase_memory as cbm

                idx = cbm.get_or_build_index(ws)
                indexed_paths = [fe.path for fe in idx.files[:12]]
                lines.append(f"Indexed files ({len(idx.files)}; use codebase_overview / search_text for the rest):")
                if indexed_paths:
                    lines.extend(f"- {p}" for p in indexed_paths)
                else:
                    lines.append("- (none)")
            except Exception as exc:  # noqa: BLE001
                from ...core.logutil import get_logger, log_exception

                log_exception(get_logger("metateam.agent"), "grounding index failed", exc)

        try:
            entries = sorted(ws.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            shown = []
            # The index already names the directories its files live in; repeating
            # them as top-level entries is the same bytes twice in every turn.
            covered = {
                str(p).replace("\\", "/").split("/", 1)[0]
                for p in indexed_paths
            }
            for e in entries:
                if len(shown) >= 24:
                    break
                if e.name.startswith(".") and e.name not in {
                    ".sidekick",
                    ".edgesidekick",
                }:
                    continue
                if e.is_dir() and e.name in covered:
                    continue
                shown.append(f"{'dir' if e.is_dir() else 'file'}:{e.name}")
            if shown:
                lines.append("Top-level now: " + ", ".join(shown))
            else:
                lines.append("Top-level now: (empty)")
        except OSError:
            pass

        layout_facts = [
            f for f in (self.workspace_facts or []) if not str(f).startswith("OK ")
        ]
        if layout_facts:
            lines.append("Session discoveries (from tools):")
            lines.extend(f"- {f}" for f in layout_facts[-4:])

        try:
            from ...services.workspace_rules import load_workspace_rules

            rules = load_workspace_rules(ws)
            if rules:
                lines.append(rules)
        except Exception:
            pass

        try:
            from ...services.verify_detect import grounding_verify_hint

            hint = grounding_verify_hint(ws)
            if hint:
                lines.append(hint)
        except Exception:
            pass

        block = "\n".join(lines)
        marker = "## Workspace ground truth (authoritative)"
        wm_marker = "## Session working memory (do not forget)"
        content = str(self.messages[0].get("content") or "")
        if wm_marker in content:
            content = content.split(wm_marker, 1)[0].rstrip()
        if marker in content:
            content = content.split(marker, 1)[0].rstrip()
        try:
            from ...services.user_auth import current_user_prompt_block

            identity = current_user_prompt_block()
        except Exception:
            identity = ""
        user_marker = "## Current user"
        if user_marker in content:
            content = content.split(user_marker, 1)[0].rstrip()
        if identity:
            content = (content.rstrip() + "\n\n" + identity).strip()
        old_cm = "## Codebase memory (structure projection)"
        if old_cm in content:
            head, _, tail = content.partition(old_cm)
            rest = tail.split("\n## ", 1)
            if len(rest) == 2:
                content = (head.rstrip() + "\n\n## " + rest[1]).strip()
            else:
                content = head.rstrip()
        # Keep the early WORKSPACE: line in sync with the live pin. Otherwise a
        # session created while the tenant default was folder A, then pinned to
        # B, still tells the model "WORKSPACE: A" while ground truth says B —
        # and the model invents a split between "cwd" and "workspace root".
        ws_line = f"WORKSPACE: {ws}"
        if re.search(r"(?m)^WORKSPACE:\s*.+$", content):
            # Use a callable replacement — a plain string would interpret
            # Windows path `\t` / `\n` sequences as regex backslash escapes.
            content = re.sub(
                r"(?m)^WORKSPACE:\s*.+$", lambda _m: ws_line, content, count=1
            )
        elif content.strip():
            content = content.rstrip() + "\n\n" + ws_line
        else:
            content = ws_line

        self.messages[0]["content"] = (content.rstrip() + "\n\n" + block).strip()
        self._refresh_mutation_ledger()

    def _refresh_memory_block(self) -> None:
        """Replace the ## Memory section with the current catalog (+ pinned)."""
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return
        from ...services.memory import merge_memory_block

        self.messages[0]["content"] = merge_memory_block(
            str(self.messages[0].get("content") or ""),
            self.settings.memory_file,
        )

    def _refresh_directives_block(self) -> None:
        """Replace sticky User directives (Instruction Hierarchy charter)."""
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return
        from ...services.user_directives import merge_directives_block

        dirs = getattr(self, "user_directives", None) or []
        self.messages[0]["content"] = merge_directives_block(
            str(self.messages[0].get("content") or ""),
            dirs,
        )

    def _refresh_core_prompt(self) -> None:
        """Ship the current CORE rules into long-lived session system messages."""
        if self.is_subagent and not getattr(self, "full_agent", False):
            return
        if getattr(self, "social", False) or getattr(self, "talk_only", False):
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return
        from ..prompts import merge_core_into_system

        self.messages[0]["content"] = merge_core_into_system(
            str(self.messages[0].get("content") or "")
        )

    def _apply_turn_coherence_policy(self, user_text: str) -> None:
        """Pin per-turn Anti-Piling policy (align/contract/pile) into system message."""
        if self.is_subagent:
            return
        if not self.messages or self.messages[0].get("role") != "system":
            return
        policy = policy_for_turn(user_text)
        self._turn_policy = policy
        block = format_turn_policy_block(policy)
        self.messages[0]["content"] = merge_policy_into_system(
            str(self.messages[0].get("content") or ""),
            block,
        )
        self._emit(
            "coherence_policy",
            {
                "kind": policy.label,
                "require_align": policy.require_align,
                "require_shape_contract": policy.require_shape_contract,
                "require_pile_check": policy.require_pile_check,
                "message": f"连贯策略：{policy.label}",
            },
        )
