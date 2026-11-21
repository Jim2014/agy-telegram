"""Unit tests for gateway.scheduler (interval/cron parsing, heartbeats, persistence, and dispatch)."""

import asyncio
from datetime import datetime
import pytest

from gateway.scheduler import (
    TaskScheduler,
    compute_next_run,
    is_quiet_heartbeat_reply,
    parse_cron_field,
    parse_interval_seconds,
    parse_schedule_spec,
)


def test_parse_interval_and_cron_next_run():
    assert parse_interval_seconds("30m") == 1800
    assert parse_interval_seconds("every 6h") == 21600
    assert parse_interval_seconds("1d") == 86400

    with pytest.raises(ValueError):
        parse_interval_seconds("10s", min_seconds=60)

    with pytest.raises(ValueError):
        parse_schedule_spec("invalid spec")

    assert parse_schedule_spec("every 15m") == ("interval", "15m")
    assert parse_schedule_spec("0   8  *  *  1-5") == ("cron", "0 8 * * 1-5")

    # Sunday=7 normalizes to 0 per POSIX cron
    assert parse_cron_field("0,7", 0, 6, allow_seven_as_zero=True) == {0}

    # Monday 2026-06-15 07:50 -> next run for "0 8 * * 1-5" is Monday 08:00
    mon_ts = datetime(2026, 6, 15, 7, 50, 12).timestamp()
    next_mon = datetime.fromtimestamp(compute_next_run("0 8 * * 1-5", after_ts=mon_ts))
    assert (next_mon.year, next_mon.month, next_mon.day, next_mon.hour, next_mon.minute) == (2026, 6, 15, 8, 0)

    # Friday 2026-06-19 08:05 -> next weekday run skips Sat/Sun to Monday 2026-06-22 08:00
    fri_ts = datetime(2026, 6, 19, 8, 5, 0).timestamp()
    next_wkday = datetime.fromtimestamp(compute_next_run("0 8 * * 1-5", after_ts=fri_ts))
    assert (next_wkday.year, next_wkday.month, next_wkday.day, next_wkday.hour, next_wkday.minute) == (2026, 6, 22, 8, 0)


def test_is_quiet_heartbeat_reply():
    assert is_quiet_heartbeat_reply("HEARTBEAT_OK") is True
    assert is_quiet_heartbeat_reply("  `HEARTBEAT_OK`!  ") is True
    assert is_quiet_heartbeat_reply("[HEARTBEAT_OK].") is True
    assert is_quiet_heartbeat_reply("") is False
    assert is_quiet_heartbeat_reply("Disk usage at 95% on /") is False


def test_task_scheduler_persistence_due_pause_resume(tmp_path):
    sched_file = tmp_path / "schedules.json"
    sched = TaskScheduler(schedules_file=sched_file, min_interval_seconds=60)
    base_ts = 1_700_000_000.0

    t1 = sched.add_task(chat_id=100, project="admin", schedule="every 30m", prompt="Check repo", kind="cron", now=base_ts)
    t2 = sched.add_task(chat_id=200, project="news", schedule="0 9 * * *", prompt="Health ping", kind="heartbeat", now=base_ts)

    assert (sched_file.stat().st_mode & 0o777) == 0o600
    assert [t.task_id for t in sched.list_tasks(chat_id=100)] == [t1.task_id]
    assert len(sched.list_tasks()) == 2

    # Not due before interval elapses
    assert sched.due_tasks(now=base_ts + 1799) == []
    assert [t.task_id for t in sched.due_tasks(now=base_ts + 1800)] == [t1.task_id]

    # Pause suppresses due detection; resume recomputes next_run_at
    sched.set_enabled(t1.task_id, enabled=False, chat_id=100)
    assert sched.due_tasks(now=base_ts + 3600) == []
    sched.set_enabled(t1.task_id, enabled=True, chat_id=100, now=base_ts + 2000)
    assert sched.get_task(t1.task_id).next_run_at == base_ts + 2000 + 1800

    # Mark executed increments run_count & advances next_run_at, survives reload
    sched.mark_executed(t1.task_id, "SUCCESS", now=base_ts + 3800)
    reloaded = TaskScheduler(schedules_file=sched_file)
    rt1 = reloaded.get_task(t1.task_id)
    assert rt1 is not None and rt1.run_count == 1 and rt1.last_status == "SUCCESS"
    assert reloaded.remove_task(t2.task_id, chat_id=100) is False  # wrong chat_id rejected
    assert reloaded.remove_task(t2.task_id, chat_id=200) is True


@pytest.mark.asyncio
async def test_task_scheduler_async_loop_and_overlap_guard(tmp_path):
    sched = TaskScheduler(
        schedules_file=tmp_path / "loop_sched.json",
        poll_interval_seconds=0.05,
        min_interval_seconds=1,
    )
    now = 1_700_000_000.0
    task = sched.add_task(chat_id=100, project="default", schedule="10s", prompt="Ping", now=now - 20)

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def worker_cb(t):
        calls.append(t.task_id)
        started.set()
        await release.wait()
        sched.mark_executed(t.task_id, "SUCCESS")

    sched.start(worker_cb)
    await asyncio.wait_for(started.wait(), timeout=2.0)
    # Give poll loop multiple ticks while callback is still running -> overlap guard skips duplicate dispatch
    await asyncio.sleep(0.15)
    assert calls == [task.task_id]

    release.set()
    await asyncio.sleep(0.05)
    await sched.stop()
