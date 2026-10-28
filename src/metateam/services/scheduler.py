"""Scheduled tasks (定时任务) — data model, next-run math, per-user persistence.

The agent (or the user, via REST) registers a ScheduledTask bound to a chat
session. When it comes due, the scheduler loop runs one agent turn in that
session and posts the result to the chat. Persistence mirrors app_prefs:
one JSON file per tenant under data/tenants/<uid>/schedules.json.
"""

from __future__ import annotations

import calendar
import json
import re
import threading
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from ..core.events import new_id
from ..core.logutil import get_logger, log_exception
from .tenant_context import tenant_schedules_path, tenants_root

_log = get_logger("metateam.scheduler")

KINDS = ("once", "daily", "weekly", "interval", "monthly")
LIMIT_PER_USER = 200
CATCHUP_WINDOW_SEC = 24 * 3600  # missed one-off runs younger than this get re-run on startup

_WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_hhmm(value: str) -> tuple[int, int]:
    """Strict 'HH:MM' (24h). Raises ValueError on anything else."""
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise ValueError("时间格式必须是 HH:MM（24小时制）")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("时间格式必须是 HH:MM（24小时制）") from exc
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("时间超出范围（HH 00-23，MM 00-59）")
    return hh, mm


# Fixed offsets when the OS has no IANA tz database (common on Windows
# without the ``tzdata`` package). Keys are normative zone names only —
# never calendar dates.
_TZ_FIXED = {
    "Asia/Shanghai": timezone(timedelta(hours=8)),
    "Asia/Chongqing": timezone(timedelta(hours=8)),
    "Asia/Hong_Kong": timezone(timedelta(hours=8)),
    "UTC": timezone.utc,
    "Etc/UTC": timezone.utc,
}


def schedule_tz(tz: str | None = None):
    """User/local timezone for schedule wall-clock math.

    Prefer an explicit IANA name when given; otherwise the host local zone
    (Asia/Shanghai on most Sidekick installs). Never hard-code a calendar day.
    """
    name = (tz or "").strip()
    if name:
        if ZoneInfo is not None:
            try:
                return ZoneInfo(name)
            except Exception:
                pass
        fixed = _TZ_FIXED.get(name)
        if fixed is not None:
            return fixed
    local = datetime.now().astimezone().tzinfo
    return local or timezone.utc


def local_now(*, now: datetime | None = None, tz: str | None = None) -> datetime:
    """Current (or injected) instant in the schedule timezone."""
    zone = schedule_tz(tz)
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def parse_local_dt(value: str, *, tz: str | None = None) -> float:
    """Parse 'YYYY-MM-DD HH:MM' (or ISO) as schedule-local wall clock → epoch."""
    text = str(value or "").strip().replace("T", " ")
    zone = schedule_tz(tz)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=zone).timestamp()
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("时间格式必须是 YYYY-MM-DD HH:MM") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    else:
        parsed = parsed.astimezone(zone)
    return parsed.timestamp()


_DAY_OFFSETS = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
    "後天": 2,
    "today": 0,
    "tomorrow": 1,
}


def _apply_day_part(hour: int, part: str) -> int:
    part = part or ""
    if part in ("下午", "晚上", "傍晚") and hour < 12:
        hour += 12
    if part == "中午" and hour < 11:
        hour = 12
    if part in ("上午", "早上", "凌晨") and hour == 12:
        hour = 0
    return min(23, max(0, hour))


def _parse_clock_tokens(hour_tok: str, minute_tok: str | None, part: str) -> tuple[int, int] | None:
    raw_h = (hour_tok or "").strip()
    if raw_h.isdigit():
        hour = int(raw_h)
    else:
        hour = _number_token(raw_h)
        if hour <= 0 and raw_h not in ("0", "零", "〇"):
            return None
    minute = 0
    if minute_tok not in (None, ""):
        raw_m = str(minute_tok).strip()
        if raw_m.isdigit():
            minute = int(raw_m)
        else:
            minute = _number_token(raw_m)
    hour = _apply_day_part(hour, part)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def once_at_from_relative_text(
    text: str,
    *,
    now: datetime | None = None,
    tz: str | None = None,
) -> str:
    """Resolve 「今天 9:15」/「明天上午九点」/bare「9点15分」to local YYYY-MM-DD HH:MM.

    Rules (schedule timezone, typically Asia/Shanghai / host local):
    - Explicit 今天/今日 keeps today's date when that local time has not passed.
    - Roll to the next calendar day only when that local time has already passed
      (bare clock times, or 今天 after the slot).
    - 明天/后天 use a fixed day offset (no same-day roll).
    """
    raw = text or ""
    base = local_now(now=now, tz=tz)
    day_pat = (
        r"(?P<day>今天|今日|明天|明日|后天|後天|today|tomorrow)"
        r"\s*(?P<part>上午|早上|凌晨|中午|下午|晚上|傍晚)?"
        r"\s*(?P<h>\d{1,2}|[零〇一二两三四五六七八九十])"
        r"\s*(?:点|时|:)"
        r"\s*(?P<m>\d{1,2}|[零〇一二三四五六七八九十])?\s*分?"
    )
    match = re.search(day_pat, raw, flags=re.IGNORECASE)
    day_offset: int | None = None
    clock = None
    if match:
        day_key = match.group("day")
        day_norm = day_key.lower() if day_key.lower() in ("today", "tomorrow") else day_key
        day_offset = int(_DAY_OFFSETS.get(day_norm, _DAY_OFFSETS.get(day_key, 0)))
        clock = _parse_clock_tokens(match.group("h"), match.group("m"), match.group("part") or "")
    if clock is None:
        bare = re.search(
            r"(?P<part>上午|早上|凌晨|中午|下午|晚上|傍晚)?\s*"
            r"(?P<h>\d{1,2}|[零〇一二两三四五六七八九十])\s*"
            r"(?:点|时|:)\s*"
            r"(?P<m>\d{1,2}|[零〇一二三四五六七八九十])?\s*分?",
            raw,
        )
        if not bare:
            return ""
        # Interval-only phrases without a clock should not become once-at.
        if re.search(r"(?:每|每隔)\s*\d+\s*分钟", raw) and "点" not in raw and re.search(r"\d{1,2}:\d{2}", raw) is None:
            return ""
        clock = _parse_clock_tokens(bare.group("h"), bare.group("m"), bare.group("part") or "")
        if clock is None:
            return ""
        day_offset = 0
    assert day_offset is not None and clock is not None
    hour, minute = clock
    target = (base + timedelta(days=int(day_offset))).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if day_offset == 0 and target <= base:
        target = target + timedelta(days=1)
    return target.strftime("%Y-%m-%d %H:%M")


def resolve_once_at(
    at: str,
    text: str = "",
    *,
    now: datetime | None = None,
    tz: str | None = None,
) -> str:
    """Prefer relative phrases in the user/request text over a model-filled date.

    When the user says 今天 HH:mm and that local time is still ahead, the result
    stays on today's date even if `at` wrongly points at tomorrow.
    """
    relative = once_at_from_relative_text(text, now=now, tz=tz)
    if relative:
        return relative
    return str(at or "").strip()


def parse_until(value: str) -> str:
    """Normalize an inclusive end to YYYY-MM-DD or YYYY-MM-DD HH:MM."""
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return ""
    day = text[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("结束日期必须是 YYYY-MM-DD") from exc
    rest = text[10:].strip()
    if not rest:
        return day
    try:
        hh, mm = parse_hhmm(rest[:5])
    except ValueError as exc:
        raise ValueError("结束时间必须是 HH:MM") from exc
    return f"{day} {hh:02d}:{mm:02d}"


def until_end_ts(until: str) -> float | None:
    """Inclusive end, local time. A bare date lasts through 23:59:59."""
    text = (until or "").strip()
    if not text:
        return None
    if len(text) > 10:
        return parse_local_dt(text[:16])
    day = datetime.strptime(text[:10], "%Y-%m-%d")
    zone = schedule_tz()
    return day.replace(hour=23, minute=59, second=59, tzinfo=zone).timestamp()


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number_token(token: str) -> int:
    raw = (token or "").strip()
    if raw.isdigit():
        return int(raw)
    return _CN_NUM.get(raw, 0)


def minutes_from_text(text: str) -> int:
    match = re.search(r"(?:每|每隔)\s*(\d+|[一二两三四五六七八九十])\s*分钟", text or "")
    if not match:
        return 0
    n = _number_token(match.group(1))
    if n <= 0:
        return 0
    return max(5, min(10080, n))


def deadline_from_text(text: str, *, now: datetime | None = None, tz: str | None = None) -> str:
    """Clock time in '持续到明天上午九点'. Empty when the words do not say one."""
    raw = text or ""
    base = local_now(now=now, tz=tz)
    match = re.search(
        r"明天\s*(上午|早上|凌晨|中午|下午|晚上)?\s*(\d{1,2}|[一二两三四五六七八九十])\s*(?:点|时|:)",
        raw,
    )
    if not match:
        if "明天" in raw:
            return (base + timedelta(days=1)).strftime("%Y-%m-%d")
        return ""
    hour = _number_token(match.group(2))
    if hour <= 0 and match.group(2) not in ("0", "零"):
        return ""
    part = match.group(1) or ""
    if part in ("下午", "晚上") and hour < 12:
        hour += 12
    if part == "中午" and hour < 11:
        hour = 12
    if part in ("上午", "早上", "凌晨") and hour == 12:
        hour = 0
    hour = min(23, max(0, hour))
    target = (base + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return target.strftime("%Y-%m-%d %H:%M")


def align_schedule_request(kind: str, until: str, interval_minutes: int, text: str) -> tuple[str, str, int]:
    """Turn '每五分钟，持续到明天上午九点' into an interval that still fits.

    A daily 09:00 with until set to today has no run left, and create raises
    已超过结束日期. The spoken request is an interval ending at that clock time.
    """
    minutes = minutes_from_text(text)
    deadline = deadline_from_text(text)
    if minutes:
        kind = "interval"
        interval_minutes = minutes
    if deadline:
        # A clock time ("明天上午九点") is the real stop. A bare "明天" only
        # fills in when the model left until empty or too early.
        if " " in deadline or not (until or "").strip():
            until = deadline
        else:
            current_end = until_end_ts(until)
            wanted = until_end_ts(deadline)
            if wanted is not None and (current_end is None or current_end < wanted):
                until = deadline
    return kind, until, int(interval_minutes or 0)


def _within_until(task: "ScheduledTask", ts: float) -> float:
    end = until_end_ts(getattr(task, "until", "") or "")
    if end is not None and ts > end:
        raise ValueError("已超过结束日期")
    return ts


def validate_task_fields(
    *,
    kind: str,
    at: float = 0,
    time_str: str = "",
    weekdays: Optional[list[int]] = None,
    interval_minutes: int = 0,
    title: str = "",
    prompt: str = "",
    until: str = "",
    month_day: int = 0,
) -> None:
    """Raise ValueError unless the schedule kind is fully specified."""
    if (title or "").strip() == "":
        raise ValueError("任务标题不能为空")
    if len(title) > 120:
        raise ValueError("任务标题过长（≤120 字）")
    if (prompt or "").strip() == "":
        raise ValueError("任务指令不能为空")
    if len(prompt) > 8000:
        raise ValueError("任务指令过长（≤8000 字）")
    if kind not in KINDS:
        raise ValueError(f"不支持的定时类型 {kind!r}（可选 once/daily/weekly/interval/monthly）")
    if until:
        parse_until(until)
    if kind == "once":
        if not at or at <= _time.time():
            raise ValueError("一次性任务必须指定将来的时间（at）")
    elif kind == "interval":
        if not 5 <= int(interval_minutes or 0) <= 10080:
            raise ValueError("间隔必须在 5 分钟到 7 天之间")
    else:
        parse_hhmm(time_str)
        if kind == "weekly":
            days = sorted({int(w) for w in (weekdays or []) if 0 <= int(w) <= 6})
            if not days:
                raise ValueError("每周任务至少要选择一个星期（0=周一 … 6=周日）")
        if kind == "monthly" and not 1 <= int(month_day or 0) <= 31:
            raise ValueError("每月任务要指定几号（month_day，1-31）")


def _with_until(text: str, task: "ScheduledTask") -> str:
    until = (getattr(task, "until", "") or "").strip()
    return f"{text} 至 {until}" if until else text


def schedule_text(task: "ScheduledTask") -> str:
    """Human-readable one-liner: 每天 09:00 / 每周一、周三 09:00 / 每 30 分钟 / 2026-10-05 15:00."""
    if task.kind == "once":
        return datetime.fromtimestamp(task.at).strftime("%Y-%m-%d %H:%M") if task.at else "一次性"
    if task.kind == "interval":
        minutes = int(task.interval_minutes or 0)
        base = f"每 {minutes} 分钟" if minutes < 60 else f"每 {minutes // 60} 小时" + (f" {minutes % 60} 分" if minutes % 60 else "")
        return _with_until(base, task)
    hh, mm = parse_hhmm(task.time or "")
    clock = f"{hh:02d}:{mm:02d}"
    if task.kind == "daily":
        return _with_until(f"每天 {clock}", task)
    if task.kind == "monthly":
        return _with_until(f"每月{int(task.month_day or 1)}日 {clock}", task)
    days = sorted({int(w) for w in (task.weekdays or []) if 0 <= int(w) <= 6})
    return _with_until("每" + "、".join(_WEEKDAY_NAMES[w] for w in days) + f" {clock}", task)


def compute_next_run(task: "ScheduledTask", *, after: float) -> float:
    """Next trigger strictly after `after` (epoch seconds, local timezone)."""
    kind = task.kind
    if kind == "once":
        return float(task.at or 0)
    if kind == "interval":
        return _within_until(task, float(after) + max(5, int(task.interval_minutes or 5)) * 60)
    hh, mm = parse_hhmm(task.time or "")
    base = datetime.fromtimestamp(float(after)).astimezone()
    candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "daily":
        ts = candidate.timestamp() if candidate.timestamp() > after else (candidate + timedelta(days=1)).timestamp()
        return _within_until(task, ts)
    if kind == "weekly":
        days = sorted({int(w) for w in (task.weekdays or []) if 0 <= int(w) <= 6})
        if not days:
            raise ValueError("每周任务至少要选择一个星期")
        for offset in range(8):
            day = (candidate + timedelta(days=offset)).replace(
                hour=hh, minute=mm, second=0, microsecond=0
            )
            if day.weekday() not in days:
                continue
            ts = day.timestamp()
            if ts > after:
                return _within_until(task, ts)
    if kind == "monthly":
        want = int(getattr(task, "month_day", 0) or 0)
        if not 1 <= want <= 31:
            raise ValueError("每月任务要指定几号（1-31）")
        year, month = base.year, base.month
        for _ in range(14):
            last = calendar.monthrange(year, month)[1]
            day = min(want, last)
            slot = base.replace(year=year, month=month, day=day, hour=hh, minute=mm, second=0, microsecond=0)
            ts = slot.timestamp()
            if ts > after:
                return _within_until(task, ts)
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
    raise ValueError(f"无法计算 {kind!r} 的下次运行时间")


@dataclass
class ScheduledTask:
    id: str
    user_id: str
    session_id: str
    title: str
    prompt: str
    kind: str  # once | daily | weekly | interval | monthly
    enabled: bool = True
    at: float = 0.0  # once: local epoch
    time: str = ""  # daily/weekly/monthly: "HH:MM"
    weekdays: list[int] = field(default_factory=list)  # weekly: 0=Mon..6=Sun
    interval_minutes: int = 0  # interval: >=5
    until: str = ""  # inclusive end date YYYY-MM-DD for repeating kinds
    month_day: int = 0  # monthly: 1-31 (clamped to the month's last day)
    created_at: float = field(default_factory=_time.time)
    next_run: float = 0.0
    last_run: Optional[float] = None
    last_status: str = ""  # ok | error | missed | orphan
    last_error: str = ""
    defer_count: int = 0
    run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["weekdays"] = list(self.weekdays)
        data["schedule_text"] = schedule_text(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledTask":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        weekdays = known.get("weekdays")
        if isinstance(weekdays, list):
            known["weekdays"] = [int(w) for w in weekdays if 0 <= int(w) <= 6]
        known["at"] = float(known.get("at") or 0)
        known["next_run"] = float(known.get("next_run") or 0)
        known["enabled"] = bool(known.get("enabled", True))
        if "until" in known:
            known["until"] = str(known.get("until") or "")
        if "month_day" in known:
            known["month_day"] = int(known.get("month_day") or 0)
        return cls(**known)


class ScheduleManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, ScheduledTask] = {}

    # -- persistence --------------------------------------------------

    def load_all(self) -> None:
        """Startup recovery: load every tenant file and recompute schedules."""
        now = _time.time()
        loaded = 0
        with self._lock:
            for path in tenants_root().glob("*/schedules.json"):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log_exception(_log, f"read {path.name} failed", exc)
                    continue
                user_id = path.parent.name
                for item in raw.get("tasks", []) if isinstance(raw, dict) else []:
                    try:
                        task = ScheduledTask.from_dict(item)
                        task.user_id = task.user_id or user_id
                    except Exception as exc:
                        log_exception(_log, f"bad schedule entry in {path.name}", exc)
                        continue
                    self._recover_task(task, now)
                    self._tasks[task.id] = task
                    loaded += 1
            dirty_users = {t.user_id for t in self._tasks.values() if t.enabled}
        for uid in dirty_users:
            self._persist_user(uid)
        if loaded:
            _log.info("scheduler: recovered %d task(s)", loaded)

    def _recover_task(self, task: ScheduledTask, now: float) -> None:
        if not task.enabled:
            return
        if task.kind == "once":
            if task.at > now:
                task.next_run = task.at
            elif now - task.at <= CATCHUP_WINDOW_SEC:
                task.next_run = now + 10  # catch-up run shortly after startup
            else:
                task.enabled = False
                task.last_status = "missed"
                task.last_error = "停机期间错过，已超过补跑窗口"
                task.next_run = 0
            return
        if task.next_run <= now:
            try:
                task.next_run = compute_next_run(task, after=now)
            except ValueError:
                task.enabled = False
                task.next_run = 0

    def _persist_user(self, user_id: str) -> None:
        try:
            path = tenant_schedules_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                tasks = sorted(
                    (t.to_dict() for t in self._tasks.values() if t.user_id == user_id),
                    key=lambda t: t.get("created_at") or 0,
                )
            path.write_text(
                json.dumps({"version": 1, "tasks": tasks}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log_exception(_log, f"persist schedules failed for {user_id}", exc)

    # -- queries ------------------------------------------------------

    def list(self, user_id: str) -> list[ScheduledTask]:
        with self._lock:
            tasks = [t for t in self._tasks.values() if t.user_id == user_id]
        tasks.sort(key=lambda t: (t.next_run or float("inf"), t.created_at))
        return tasks

    def get(self, task_id: str, user_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            task = self._tasks.get((task_id or "").strip())
        if not task or task.user_id != (user_id or ""):
            return None
        return task

    def due(self, now: float) -> list[ScheduledTask]:
        with self._lock:
            return [t for t in self._tasks.values() if t.enabled and 0 < t.next_run <= now]

    def upcoming(self, task: ScheduledTask, n: int = 3) -> list[float]:
        if not task.enabled or task.next_run <= 0:
            return []
        out: list[float] = []
        try:
            cursor = float(task.next_run)
            for _ in range(max(1, int(n))):
                out.append(cursor)
                if task.kind == "once":
                    break
                cursor = compute_next_run(task, after=cursor)
        except ValueError:
            pass
        return out

    # -- mutations ----------------------------------------------------

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
        prompt: str,
        kind: str,
        at: float = 0,
        time_str: str = "",
        weekdays: Optional[list[int]] = None,
        interval_minutes: int = 0,
        enabled: bool = True,
        until: str = "",
        month_day: int = 0,
    ) -> ScheduledTask:
        clean_days = sorted({int(w) for w in (weekdays or []) if 0 <= int(w) <= 6})
        clean_until = parse_until(until) if kind != "once" else ""
        validate_task_fields(
            kind=kind,
            at=at,
            time_str=time_str,
            weekdays=clean_days,
            interval_minutes=interval_minutes,
            title=title,
            prompt=prompt,
            until=clean_until,
            month_day=month_day,
        )
        with self._lock:
            if sum(1 for t in self._tasks.values() if t.user_id == user_id) >= LIMIT_PER_USER:
                raise ValueError(f"每个用户最多 {LIMIT_PER_USER} 个定时任务")
            task = ScheduledTask(
                id=new_id("sch"),
                user_id=user_id,
                session_id=(session_id or "").strip(),
                title=title.strip(),
                prompt=prompt.strip(),
                kind=kind,
                enabled=bool(enabled),
                at=float(at or 0),
                time=(time_str or "").strip(),
                weekdays=clean_days,
                interval_minutes=int(interval_minutes or 0),
                until=clean_until,
                month_day=int(month_day or 0),
            )
            task.created_at = _time.time()
            if kind == "once":
                task.next_run = task.at
            else:
                task.next_run = compute_next_run(task, after=_time.time())
            self._tasks[task.id] = task
        self._persist_user(user_id)
        return task

    def patch(self, task_id: str, user_id: str, changes: dict[str, Any]) -> ScheduledTask:
        task = self.get(task_id, user_id)
        if not task:
            raise ValueError("任务不存在")
        with self._lock:
            recompute = False
            if "title" in changes and str(changes["title"] or "").strip():
                task.title = str(changes["title"]).strip()[:120]
            if "prompt" in changes and str(changes["prompt"] or "").strip():
                task.prompt = str(changes["prompt"]).strip()[:8000]
            if "kind" in changes and changes["kind"] in KINDS and changes["kind"] != task.kind:
                task.kind = str(changes["kind"])
                recompute = True
            if "at" in changes and changes["at"]:
                task.at = parse_local_dt(changes["at"])
                recompute = True
            if "time" in changes and changes["time"]:
                parse_hhmm(changes["time"])
                task.time = str(changes["time"]).strip()
                recompute = True
            if "weekdays" in changes and changes["weekdays"] is not None:
                days = sorted({int(w) for w in changes["weekdays"] if 0 <= int(w) <= 6})
                if not days:
                    raise ValueError("每周任务至少要选择一个星期")
                task.weekdays = days
                recompute = True
            if "interval_minutes" in changes and changes["interval_minutes"]:
                minutes = int(changes["interval_minutes"])
                if not 5 <= minutes <= 10080:
                    raise ValueError("间隔必须在 5 分钟到 7 天之间")
                task.interval_minutes = minutes
                recompute = True
            if "until" in changes:
                task.until = parse_until(str(changes["until"] or "")) if task.kind != "once" else ""
                recompute = True
            if "month_day" in changes and changes["month_day"]:
                task.month_day = int(changes["month_day"])
                recompute = True
            validate_task_fields(
                kind=task.kind,
                at=task.at,
                time_str=task.time,
                weekdays=task.weekdays,
                interval_minutes=task.interval_minutes,
                title=task.title,
                prompt=task.prompt,
                until=task.until,
                month_day=task.month_day,
            )
            if "enabled" in changes:
                want = bool(changes["enabled"])
                if want and not task.enabled:
                    recompute = True
                task.enabled = want
            now = _time.time()
            if recompute and task.enabled:
                if task.kind == "once":
                    if task.at <= now:
                        raise ValueError("一次性任务的时间必须是将来")
                    task.next_run = task.at
                else:
                    task.next_run = compute_next_run(task, after=now)
                task.defer_count = 0
        self._persist_user(user_id)
        return task

    def delete(self, task_id: str, user_id: str) -> bool:
        task = self.get(task_id, user_id)
        if not task:
            return False
        with self._lock:
            self._tasks.pop(task.id, None)
        self._persist_user(user_id)
        return True

    def advance(self, task: ScheduledTask, now: float) -> None:
        """Recurring task finished a run — anchor next_run at the completion time."""
        with self._lock:
            if task.kind == "once":
                task.enabled = False
                task.next_run = 0
                task.defer_count = 0
            else:
                try:
                    task.next_run = compute_next_run(task, after=now)
                except ValueError as exc:
                    task.enabled = False
                    task.next_run = 0
                    task.last_status = "error"
                    task.last_error = str(exc)
                task.defer_count = 0
        self._persist_user(task.user_id)

    def defer(self, task: ScheduledTask, until: float, note: str = "") -> None:
        with self._lock:
            task.next_run = float(until)
            task.defer_count = int(task.defer_count) + 1
            if note:
                task.last_error = note
        self._persist_user(task.user_id)

    def mark_result(self, task: ScheduledTask, *, ok: bool, error: str = "") -> None:
        with self._lock:
            task.last_run = _time.time()
            task.run_count = int(task.run_count) + 1
            task.last_status = "ok" if ok else "error"
            task.last_error = str(error or "")
        self._persist_user(task.user_id)

    def pause_orphan(self, task: ScheduledTask, reason: str) -> None:
        with self._lock:
            task.enabled = False
            task.next_run = 0
            task.last_status = "orphan"
            task.last_error = str(reason or "")
        self._persist_user(task.user_id)

    def resync_after_clock_change(self, now: float) -> None:
        """Wall clock jumped backwards — recompute recurring next_run values."""
        with self._lock:
            affected = {
                t.user_id
                for t in self._tasks.values()
                if t.enabled and t.kind != "once"
            }
            for t in self._tasks.values():
                if not t.enabled or t.kind == "once":
                    continue
                try:
                    t.next_run = compute_next_run(t, after=now)
                except ValueError:
                    t.enabled = False
                    t.next_run = 0
        for uid in affected:
            self._persist_user(uid)

    def pause_for_session(self, user_id: str, session_id: str) -> int:
        """Disable every task bound to a (deleted) session. Returns count."""
        sid = (session_id or "").strip()
        if not sid:
            return 0
        with self._lock:
            affected = [
                t
                for t in self._tasks.values()
                if t.user_id == user_id and t.session_id == sid and t.enabled
            ]
            for t in affected:
                t.enabled = False
                t.next_run = 0
                t.last_status = "orphan"
                t.last_error = "绑定的会话已删除"
        if affected:
            self._persist_user(user_id)
        return len(affected)


SCHEDULES = ScheduleManager()
