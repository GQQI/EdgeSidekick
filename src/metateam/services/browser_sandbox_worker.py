"""Dedicated Playwright asyncio worker thread for the browser sandbox."""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

@dataclass
class _Job:
    factory: Callable[[], Awaitable[Any]]
    timeout: float = 120.0
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None


class _PlaywrightThread:
    """Dedicated thread: own asyncio loop + one async Playwright instance."""

    def __init__(self) -> None:
        self._q: queue.Queue[Optional[_Job]] = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="sidekick-playwright", daemon=True
        )
        self._started = False
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._pw: Any = None
        self._boot_error: Optional[BaseException] = None

    @property
    def playwright(self) -> Any:
        return self._pw

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._thread.start()
            self._started = True
        if not self._ready.wait(timeout=60.0):
            raise TimeoutError("browser sandbox worker failed to start")
        if self._boot_error is not None:
            raise RuntimeError(str(self._boot_error)) from self._boot_error

    def revive(self) -> None:
        """Abandon a wedged worker thread and start a fresh one in-place.

        Playwright ops can block the dedicated loop in a non-cancellable C call;
        when that happens `call` times out and the old daemon thread is left
        to die on process exit. New work must not queue behind it.
        """
        with self._start_lock:
            try:
                self._q.put_nowait(None)
            except Exception:
                pass
            self._q = queue.Queue()
            self._thread = threading.Thread(
                target=self._loop, name="sidekick-playwright", daemon=True
            )
            self._started = False
            self._ready = threading.Event()
            self._pw = None
            self._boot_error = None

    def call(self, factory: Callable[[], Awaitable[Any]], *, timeout: float = 120.0) -> Any:
        self.ensure_started()
        job = _Job(factory=factory, timeout=float(timeout))
        self._q.put(job)
        # The loop-side wait_for fires first and reports the real failure;
        # this grace period only catches a truly dead worker thread.
        if not job.done.wait(timeout=timeout + 10.0):
            self.revive()
            raise TimeoutError("browser sandbox worker timed out")
        if job.error is not None:
            raise RuntimeError(str(job.error)) from job.error
        return job.result

    def _loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        pw = None
        try:
            try:
                from playwright.async_api import async_playwright

                pw = loop.run_until_complete(async_playwright().start())
                self._pw = pw
            except BaseException as exc:  # noqa: BLE001
                self._boot_error = exc
                self._ready.set()
                return
            self._ready.set()
            while True:
                job = self._q.get()
                if job is None:
                    return
                try:
                    job.result = loop.run_until_complete(
                        asyncio.wait_for(job.factory(), timeout=job.timeout)
                    )
                except asyncio.TimeoutError:
                    # A hung op (launch/CDP connect/renderer wedged) must not
                    # poison the whole worker: cancel it and keep serving.
                    job.error = TimeoutError(
                        f"browser sandbox job timed out after {job.timeout:.0f}s"
                    )
                except BaseException as exc:  # noqa: BLE001
                    job.error = exc
                finally:
                    job.done.set()
        finally:
            self._pw = None
            if pw is not None:
                try:
                    loop.run_until_complete(pw.stop())
                except Exception:
                    pass
            try:
                loop.close()
            except Exception:
                pass


_WORKER = _PlaywrightThread()


