from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from metateam.services.scheduler import (
    ScheduledTask,
    compute_next_run,
    once_at_from_relative_text,
    parse_hhmm,
    parse_local_dt,
    resolve_once_at,
    schedule_text,
    validate_task_fields,
)


def _at(hour: int, minute: int = 0) -> float:
    """Explicit local wall-clock time today."""
    return (
        datetime.now().astimezone()
        .replace(hour=hour, minute=minute, second=0, microsecond=0)
        .timestamp()
    )


def _ts(**kwargs: int) -> float:
    """Now + relative offset."""
    import time as _time

    return _time.time() + timedelta(**kwargs).total_seconds()


def _task(**overrides) -> ScheduledTask:
    base = dict(
        id="sch_x",
        user_id="default",
        session_id="sess_x",
        title="t",
        prompt="p",
        kind="daily",
        time="09:00",
    )
    base.update(overrides)
    return ScheduledTask(**base)


def test_parse_hhmm_valid_and_invalid() -> None:
    assert parse_hhmm("09:05") == (9, 5)
    assert parse_hhmm("23:59") == (23, 59)
    with pytest.raises(ValueError):
        parse_hhmm("9:05")
    with pytest.raises(ValueError):
        parse_hhmm("24:00")
    with pytest.raises(ValueError):
        parse_hhmm("abc")


def test_parse_local_dt() -> None:
    ts = parse_local_dt("2030-01-02 08:30")
    dt = datetime.fromtimestamp(ts)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2030, 1, 2, 8, 30)
    with pytest.raises(ValueError):
        parse_local_dt("not-a-date")


def test_daily_next_run_before_and_after_time() -> None:
    # 08:00 local now → today 09:00 (1-2h ahead)
    after = _at(8)
    nxt = compute_next_run(_task(time="09:00"), after=after)
    assert after < nxt <= after + 2 * 3600

    # 10:00 local now → tomorrow 09:00 (22-24h ahead)
    after = _at(10)
    nxt = compute_next_run(_task(time="09:00"), after=after)
    assert after + 22 * 3600 < nxt <= after + 24 * 3600


def test_daily_crosses_midnight() -> None:
    # 23:30 local now, daily 00:10 → tomorrow 00:10 (≤40min ahead)
    after = _at(23, 30)
    nxt = compute_next_run(_task(time="00:10"), after=after)
    assert 0 < nxt - after <= 3600


def test_weekly_picks_next_matching_weekday() -> None:
    after = datetime.now().astimezone().replace(second=0, microsecond=0).timestamp()
    base_dt = datetime.fromtimestamp(after)
    nxt = compute_next_run(_task(kind="weekly", time="09:00", weekdays=[0]), after=after)
    delta_days = (datetime.fromtimestamp(nxt).date() - base_dt.date()).days
    assert datetime.fromtimestamp(nxt).weekday() == 0
    assert 1 <= delta_days <= 7
    assert nxt > after

    # multiple weekdays: next run must be one of them and within 7 days
    nxt2 = compute_next_run(_task(kind="weekly", time="23:59", weekdays=[0, 4, 6]), after=after)
    assert datetime.fromtimestamp(nxt2).weekday() in (0, 4, 6)
    assert nxt2 <= after + 7 * 86400 + 60


def test_interval_anchors_on_after() -> None:
    after = _ts(minutes=17)
    assert compute_next_run(_task(kind="interval", interval_minutes=30), after=after) == pytest.approx(
        after + 1800
    )


def test_once_returns_at_verbatim() -> None:
    at = _ts(hours=2)
    assert compute_next_run(_task(kind="once", at=at), after=_ts()) == at


def test_schedule_text() -> None:
    assert schedule_text(_task(kind="daily", time="09:00")) == "每天 09:00"
    assert schedule_text(_task(kind="weekly", time="08:15", weekdays=[0, 2])) == "每周一、周三 08:15"
    assert schedule_text(_task(kind="interval", interval_minutes=30)) == "每 30 分钟"
    assert schedule_text(_task(kind="interval", interval_minutes=120)) == "每 2 小时"
    once = _task(kind="once", at=parse_local_dt("2030-05-06 07:08"))
    assert schedule_text(once) == "2030-05-06 07:08"


def test_monthly_lands_on_the_day() -> None:
    after = datetime.now().astimezone().timestamp()
    nxt = compute_next_run(_task(kind="monthly", time="09:00", month_day=15), after=after)
    got = datetime.fromtimestamp(nxt)
    assert got.day == 15
    assert got.hour == 9
    assert nxt > after


def test_interval_until_clock_is_still_ahead() -> None:
    from metateam.services.scheduler import align_schedule_request, until_end_ts

    kind, until, minutes = align_schedule_request(
        "daily",
        datetime.now().strftime("%Y-%m-%d"),
        0,
        "\u6bcf\u4e94\u5206\u949f\u544a\u8bc9\u6211\u5f53\u524d\u76ee\u5f55\uff0c\u6301\u7eed\u5230\u660e\u5929\u4e0a\u5348\u4e5d\u70b9",
    )
    assert kind == "interval"
    assert minutes == 5
    assert until.endswith(" 09:00")
    end = until_end_ts(until)
    assert end is not None and end > datetime.now().timestamp()
    nxt = compute_next_run(
        _task(kind="interval", interval_minutes=5, until=until),
        after=datetime.now().timestamp(),
    )
    assert nxt <= end


def test_daily_stops_after_until() -> None:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    with pytest.raises(ValueError):
        compute_next_run(_task(kind="daily", time="09:00", until=yesterday), after=_ts())


def test_schedule_text_until_and_monthly() -> None:
    assert schedule_text(_task(kind="daily", time="09:00", until="2030-06-01")) == "每天 09:00 至 2030-06-01"
    assert schedule_text(_task(kind="monthly", time="09:00", month_day=3)) == "每月3日 09:00"


def test_validate_task_fields() -> None:
    validate_task_fields(kind="daily", time_str="09:00", title="t", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="daily", time_str="bad", title="t", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="weekly", time_str="09:00", weekdays=[], title="t", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="interval", interval_minutes=2, title="t", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="once", at=_ts() - 10, title="t", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="daily", time_str="09:00", title="", prompt="p")
    with pytest.raises(ValueError):
        validate_task_fields(kind="monthly", time_str="09:00", title="t", prompt="p")

def test_relative_once_at_today_before_and_after() -> None:
    from datetime import timezone as _tz

    from metateam.services.scheduler import once_at_from_relative_text, resolve_once_at

    # UTC+8 stands in for Asia/Shanghai without requiring the tzdata package.
    zone = _tz(timedelta(hours=8))
    tz = "Asia/Shanghai"
    # Same local morning: 今天 09:15 must stay on that calendar day.
    now = datetime(2026, 10, 27, 9, 0, tzinfo=zone)
    got = once_at_from_relative_text("请设置今天9点15分的定时任务", now=now, tz=tz)
    assert got == "2026-10-27 09:15"
    # Model wrongly filled tomorrow — resolve_once_at must correct it.
    assert (
        resolve_once_at("2026-10-28 09:15", "今天 09:15 提醒我", now=now, tz=tz)
        == "2026-10-27 09:15"
    )
    # After the slot → roll to next day.
    later = datetime(2026, 10, 27, 9, 20, tzinfo=zone)
    assert once_at_from_relative_text("今天9:15", now=later, tz=tz) == "2026-10-28 09:15"
    assert once_at_from_relative_text("9点15分提醒我", now=later, tz=tz) == "2026-10-28 09:15"


def test_relative_once_at_timezone_boundary_utc_evening() -> None:
    """UTC evening can still be Shanghai morning — stay on Shanghai's today."""
    from datetime import timezone as _tz

    from metateam.services.scheduler import once_at_from_relative_text, parse_local_dt

    tz = "Asia/Shanghai"
    shanghai = _tz(timedelta(hours=8))
    # 2026-10-26 17:00 UTC == 2026-10-27 01:00 Asia/Shanghai
    now_utc = datetime(2026, 10, 26, 17, 0, tzinfo=_tz.utc)
    got = once_at_from_relative_text("今天上午9点15分", now=now_utc, tz=tz)
    assert got == "2026-10-27 09:15"
    ts = parse_local_dt(got, tz=tz)
    local = datetime.fromtimestamp(ts, tz=shanghai)
    assert (local.year, local.month, local.day, local.hour, local.minute) == (
        2026,
        10,
        27,
        9,
        15,
    )


def test_relative_once_at_tomorrow_fixed_offset() -> None:
    from datetime import timezone as _tz

    from metateam.services.scheduler import once_at_from_relative_text

    tz = "Asia/Shanghai"
    now = datetime(2026, 10, 27, 22, 0, tzinfo=_tz(timedelta(hours=8)))
    assert once_at_from_relative_text("明天下午3点", now=now, tz=tz) == "2026-10-28 15:00"
