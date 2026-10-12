"""Background scheduler loop — fires due ScheduledTasks as agent turns.

Runs as a daemon thread started from the FastAPI lifespan. Each tick collects
due tasks and executes them on worker threads that mirror the chat worker in
api/routes/chat.py (busy guard → agent.run → persist → final event → flush).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..api.sse import event_payload
from ..core.events import Event
from ..core.logutil import get_logger, log_exception
from .schedule_live import SCHEDULE_LIVE
from .scheduler import SCHEDULES, ScheduledTask
from .shell_job_notify import flush_pending_job_notices
from .store import STORE
from .tenant_context import reset_user, set_user

# Events the open chat needs in order to render a scheduled turn as it runs.
_LIVE_TYPES = frozenset(
    {
        "schedule_trigger",
        "assistant_delta",
        "assistant_reasoning_delta",
        "tool_call_delta",
        "tool_start",
        "tool_end",
        "approval_request",
        "approval_resolved",
        "ask_request",
        "ask_resolved",
        "plan_created",
        "plan_confirm_request",
        "plan_confirm_resolved",
        "plan_step",
        "plan_done",
        "llm_start",
        "browser_open",
        "error",
        "final",
    }
)

_log = get_logger("metateam.scheduler")


def _skip_notice(task: ScheduledTask, detail: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": f"⏰ 定时任务「{task.title}」到点时{detail}",
        "ts": time.time(),
        "sidekick": {
            "kind": "schedule_notice",
            "task_id": task.id,
            "event": "skipped_busy",
        },
    }


def _queue_notice(sess: Any, notice: dict[str, Any]) -> None:
    """Mirror deliver_job_done: queue while busy, else append + persist."""
    if sess.busy:
        sess.pending_job_notices.append(notice)
        return
    sess.agent.messages.append(notice)
    try:
        STORE.persist(sess.id)
    except Exception as exc:
        log_exception(_log, f"persist skip notice failed for {sess.id}", exc)


class SchedulerLoop:
    TICK_SEC = 5
    MAX_PARALLEL = 2
    ONCE_DEFER_SEC = 60
    ONCE_DEFER_CAP = 240  # ≈4h of busy-defers before a once task is marked missed

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._inflight: set[str] = set()
        self._last_tick = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="metateam-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.TICK_SEC):
            try:
                self._tick()
            except Exception as exc:
                log_exception(_log, "scheduler tick failed", exc)

    def _tick(self) -> None:
        now = time.time()
        if self._last_tick and now < self._last_tick - 60:
            _log.warning("scheduler: clock jumped backwards, resyncing schedules")
            SCHEDULES.resync_after_clock_change(now)
        self._last_tick = now
        for task in SCHEDULES.due(now):
            if task.id in self._inflight:
                continue
            if len(self._inflight) >= self.MAX_PARALLEL:
                SCHEDULES.defer(task, now + 15, "并发排队中")
                continue
            self._inflight.add(task.id)
            threading.Thread(
                target=self._run_task, args=(task,), name=f"schedule-{task.id}", daemon=True
            ).start()

    def _run_task(self, task: ScheduledTask) -> None:
        """Run one due task. Public-ish so tests can call it directly."""
        try:
            set_user(task.user_id)
            try:
                sess = STORE.get(task.session_id)
                if sess is None:
                    SCHEDULES.pause_orphan(task, "绑定的会话不存在或已删除")
                    return
                if sess.busy or sess.stop_requested:
                    self._handle_busy(sess, task)
                    return
                self._execute_turn(sess, task)
            except Exception as exc:
                log_exception(_log, f"schedule run failed for {task.id}", exc)
                SCHEDULES.mark_result(task, ok=False, error=str(exc))
        except Exception as exc:
            log_exception(_log, f"schedule dispatch failed for {task.id}", exc)
        finally:
            reset_user()
            self._inflight.discard(task.id)

    def _handle_busy(self, sess: Any, task: ScheduledTask) -> None:
        now = time.time()
        _queue_notice(
            sess,
            _skip_notice(
                task,
                "本会话正在执行，本次已跳过。" if task.kind != "once" else "本会话正在执行，已顺延重试。",
            ),
        )
        try:
            sess.agent.bus.emit(
                Event(
                    type="schedule_trigger",
                    data={
                        "task_id": task.id,
                        "title": task.title,
                        "session_id": sess.id,
                        "event": "skipped_busy",
                    },
                    agent_id=sess.agent.agent_id,
                )
            )
        except Exception as exc:
            log_exception(_log, f"emit schedule skip failed for {sess.id}", exc)
        if task.kind == "once":
            SCHEDULES.defer(task, now + self.ONCE_DEFER_SEC)
            if task.defer_count >= self.ONCE_DEFER_CAP:
                with SCHEDULES._lock:
                    task.enabled = False
                    task.next_run = 0
                    task.last_status = "missed"
                    task.last_error = "会话长时间繁忙，已放弃执行"
                SCHEDULES._persist_user(task.user_id)
        else:
            SCHEDULES.advance(task, now)

    def _execute_turn(self, sess: Any, task: ScheduledTask) -> None:
        agent = sess.agent

        def forward(ev: Event) -> None:
            if ev.type not in _LIVE_TYPES:
                return
            data = dict(ev.data or {})
            data.setdefault("session_id", sess.id)
            SCHEDULE_LIVE.publish(
                event_payload(
                    ev.type,
                    data,
                    agent_id=ev.agent_id or agent.agent_id,
                    ts=ev.ts,
                )
            )

        subscribe = getattr(agent.bus, "subscribe", None)
        unsub = subscribe(forward) if callable(subscribe) else (lambda: None)
        sess.stop_requested = False
        sess.busy = True
        try:
            try:
                agent.bus.emit(
                    Event(
                        type="schedule_trigger",
                        data={
                            "task_id": task.id,
                            "title": task.title,
                            "session_id": sess.id,
                            "prompt": task.prompt,
                            "event": "started",
                        },
                        agent_id=agent.agent_id,
                    )
                )
            except Exception as exc:
                log_exception(_log, f"emit schedule_trigger failed for {sess.id}", exc)

            try:
                result = agent.run(
                    task.prompt,
                    mode="agent",
                    display=f"定时任务 · {task.title}",
                )
            except Exception as exc:
                try:
                    agent.bus.emit(
                        Event(
                            type="error",
                            data={"message": str(exc), "session_id": sess.id},
                            agent_id=agent.agent_id,
                        )
                    )
                except Exception:
                    log_exception(_log, f"emit schedule error failed for {sess.id}", exc)
                raise
            try:
                STORE.persist(sess.id)
            except Exception as exc:
                log_exception(_log, f"persist after schedule run failed for {sess.id}", exc)
            final_data: dict[str, Any] = {
                "text": getattr(result, "text", ""),
                "session_id": sess.id,
                "cancelled": bool(getattr(result, "cancelled", False)),
                "title": sess.title,
                "schedule": {
                    "task_id": task.id,
                    "task_title": task.title,
                    "kind": task.kind,
                },
            }
            try:
                agent.bus.emit(
                    Event(type="final", data=final_data, agent_id=agent.agent_id)
                )
            except Exception as exc:
                log_exception(_log, f"emit schedule final failed for {sess.id}", exc)
            SCHEDULES.mark_result(task, ok=True)
        finally:
            try:
                unsub()
            except Exception:
                pass
            SCHEDULES.advance(task, time.time())
            sess.busy = False
            try:
                flush_pending_job_notices(sess.id)
            except Exception as exc:
                log_exception(_log, f"flush notices after schedule run failed", exc)


SCHEDULER = SchedulerLoop()
