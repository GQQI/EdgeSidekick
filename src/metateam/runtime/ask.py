"""Human-in-the-loop clarification gate (flexible option lists)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

CUSTOM_CHOICE_KEY = "custom"
MIN_ASK_OPTIONS = 2
MAX_ASK_OPTIONS = 12


@dataclass
class AskRequest:
    id: str
    question: str
    options: list[dict[str, str]]
    allow_custom: bool = True
    custom_label: str = "其他（请补充）"
    created_at: float = field(default_factory=time.time)


class AskGate:
    """Blocks worker threads until the UI answers a clarification question."""

    def __init__(self, timeout_sec: float = 600.0) -> None:
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._ui_lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._answers: dict[str, str] = {}
        self._pending: dict[str, AskRequest] = {}

    def serialize_ui(self) -> threading.Lock:
        """One in-flight ask dialog at a time (parallel subagents share this gate)."""
        return self._ui_lock

    def request(
        self,
        ask_id: str,
        question: str,
        options: list[dict[str, str]],
        *,
        allow_custom: bool = True,
        custom_label: str = "其他（请补充）",
    ) -> str:
        ev = threading.Event()
        req = AskRequest(
            id=ask_id,
            question=question,
            options=options,
            allow_custom=allow_custom,
            custom_label=custom_label,
        )
        with self._lock:
            self._events[ask_id] = ev
            self._pending[ask_id] = req
            self._answers.pop(ask_id, None)
        ok = ev.wait(timeout=self.timeout_sec)
        with self._lock:
            answer = self._answers.pop(ask_id, "") if ok else ""
            self._events.pop(ask_id, None)
            self._pending.pop(ask_id, None)
        if not ok:
            return "ERROR: user did not answer (timeout) — proceed with a reasonable default or ask again briefly."
        if not answer:
            return "ERROR: user cancelled the clarification — proceed with a reasonable default or stop."
        return answer

    def answer(
        self,
        ask_id: str,
        *,
        choice: str,
        text: str = "",
        option_label: str = "",
    ) -> bool:
        """Resolve a pending ask. choice is an option key or 'custom' for free-form text."""
        choice_key = (choice or "").strip()
        with self._lock:
            ev = self._events.get(ask_id)
            req = self._pending.get(ask_id)
            if not ev:
                return True  # already resolved

            label = (option_label or "").strip()
            if not label and req:
                for opt in req.options:
                    if str(opt.get("key") or "") == choice_key:
                        label = str(opt.get("label") or "").strip()
                        break

            if choice_key == CUSTOM_CHOICE_KEY:
                custom = (text or "").strip()
                if custom:
                    payload = f"User answered (custom): {custom}"
                else:
                    fallback = label or (req.custom_label if req else "其他")
                    payload = f"User chose custom: {fallback} (no extra details)"
            elif choice_key:
                extra = (text or "").strip()
                if label:
                    payload = (
                        f"User chose {choice_key}: {label}"
                        + (f" — {extra}" if extra and extra != label else "")
                    )
                elif extra:
                    payload = f"User chose {choice_key}: {extra}"
                else:
                    payload = f"User chose {choice_key}"
            else:
                custom = (text or "").strip()
                payload = custom or "User answered: (empty)"

            self._answers[ask_id] = payload
            ev.set()
            return True

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._events.keys())
        for ask_id in ids:
            with self._lock:
                ev = self._events.get(ask_id)
                if not ev:
                    continue
                self._answers[ask_id] = ""
                ev.set()

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": r.id,
                    "question": r.question,
                    "options": r.options,
                    "allow_custom": r.allow_custom,
                    "custom_label": r.custom_label,
                    "created_at": r.created_at,
                }
                for r in self._pending.values()
            ]


def normalize_option_labels(raw: Any) -> list[str]:
    """Coerce tool args into a clean list of option labels."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, list):
                return normalize_option_labels(parsed)
        except Exception:
            pass
        return [text]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if item is None:
            continue
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
        else:
            label = str(item).strip()
        if label:
            out.append(label)
    return out


def build_ask_options(labels: list[str]) -> list[dict[str, str]]:
    """Assign stable numeric keys 1..n to option labels."""
    clean = [str(x).strip() for x in labels if str(x).strip()]
    if len(clean) > MAX_ASK_OPTIONS:
        clean = clean[:MAX_ASK_OPTIONS]
    return [{"key": str(i), "label": label} for i, label in enumerate(clean, start=1)]


def _options_look_like_keypoints(labels: list[str]) -> bool:
    """Report prose and markdown headings are 要点, not button labels."""
    if not labels:
        return False
    if any("**" in x or x.lstrip().startswith("#") for x in labels):
        return True
    prose = 0
    for label in labels:
        if len(label) > 180 or label.count("。") >= 2:
            prose += 1
    return prose >= 2


def should_keep_as_markdown(question: str, labels: list[str] | None = None) -> bool:
    """True when ask_user was misused to present 要点 / a report.

    Only looks at option shape (markdown / long prose). Question wording is not
    keyword-matched — the model already chose ask_user.
    """
    del question  # kept for call-site compatibility
    opts = [str(x).strip() for x in (labels or []) if str(x).strip()]
    return _options_look_like_keypoints(opts)


def try_parse_inline_ask(text: str) -> Optional[dict[str, Any]]:
    """Inline MCQ rescue disabled — prefer the native ask_user tool."""
    del text
    return None


# Backward-compatible alias
try_parse_inline_mcq = try_parse_inline_ask
