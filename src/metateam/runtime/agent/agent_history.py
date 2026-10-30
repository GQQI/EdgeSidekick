"""Transcript repair: cancel seals and dangling tool-call recovery."""

from __future__ import annotations

from typing import Any

# Control text for the NEXT model turn only — never show in the assistant bubble.
_CANCEL_CONTROL = (
    "（用户已停止本轮生成。请等待下一条用户指令；"
    "不要继续或恢复刚才未完成的任务。）"
)
_INTERRUPT_CONTROL = (
    "（上一轮生成已中断。请等待用户下一条指令；"
    "不要继续或恢复未完成的任务。）"
)

# Markers used to detect poisoned / imitated cancel notices in history.
_CANCEL_MARKERS = (
    "用户已停止本轮生成",
    "请等待下一条用户指令",
    "不要继续或恢复刚才未完成的任务",
    "上一轮生成已中断",
    "不要继续或恢复未完成的任务",
)


def is_cancel_control_text(text: str) -> bool:
    """True when text is (or is dominated by) the stop/interrupt control notice."""
    s = (text or "").strip()
    if not s:
        return False
    if s in (_CANCEL_CONTROL, _INTERRUPT_CONTROL):
        return True
    # Parenthetical block alone
    if s.startswith("（") and s.endswith("）"):
        if "用户已停止本轮生成" in s or "上一轮生成已中断" in s:
            return True
    return False


def strip_cancel_control(text: str) -> str:
    """Remove injected/imitated cancel notices from visible assistant content."""
    raw = text or ""
    if not any(m in raw for m in _CANCEL_MARKERS):
        return raw
    lines = raw.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if is_cancel_control_text(stripped):
            continue
        if "用户已停止本轮生成" in stripped or "上一轮生成已中断" in stripped:
            # Line mixed with real content + notice: drop the notice clause.
            for marker in (
                "（用户已停止本轮生成",
                "用户已停止本轮生成",
                "（上一轮生成已中断",
                "上一轮生成已中断",
            ):
                idx = stripped.find(marker)
                if idx >= 0:
                    stripped = stripped[:idx].rstrip(" \t\r\n（(")
                    break
            if not stripped or is_cancel_control_text(stripped):
                continue
            kept.append(stripped)
            continue
        kept.append(line)
    # Trim trailing blank lines left by removals
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept).strip()


# Runtime control messages (nudges/seals) are instructions for the current
# round only. Replaying old copies makes the model re-read "continue" many
# times, which both bloats the prompt and fuels repetition.
_CONTROL_KINDS = frozenset(
    {
        "goal_continue",
        "stall",
        "spin_stop",
        "post_compress",
        "empty_assistant_recover",
        "text_loop_cut",
        "tool_repeat",
        "browser_nav_recover",
        "file_retry",
        "empty_final_recover",
        "cancel_seal",
    }
)


def _control_kind(message: dict[str, Any]) -> str:
    meta = message.get("sidekick")
    kind = str(meta.get("kind") or "") if isinstance(meta, dict) else ""
    if kind:
        return kind
    if message.get("sidekick_internal") or message.get("internal"):
        return "legacy_control"
    return ""


def _is_turn_boundary(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    kind = _control_kind(message)
    # Plan steps start a real sub-obligation; other internal messages are nudges.
    return not kind or kind == "plan_step"


def prune_stale_internal_nudges(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop control nudges from earlier turns and older duplicates of each kind.

    The returned list is a shallow copy for the model call; persisted history
    keeps every message so transcripts and audits remain complete.
    """
    last_turn = -1
    for i, message in enumerate(messages):
        if _is_turn_boundary(message):
            last_turn = i

    keep_seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        kind = _control_kind(message)
        if kind in _CONTROL_KINDS or kind == "legacy_control":
            if i < last_turn:
                continue
            if kind in keep_seen:
                continue
            keep_seen.add(kind)
        kept.append(message)
    kept.reverse()
    return kept


class AgentHistoryMixin:
    def _is_internal_message(self, m: dict[str, Any]) -> bool:
        if m.get("sidekick_internal") or m.get("internal"):
            return True
        meta = m.get("sidekick")
        if isinstance(meta, dict) and meta.get("internal"):
            return True
        content = str(m.get("content") or "").lstrip()
        return content.startswith("[Plan step ") or content.startswith("[sidekick:")

    def _last_user_index(self) -> int:
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "user" and not self._is_internal_message(m):
                return i
        return -1

    def _scrub_cancel_notices_in_history(self) -> None:
        """Drop control notices that leaked into visible assistant bodies.

        Past seals used to append the stop instruction into assistant content.
        Models then imitated it mid-stream. Scrub so neither the UI nor the
        next prompt sees it as model speech.
        """
        for m in self.messages:
            if m.get("role") != "assistant":
                continue
            if m.get("tool_calls") or self._is_internal_message(m):
                continue
            raw = str(m.get("content") or "")
            cleaned = strip_cancel_control(raw)
            if cleaned != raw:
                m["content"] = cleaned

    def _append_cancel_control(self, *, kind: str = "user_stop") -> None:
        """Internal-only instruction for the next model turn."""
        note = _CANCEL_CONTROL if kind == "user_stop" else _INTERRUPT_CONTROL
        self.messages.append(
            {
                "role": "system",
                "content": note,
                "sidekick_internal": True,
                "sidekick": {"internal": True, "kind": "cancel_seal"},
            }
        )

    def _seal_cancelled_turn(self, partial_text: str = "") -> str:
        """Drop unfinished tool chains after stop so the next turn won't resume them.

        Visible assistant content keeps only what the model already said.
        The stop instruction stays internal — never spliced into the bubble.
        """
        self._scrub_cancel_notices_in_history()
        last_user = self._last_user_index()
        if last_user < 0:
            return strip_cancel_control(partial_text or "")

        tail = self.messages[last_user + 1 :]
        partial = strip_cancel_control(partial_text or "")

        kept_bits: list[str] = []
        if partial.strip():
            kept_bits.append(partial.strip())
        for m in tail:
            if m.get("role") != "assistant":
                continue
            if m.get("tool_calls") or self._is_internal_message(m):
                continue
            text = strip_cancel_control(str(m.get("content") or "")).strip()
            if text and text not in kept_bits:
                kept_bits.append(text)

        body = "\n\n".join(kept_bits).strip()
        if len(body) > 2500:
            from ...core.textutil import safe_clip

            body = safe_clip(body, 2500)

        self.messages = self.messages[: last_user + 1]
        if body:
            self.messages.append({"role": "assistant", "content": body})
        # Control for the next turn only (hidden from UI via sidekick_internal).
        self._append_cancel_control(kind="user_stop")
        return body

    def _repair_dangling_tool_calls(self) -> None:
        """If history ends mid tool-call (e.g. crash), seal it before a new turn."""
        self._scrub_cancel_notices_in_history()
        if not self.messages:
            return
        last_asst = -1
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                last_asst = i
                break
            if m.get("role") == "user":
                break
        if last_asst < 0:
            return
        needed: set[str] = set()
        for tc in self.messages[last_asst].get("tool_calls") or []:
            cid = str(tc.get("id") or "")
            if cid:
                needed.add(cid)
        if not needed:
            return
        have: set[str] = set()
        for m in self.messages[last_asst + 1 :]:
            if m.get("role") == "tool":
                have.add(str(m.get("tool_call_id") or ""))
            elif m.get("role") in ("assistant", "user"):
                break
        if needed <= have:
            return
        user_idx = last_asst - 1
        while user_idx >= 0 and self.messages[user_idx].get("role") != "user":
            user_idx -= 1
        if user_idx < 0:
            return
        # Keep any visible preamble from the interrupted assistant; drop tool_calls.
        prior = self.messages[last_asst]
        preamble = strip_cancel_control(str(prior.get("content") or "")).strip()
        self.messages = self.messages[: user_idx + 1]
        if preamble:
            self.messages.append({"role": "assistant", "content": preamble})
        self._append_cancel_control(kind="interrupt")
