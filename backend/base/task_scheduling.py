# -*- coding: utf-8 -*-

"""Pure recurrence helpers for scheduled tasks."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Optional

from backend.base.definitions import TaskSchedule


@dataclass(frozen=True)
class ScheduleSpec:
    """The persisted values that define one task recurrence."""

    schedule_type: TaskSchedule
    interval_seconds: int = 0
    weekday: int = 0
    time_of_day: str = "03:00"


def _local_now(value: Optional[datetime] = None) -> datetime:
    """Return an aware datetime in the server's local timezone."""
    current = value or datetime.now().astimezone()
    if current.tzinfo is None:
        return current.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return current.astimezone()


def _scheduled_time(spec: ScheduleSpec) -> datetime_time:
    hour, minute = (int(part) for part in spec.time_of_day.split(":", 1))
    return datetime_time(hour, minute)


def schedule_interval_seconds(spec: ScheduleSpec) -> int:
    """Return the effective interval used by the task timer."""
    if spec.schedule_type is TaskSchedule.DISABLED:
        return 0
    if spec.schedule_type is TaskSchedule.INTERVAL:
        return spec.interval_seconds
    if spec.schedule_type is TaskSchedule.DAILY:
        return 24 * 60 * 60
    return 7 * 24 * 60 * 60


def next_schedule_run(
    spec: ScheduleSpec,
    now: Optional[datetime] = None
) -> int:
    """Return the next occurrence strictly after ``now`` as a Unix timestamp."""
    current = _local_now(now)
    if spec.schedule_type is TaskSchedule.DISABLED:
        return 0
    if spec.schedule_type is TaskSchedule.INTERVAL:
        return round(current.timestamp() + spec.interval_seconds)

    scheduled_time = _scheduled_time(spec)
    scheduled = current.replace(
        hour=scheduled_time.hour,
        minute=scheduled_time.minute,
        second=0,
        microsecond=0
    )
    if spec.schedule_type is TaskSchedule.DAILY:
        if scheduled <= current:
            scheduled += timedelta(days=1)
    else:
        days_ahead = (spec.weekday - current.weekday()) % 7
        scheduled += timedelta(days=days_ahead)
        if scheduled <= current:
            scheduled += timedelta(days=7)

    return round(scheduled.timestamp())


def next_run_after_startup(
    spec: ScheduleSpec,
    now: Optional[datetime] = None
) -> int:
    """Return the next run after startup counts for the current period."""
    current = _local_now(now)
    if spec.schedule_type in (TaskSchedule.DISABLED, TaskSchedule.INTERVAL):
        return next_schedule_run(spec, current)

    next_run = next_schedule_run(spec, current)
    next_datetime = datetime.fromtimestamp(next_run, current.tzinfo)
    if spec.schedule_type is TaskSchedule.DAILY:
        next_datetime += timedelta(days=1)
    else:
        next_datetime += timedelta(days=7)
    return round(next_datetime.timestamp())