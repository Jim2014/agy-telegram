"""gateway/scheduler.py — Proactive Cron & Heartbeat Task Scheduler for agy-telegram."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r"^(?:every\s+)?(\d+)\s*([smhd])$", re.IGNORECASE)
_QUIET_HEARTBEAT_RE = re.compile(r"^[\[\(\*_`\s]*HEARTBEAT_OK[\]\)\*_`\.!\s]*$", re.IGNORECASE)


def parse_interval_seconds(spec: str, min_seconds: int = 60) -> Optional[int]:
    """Parse interval notation such as '30m', 'every 6h', or '1d' into seconds."""
    m = _INTERVAL_RE.match((spec or "").strip())
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * mult
    if seconds < min_seconds:
        raise ValueError(f"Interval '{spec.strip()}' is below the minimum allowed ({min_seconds}s).")
    return seconds


def parse_cron_field(
    field: str,
    min_val: int,
    max_val: int,
    allow_seven_as_zero: bool = False,
) -> Set[int]:
    """Parse a single 5-field cron component into a set of matching integers."""
    raw = (field or "").strip()
    if not raw:
        raise ValueError("Empty cron field.")

    result: Set[int] = set()
    upper_bound = 7 if allow_seven_as_zero else max_val

    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Invalid empty list item in cron field '{field}'.")

        step = 1
        if "/" in part:
            pieces = part.split("/", 1)
            base_str, step_str = pieces[0].strip(), pieces[1].strip()
            if not step_str.isdigit() or int(step_str) <= 0:
                raise ValueError(f"Invalid step '{part}' in cron field '{field}'.")
            step = int(step_str)
        else:
            base_str = part

        if base_str == "*":
            start, end = min_val, max_val
        elif "-" in base_str:
            bounds = base_str.split("-", 1)
            if not (bounds[0].strip().isdigit() and bounds[1].strip().isdigit()):
                raise ValueError(f"Invalid range '{part}' in cron field '{field}'.")
            start, end = int(bounds[0].strip()), int(bounds[1].strip())
            if start > end or start < min_val or end > upper_bound:
                raise ValueError(f"Range out of bounds '{part}' ({min_val}..{upper_bound}).")
        elif base_str.isdigit():
            val = int(base_str)
            if val < min_val or val > upper_bound:
                raise ValueError(f"Value {val} out of bounds ({min_val}..{upper_bound}).")
            if allow_seven_as_zero and val == 7:
                val = 0
            result.add(val)
            continue
        else:
            raise ValueError(f"Invalid cron token '{part}' in field '{field}'.")

        for val in range(start, end + 1, step):
            norm = 0 if (allow_seven_as_zero and val == 7) else val
            result.add(norm)

    if not result:
        raise ValueError(f"Cron field '{field}' matched no values.")
    return result


def parse_schedule_spec(spec: str, min_interval_seconds: int = 60) -> Tuple[str, str]:
    """Validate an interval ('30m', 'every 6h') or 5-field cron expression ('0 8 * * *')."""
    cleaned = " ".join((spec or "").strip().split())
    if not cleaned:
        raise ValueError("Schedule specification cannot be empty.")

    interval = parse_interval_seconds(cleaned, min_seconds=min_interval_seconds)
    if interval is not None:
        m = _INTERVAL_RE.match(cleaned)
        assert m is not None
        return "interval", f"{int(m.group(1))}{m.group(2).lower()}"

    parts = cleaned.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid schedule '{spec}'. Use an interval like '30m' / '6h' or 5-field cron like '0 8 * * *'."
        )

    parse_cron_field(parts[0], 0, 59)
    parse_cron_field(parts[1], 0, 23)
    parse_cron_field(parts[2], 1, 31)
    parse_cron_field(parts[3], 1, 12)
    parse_cron_field(parts[4], 0, 6, allow_seven_as_zero=True)
    return "cron", " ".join(parts)


def compute_next_run(
    spec: str,
    after_ts: Optional[float] = None,
    min_interval_seconds: int = 60,
) -> float:
    """Compute the next execution epoch timestamp after after_ts for an interval or cron spec."""
    base = time.time() if after_ts is None else float(after_ts)
    kind, normalized = parse_schedule_spec(spec, min_interval_seconds=min_interval_seconds)
    if kind == "interval":
        seconds = parse_interval_seconds(normalized, min_seconds=min_interval_seconds)
        assert seconds is not None
        return base + seconds

    fields = normalized.split()
    minutes = parse_cron_field(fields[0], 0, 59)
    hours = parse_cron_field(fields[1], 0, 23)
    days = parse_cron_field(fields[2], 1, 31)
    months = parse_cron_field(fields[3], 1, 12)
    dows = parse_cron_field(fields[4], 0, 6, allow_seven_as_zero=True)
    dom_wild = fields[2] == "*"
    dow_wild = fields[4] == "*"

    dt = datetime.fromtimestamp(base).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = dt + timedelta(days=366)

    while dt < limit:
        if dt.month not in months:
            if dt.month == 12:
                dt = dt.replace(year=dt.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                dt = dt.replace(month=dt.month + 1, day=1, hour=0, minute=0)
            continue

        # Standard Vixie/POSIX cron: Sunday=0 (datetime.weekday() is Monday=0..Sunday=6)
        cron_dow = (dt.weekday() + 1) % 7
        if not dom_wild and not dow_wild:
            day_ok = (dt.day in days) or (cron_dow in dows)
        else:
            day_ok = (dt.day in days) and (cron_dow in dows)

        if not day_ok:
            dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        if dt.hour not in hours:
            dt = (dt + timedelta(hours=1)).replace(minute=0)
            continue

        if dt.minute in minutes:
            return dt.timestamp()

        dt += timedelta(minutes=1)

    raise ValueError(f"No matching run time within 366 days for cron expression '{spec}'.")


def is_quiet_heartbeat_reply(text: str) -> bool:
    """Return True if a heartbeat response indicates healthy status with no notification needed."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return bool(_QUIET_HEARTBEAT_RE.fullmatch(cleaned))


@dataclass
class ScheduledTask:
    """Persistent definition and runtime metadata for a scheduled cron or heartbeat job."""

    task_id: str
    chat_id: int
    project: str
    schedule: str
    prompt: str
    enabled: bool = True
    kind: str = "cron"  # "cron" | "heartbeat"
    created_at: float = 0.0
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    last_status: str = ""
    run_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize ScheduledTask to JSON-compatible dict."""
        return {
            "task_id": self.task_id,
            "chat_id": self.chat_id,
            "project": self.project,
            "schedule": self.schedule,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "kind": self.kind,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "last_status": self.last_status,
            "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScheduledTask:
        """Deserialize ScheduledTask from dict."""
        return cls(
            task_id=str(data["task_id"]),
            chat_id=int(data["chat_id"]),
            project=str(data.get("project") or "default"),
            schedule=str(data["schedule"]),
            prompt=str(data["prompt"]),
            enabled=bool(data.get("enabled", True)),
            kind=str(data.get("kind") or "cron"),
            created_at=float(data.get("created_at", 0.0)),
            last_run_at=float(data.get("last_run_at", 0.0)),
            next_run_at=float(data.get("next_run_at", 0.0)),
            last_status=str(data.get("last_status", "")),
            run_count=int(data.get("run_count", 0)),
        )


TaskCallback = Callable[[ScheduledTask], Awaitable[None]]


class TaskScheduler:
    """Manages persistent cron/heartbeat tasks and dispatches due runs asynchronously."""

    def __init__(
        self,
        schedules_file: Optional[str | Path] = None,
        poll_interval_seconds: float = 15.0,
        min_interval_seconds: int = 60,
    ) -> None:
        self.schedules_file = Path(schedules_file) if schedules_file else Path("runtime/schedules.json")
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.min_interval_seconds = max(1, int(min_interval_seconds))
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running_task_ids: Set[str] = set()
        self._worker_tasks: Set[asyncio.Task] = set()
        self._loop_task: Optional[asyncio.Task] = None
        self._callback: Optional[TaskCallback] = None
        self._is_running = False
        self.load()

    def load(self) -> None:
        """Load persisted scheduled tasks from JSON store if present."""
        if not self.schedules_file.is_file():
            self._tasks = {}
            return
        try:
            raw = json.loads(self.schedules_file.read_text(encoding="utf-8"))
            items = raw.get("tasks", []) if isinstance(raw, dict) else []
            loaded: Dict[str, ScheduledTask] = {}
            for item in items:
                if isinstance(item, dict) and "task_id" in item and "schedule" in item:
                    task = ScheduledTask.from_dict(item)
                    loaded[task.task_id] = task
            self._tasks = loaded
        except Exception as e:
            logger.warning("Failed to load schedules from %s: %s", self.schedules_file, e)
            self._tasks = {}

    def save(self) -> None:
        """Persist scheduled tasks atomically with restricted 0600 permissions."""
        try:
            self.schedules_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "tasks": [t.to_dict() for t in self._tasks.values()],
            }
            tmp_path = self.schedules_file.with_name(
                f".{self.schedules_file.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.schedules_file)
            try:
                os.chmod(self.schedules_file, 0o600)
            except OSError:
                pass
        except Exception as e:
            logger.warning("Failed to save schedules to %s: %s", self.schedules_file, e)

    def add_task(
        self,
        chat_id: int,
        project: str,
        schedule: str,
        prompt: str,
        kind: str = "cron",
        now: Optional[float] = None,
    ) -> ScheduledTask:
        """Validate schedule spec, create a new ScheduledTask, and persist it."""
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            raise ValueError("Task prompt cannot be empty.")

        _, normalized_spec = parse_schedule_spec(
            schedule,
            min_interval_seconds=self.min_interval_seconds,
        )
        norm_kind = "heartbeat" if (kind or "").strip().lower() == "heartbeat" else "cron"
        ts = time.time() if now is None else float(now)
        next_ts = compute_next_run(
            normalized_spec,
            after_ts=ts,
            min_interval_seconds=self.min_interval_seconds,
        )

        task_id = uuid.uuid4().hex[:8]
        while task_id in self._tasks:
            task_id = uuid.uuid4().hex[:8]

        task = ScheduledTask(
            task_id=task_id,
            chat_id=int(chat_id),
            project=(project or "default").strip() or "default",
            schedule=normalized_spec,
            prompt=clean_prompt,
            enabled=True,
            kind=norm_kind,
            created_at=ts,
            next_run_at=next_ts,
        )
        self._tasks[task_id] = task
        self.save()
        return task

    def list_tasks(self, chat_id: Optional[int] = None) -> List[ScheduledTask]:
        """Return scheduled tasks sorted by creation time (optionally filtered by chat_id)."""
        items = [
            t for t in self._tasks.values()
            if chat_id is None or t.chat_id == int(chat_id)
        ]
        items.sort(key=lambda t: (t.created_at, t.task_id))
        return items

    def get_task(self, task_id: str, chat_id: Optional[int] = None) -> Optional[ScheduledTask]:
        """Look up a task by ID (with optional chat ownership check)."""
        task = self._tasks.get((task_id or "").strip())
        if task is None:
            return None
        if chat_id is not None and task.chat_id != int(chat_id):
            return None
        return task

    def remove_task(self, task_id: str, chat_id: Optional[int] = None) -> bool:
        """Delete a task by ID. Returns True if removed."""
        task = self.get_task(task_id, chat_id=chat_id)
        if task is None:
            return False
        self._tasks.pop(task.task_id, None)
        self.save()
        return True

    def set_enabled(
        self,
        task_id: str,
        enabled: bool,
        chat_id: Optional[int] = None,
        now: Optional[float] = None,
    ) -> Optional[ScheduledTask]:
        """Pause or resume a scheduled task, recomputing next_run_at when resumed."""
        task = self.get_task(task_id, chat_id=chat_id)
        if task is None:
            return None
        task.enabled = bool(enabled)
        if task.enabled:
            ts = time.time() if now is None else float(now)
            task.next_run_at = compute_next_run(
                task.schedule,
                after_ts=ts,
                min_interval_seconds=self.min_interval_seconds,
            )
        self.save()
        return task

    def due_tasks(self, now: Optional[float] = None) -> List[ScheduledTask]:
        """Return all enabled tasks whose next_run_at <= now and are not currently running."""
        ts = time.time() if now is None else float(now)
        return [
            t for t in self._tasks.values()
            if t.enabled and t.next_run_at <= ts and t.task_id not in self._running_task_ids
        ]

    def mark_executed(
        self,
        task_id: str,
        status: str,
        now: Optional[float] = None,
    ) -> Optional[ScheduledTask]:
        """Update execution stats and advance next_run_at after a task completes."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        ts = time.time() if now is None else float(now)
        task.last_run_at = ts
        task.last_status = status
        task.run_count += 1
        try:
            task.next_run_at = compute_next_run(
                task.schedule,
                after_ts=ts,
                min_interval_seconds=self.min_interval_seconds,
            )
        except Exception as e:
            logger.warning("Failed to recompute next_run_at for task %s: %s", task_id, e)
            task.enabled = False
        self.save()
        return task

    async def run_pending_once(self, now: Optional[float] = None) -> int:
        """Check for due tasks and launch non-overlapping background callbacks."""
        if not self._callback:
            return 0
        due = self.due_tasks(now=now)
        for task in due:
            self._running_task_ids.add(task.task_id)
            worker = asyncio.create_task(self._run_task_wrapper(task))
            self._worker_tasks.add(worker)
            worker.add_done_callback(self._worker_tasks.discard)
        return len(due)

    async def _run_task_wrapper(self, task: ScheduledTask) -> None:
        """Invoke registered callback with overlap guard cleanup."""
        try:
            if self._callback:
                await self._callback(task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Scheduled task %s callback failed: %s", task.task_id, e, exc_info=True)
        finally:
            self._running_task_ids.discard(task.task_id)

    async def _poll_loop(self) -> None:
        """Background loop ticking every poll_interval_seconds to fire due tasks."""
        try:
            while self._is_running:
                await self.run_pending_once()
                await asyncio.sleep(self.poll_interval_seconds)
        except asyncio.CancelledError:
            pass

    def start(self, callback: TaskCallback) -> None:
        """Start the background scheduler polling loop if not already running."""
        self._callback = callback
        if self._is_running and self._loop_task and not self._loop_task.done():
            return
        self._is_running = True
        self._loop_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background scheduler loop and cancel any in-flight workers cleanly."""
        self._is_running = False
        tasks_to_cancel: List[asyncio.Task] = []
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            tasks_to_cancel.append(self._loop_task)
        for wt in list(self._worker_tasks):
            if not wt.done():
                wt.cancel()
                tasks_to_cancel.append(wt)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._loop_task = None
        self._worker_tasks.clear()
        self._running_task_ids.clear()


def _run_self_test() -> None:
    """Execute built-in self-test verifying cron/interval parsing, persistence, and dispatch."""
    base_ts = datetime(2026, 6, 15, 7, 50, 12).timestamp()  # Monday 07:50:12
    assert parse_interval_seconds("every 30m") == 1800
    next_cron = compute_next_run("0 8 * * 1-5", after_ts=base_ts)
    next_dt = datetime.fromtimestamp(next_cron)
    assert (next_dt.hour, next_dt.minute) == (8, 0)
    assert is_quiet_heartbeat_reply("HEARTBEAT_OK") is True
    assert is_quiet_heartbeat_reply("[HEARTBEAT_OK].") is True
    assert is_quiet_heartbeat_reply("Alert: disk full") is False

    with tempfile.TemporaryDirectory() as tmpd:
        sf = Path(tmpd) / "schedules.json"
        sched = TaskScheduler(schedules_file=sf, poll_interval_seconds=0.1, min_interval_seconds=60)
        t = sched.add_task(chat_id=100, project="admin", schedule="30m", prompt="Check repo status", now=base_ts)
        assert len(sched.list_tasks(100)) == 1
        assert (sf.stat().st_mode & 0o777) == 0o600
        assert sched.due_tasks(now=base_ts + 1801) == [t]
        sched.mark_executed(t.task_id, "SUCCESS", now=base_ts + 1801)
        reloaded = TaskScheduler(schedules_file=sf)
        assert reloaded.get_task(t.task_id) is not None
        assert reloaded.get_task(t.task_id).run_count == 1
        assert reloaded.remove_task(t.task_id) is True

    print("TaskScheduler self-test passed!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage agy-telegram scheduled cron and heartbeat tasks")
    parser.add_argument("--file", default="runtime/schedules.json", help="Path to schedules.json")
    parser.add_argument("--self-test", action="store_true", help="Run built-in scheduler self-test")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("list", help="List scheduled tasks")

    p_add = sub.add_parser("add", help="Add a scheduled task")
    p_add.add_argument("--chat-id", type=int, required=True)
    p_add.add_argument("--project", default="default")
    p_add.add_argument("--kind", choices=("cron", "heartbeat"), default="cron")
    p_add.add_argument("schedule", help="Interval (e.g. '30m') or 5-field cron (e.g. '0 8 * * *')")
    p_add.add_argument("prompt", help="Task prompt to execute")

    p_rm = sub.add_parser("remove", help="Remove a scheduled task by ID")
    p_rm.add_argument("task_id", help="8-char task ID")

    args = parser.parse_args()
    if args.self_test or not args.action:
        _run_self_test()
        return

    sched = TaskScheduler(schedules_file=args.file)
    if args.action == "list":
        tasks = sched.list_tasks()
        print(f"Scheduled tasks ({len(tasks)}):")
        for t in tasks:
            state = "enabled" if t.enabled else "paused"
            print(f"  • [{t.task_id}] ({t.kind}, {state}) proj={t.project} sched='{t.schedule}' -> {t.prompt}")
    elif args.action == "add":
        task = sched.add_task(
            chat_id=args.chat_id,
            project=args.project,
            schedule=args.schedule,
            prompt=args.prompt,
            kind=args.kind,
        )
        print(f"✅ Added task [{task.task_id}] ({task.kind}) on project '{task.project}' ({task.schedule}).")
    elif args.action == "remove":
        if sched.remove_task(args.task_id):
            print(f"🗑️ Removed task [{args.task_id}].")
        else:
            print(f"❌ Task '{args.task_id}' not found.")
            sys.exit(1)


if __name__ == "__main__":
    main()
