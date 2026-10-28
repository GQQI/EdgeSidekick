"""Fan-out for scheduled-task runs so an open chat can stream them.

The scheduler executes on a background thread. The workbench only sees
assistant tokens while something is subscribed. This hub keeps a long-lived
SSE (`GET /api/schedules/live`) that forwards the live turn.
"""

from __future__ import annotations

import queue
import threading
from typing import Any


class ScheduleLiveHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[dict[str, Any]]] = []

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass


SCHEDULE_LIVE = ScheduleLiveHub()
