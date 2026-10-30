"""Plan confirmation gate (UI approve/reject)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PlanConfirmRequest:
    id: str
    summary: str
    tasks: list[dict[str, Any]]
    created_at: float = field(default_factory=time.time)


class PlanGate:
    """Blocks until the UI confirms or rejects a generated plan."""

    def __init__(self, timeout_sec: float = 600.0) -> None:
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._pending: dict[str, PlanConfirmRequest] = {}

    def request(
        self,
        plan_id: str,
        *,
        summary: str,
        tasks: list[dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]]:
        """Wait for UI. Returns (approved, applied plan fields)."""
        ev = threading.Event()
        req = PlanConfirmRequest(id=plan_id, summary=summary, tasks=list(tasks))
        with self._lock:
            self._events[plan_id] = ev
            self._pending[plan_id] = req
            self._decisions.pop(plan_id, None)
        ok = ev.wait(timeout=self.timeout_sec)
        with self._lock:
            approved = self._decisions.pop(plan_id, False) if ok else False
            self._events.pop(plan_id, None)
            held = self._pending.pop(plan_id, req)
        applied = {
            "summary": held.summary if held else summary,
            "tasks": list(held.tasks) if held else list(tasks),
        }
        if not ok:
            return False, applied
        return bool(approved), applied

    def decide(
        self,
        plan_id: str,
        approved: bool,
        *,
        summary: Optional[str] = None,
        tasks: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        with self._lock:
            ev = self._events.get(plan_id)
            if not ev:
                return True  # already resolved
            self._decisions[plan_id] = bool(approved)
            req = self._pending.get(plan_id)
            if req and approved:
                if summary and str(summary).strip():
                    req.summary = str(summary).strip()
                if isinstance(tasks, list) and tasks:
                    req.tasks = list(tasks)
            ev.set()
            return True

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._events.keys())
        for plan_id in ids:
            self.decide(plan_id, False)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": r.id,
                    "summary": r.summary,
                    "tasks": r.tasks,
                    "created_at": r.created_at,
                }
                for r in self._pending.values()
            ]
