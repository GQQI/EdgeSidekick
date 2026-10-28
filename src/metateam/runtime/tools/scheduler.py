"""Scheduled-task tools — let the agent create/list/remove future runs."""

from __future__ import annotations

from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext


def _fmt_ts(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "(未计划)"


def register_scheduler_tools(reg: ToolRegistry, ctx: ToolContext) -> None:

    def create_scheduled_task(
        title: str,
        prompt: str,
        kind: str,
        at: str = "",
        time: str = "",
        weekdays: list[int] | None = None,
        interval_minutes: int = 0,
        until: str = "",
        month_day: int = 0,
    ) -> str:
        from ...services.scheduler import SCHEDULES, align_schedule_request, parse_local_dt, resolve_once_at
        from ...services.store import STORE
        from ...services.tenant_context import get_session_id, get_user_id

        uid = get_user_id()
        sid = get_session_id()
        sess = STORE.get(sid) if sid else None
        try:
            bound = STORE.anchor_schedule_session(sess)
        except Exception as exc:
            return f"创建失败：{exc}"
        anchored = bound.id != (getattr(sess, "id", "") or "")
        sid = bound.id
        import time as _time

        last_user = ""
        source = sess or bound
        for msg in reversed(getattr(getattr(source, "agent", None), "messages", None) or []):
            if isinstance(msg, dict) and msg.get("role") == "user":
                last_user = str(msg.get("content") or "")
                break
        span = f"{title}\n{prompt}\n{last_user}"
        kind, until, interval_minutes = align_schedule_request(
            kind, until, int(interval_minutes or 0), span
        )
        month_span = any(s in span for s in ("定时一个月", "持续一个月", "这一个月", "整月", "一个月内"))
        near = False
        if kind == "once":
            at = resolve_once_at(at, span)
        if kind == "once" and at:
            try:
                near = parse_local_dt(at) - _time.time() < 36 * 3600
            except ValueError:
                near = False
        if month_span and kind == "once" and (not at or near):
            return (
                "创建失败：这是持续大约一个月的任务，不能建成今天的一次性任务。"
                "请改用 kind=daily（或用户说的每周/间隔），time 填每天的时刻，"
                "until 填从今天起约 30 天后的 YYYY-MM-DD。"
                "如果是每个月的某一天，用 kind=monthly 和 month_day。"
                "如果是一个月后只跑一次，kind=once 且 at 必须是一个月后的日期。"
            )
        if month_span and kind in ("daily", "weekly", "interval") and not (until or "").strip():
            return (
                "创建失败：持续一个月必须带 until（YYYY-MM-DD，从今天起约 30 天）。"
                "不要只创建今天。"
            )
        try:
            at_epoch = parse_local_dt(at) if kind == "once" else 0
            task = SCHEDULES.create(
                user_id=uid,
                session_id=sid,
                title=title,
                prompt=prompt,
                kind=kind,
                at=at_epoch,
                time_str=time,
                weekdays=weekdays,
                interval_minutes=int(interval_minutes or 0),
                until=until,
                month_day=int(month_day or 0),
            )
        except ValueError as exc:
            if "已超过结束日期" in str(exc):
                return (
                    f"创建失败：下一次运行晚于结束时间（until={until or '未填'}）。"
                    "这不是功能限制。每几分钟持续到某个时刻要用 kind=interval，"
                    "until 填 YYYY-MM-DD HH:MM，而且这个时刻必须还没到。"
                    "不要告诉用户当前会话不支持定时任务。改好参数再调用一次。"
                )
            return f"创建失败：{exc}"
        where = (
            "可在会话版侧边栏「日程」查看。到点后会在日程会话里执行。"
            if anchored
            else "到点后会在当前会话自动执行，结果会出现在聊天里。也可在侧边栏「日程」查看。"
        )
        return (
            f"已创建定时任务「{task.title}」（{task.to_dict()['schedule_text']}）。\n"
            f"下次运行：{_fmt_ts(task.next_run)}（task_id={task.id}）。\n"
            f"{where}\n"
            "这是工具的结果。不要说需要换到别的会话，也不要之后改口说没设成功或已经设成功。"
        )

    def list_scheduled_tasks() -> str:
        from ...services.scheduler import SCHEDULES
        from ...services.tenant_context import get_user_id

        tasks = SCHEDULES.list(get_user_id())
        if not tasks:
            return "当前没有定时任务。"
        lines = ["定时任务列表："]
        for t in tasks:
            info = t.to_dict()
            state = "启用" if t.enabled else "已暂停"
            lines.append(
                f"- [{state}] {t.title}（{info['schedule_text']}）"
                f" 下次运行：{_fmt_ts(t.next_run)} task_id={t.id}"
                + (f" 上次状态：{t.last_status}" if t.last_status else "")
            )
        return "\n".join(lines)

    def cancel_scheduled_task(task_id: str) -> str:
        from ...services.scheduler import SCHEDULES
        from ...services.tenant_context import get_user_id

        task = SCHEDULES.get(task_id, get_user_id())
        if not task:
            return (
                "未找到该 task_id。先调用 list_scheduled_tasks 查看现有定时任务，"
                "再把完整的 task_id 传进来。"
            )
        SCHEDULES.delete(task.id, task.user_id)
        return f"已删除定时任务「{task.title}」。"

    reg.register(
        Tool(
            "create_scheduled_task",
            "Schedule a future agent run. This works in 会话版 and the workbench; "
            "do not tell the user to switch to another session. "
            "After the tool returns 已创建, confirm that and point to 侧边栏「日程」. "
            "If it returns 创建失败, report the failure. Never reverse the tool result "
            "using group memory. "
            "kind: once(at='YYYY-MM-DD HH:MM' in the user's local timezone from Host Local time; 「今天 HH:MM」stays today unless that local time already passed) | "
            "daily(time='HH:MM') | weekly(time, weekdays=[0=Mon..6=Sun]) | "
            "interval(interval_minutes>=5) | monthly(time, month_day=1-31). "
            "Repeating kinds may set until='YYYY-MM-DD' (inclusive last day). "
            "「定时一个月 / 持续一个月 / 这一个月」is NOT once-today: use daily "
            "(or the cadence the user named) and until = about 30 days from today. "
            "「每个月」is monthly. 「一个月后」is once with at one month from now. "
            "Never create a task that only runs today when the user asked for a month. "
            "prompt must be a self-contained instruction. Requires user approval.",
            {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "简短标题，如：每日站会提醒",
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "到点后执行的完整指令；必须自包含，"
                            "不能依赖本次对话的临时上下文"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["once", "daily", "weekly", "interval", "monthly"],
                    },
                    "at": {
                        "type": "string",
                        "description": "once 必填，本地时间 YYYY-MM-DD HH:MM",
                    },
                    "time": {
                        "type": "string",
                        "description": "daily/weekly 必填，HH:MM 24小时制",
                    },
                    "weekdays": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 6},
                        "description": "weekly 必填，0=周一 … 6=周日",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 10080,
                        "description": "interval 必填，间隔分钟数（≥5）",
                    },
                    "until": {
                        "type": "string",
                        "description": (
                            "重复任务的结束日期 YYYY-MM-DD（含当天）。"
                            "定时一个月/持续一个月时必填，约为今天起 30 天后"
                        ),
                    },
                    "month_day": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 31,
                        "description": "monthly 必填，每月几号（1-31，短月自动取月末）",
                    },
                },
                "required": ["title", "prompt", "kind"],
            },
            create_scheduled_task,
            parallel_safe=False,
            requires_approval=True,
        )
    )
    reg.register(
        Tool(
            "list_scheduled_tasks",
            "List existing scheduled tasks (id, schedule, next run, enabled). "
            "Use before cancel_scheduled_task.",
            {"type": "object", "properties": {}, "required": []},
            list_scheduled_tasks,
            parallel_safe=True,
        )
    )
    reg.register(
        Tool(
            "cancel_scheduled_task",
            "Delete one scheduled task by task_id. Requires user approval.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Exact id from list_scheduled_tasks"}
                },
                "required": ["task_id"],
            },
            cancel_scheduled_task,
            parallel_safe=False,
            requires_approval=True,
        )
    )
