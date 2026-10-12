"""Scheduled tasks API — list / create / patch / delete for the UI panel."""

from __future__ import annotations

import asyncio
import json
import queue
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ...services.schedule_live import SCHEDULE_LIVE
from ...services.scheduler import SCHEDULES, parse_local_dt
from ...services.tenant_context import get_user_id
from ..http import require_session
from ..schemas import ScheduleCreate, SchedulePatch
from ..sse import SSE_HEADERS

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _hidden(sess: Any) -> bool:
    return bool(getattr(sess, "hidden", False)) or bool(
        getattr(getattr(sess, "agent", None), "social", False)
    )


@router.get("")
def api_schedules() -> dict[str, Any]:
    tasks = SCHEDULES.list(get_user_id())
    return {
        "items": [t.to_dict() for t in tasks],
        "enabled": sum(1 for t in tasks if t.enabled),
    }


@router.post("")
def api_schedule_create(body: ScheduleCreate) -> dict[str, Any]:
    from ...services.store import STORE

    uid = get_user_id()
    sess = require_session(body.session_id)
    # 会话版 one-shot sessions disappear before the task fires. Keep the task
    # on the durable 日程 session instead of rejecting the create.
    if _hidden(sess):
        sess = STORE.anchor_schedule_session(sess)
    try:
        task = SCHEDULES.create(
            user_id=uid,
            session_id=sess.id,
            title=body.title,
            prompt=body.prompt,
            kind=body.kind,
            at=parse_local_dt(body.at) if body.kind == "once" else 0,
            time_str=body.time or "",
            weekdays=body.weekdays,
            interval_minutes=int(body.interval_minutes or 0),
            enabled=body.enabled,
            until=body.until or "",
            month_day=int(body.month_day or 0),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return task.to_dict()


@router.get("/live")
async def api_schedule_live() -> EventSourceResponse:
    """Stay open and forward tokens from scheduled runs. Sends ping when idle."""
    q = SCHEDULE_LIVE.subscribe()

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=15)
                    )
                except queue.Empty:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {
                    "event": item.get("type") or "message",
                    "data": json.dumps(item, ensure_ascii=False),
                }
        finally:
            SCHEDULE_LIVE.unsubscribe(q)

    return EventSourceResponse(gen(), headers=SSE_HEADERS)


@router.get("/{task_id}")
def api_schedule_detail(task_id: str) -> dict[str, Any]:
    task = SCHEDULES.get(task_id, get_user_id())
    if not task:
        raise HTTPException(404, "schedule not found")
    data = task.to_dict()
    data["upcoming"] = SCHEDULES.upcoming(task, 3)
    return data


@router.patch("/{task_id}")
def api_schedule_patch(task_id: str, body: SchedulePatch) -> dict[str, Any]:
    uid = get_user_id()
    task = SCHEDULES.get(task_id, uid)
    if not task:
        raise HTTPException(404, "schedule not found")
    changes = body.model_dump(exclude_none=True)
    try:
        updated = SCHEDULES.patch(task_id, uid, changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return updated.to_dict()


@router.delete("/{task_id}")
def api_schedule_delete(task_id: str) -> dict[str, Any]:
    if not SCHEDULES.delete(task_id, get_user_id()):
        raise HTTPException(404, "schedule not found")
    return {"status": "ok", "task_id": task_id}
