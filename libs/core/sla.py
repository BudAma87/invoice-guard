"""Review SLA in business hours of the entity's calendar (TSD §8).

Normal 8 h, month-end window 4 h. Warn at 75 %, breach at 100 %, second escalation at
150 % (125 % in the month-end window). Holds pause the clock for up to 3 business days.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from libs.core.entities import Entity

HOUR = 3600


class SlaState(StrEnum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    ON_HOLD = "on_hold"


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5  # TODO: entity holiday calendar


def _business_days(year: int, month: int) -> list[date]:
    n = calendar.monthrange(year, month)[1]
    return [date(year, month, i) for i in range(1, n + 1) if _is_business_day(date(year, month, i))]


def in_month_end_window(ent: Entity, d: date) -> bool:
    days = _business_days(d.year, d.month)
    return d in days[-ent.month_end_last :] or d in days[: ent.month_end_first]


def sla_seconds(ent: Entity, at: datetime) -> int:
    return (4 if in_month_end_window(ent, ent.today(at)) else 8) * HOUR


def _day_bounds(ent: Entity, d: date) -> tuple[datetime, datetime]:
    base = datetime(d.year, d.month, d.day, tzinfo=ent.tz)
    return base + timedelta(hours=ent.open_h), base + timedelta(hours=ent.close_h)


def add_business_seconds(ent: Entity, start: datetime, seconds: float) -> datetime:
    t = start.astimezone(ent.tz)
    remaining = seconds
    while True:
        d = t.date()
        if _is_business_day(d):
            open_, close = _day_bounds(ent, d)
            if t < open_:
                t = open_
            if t < close:
                avail = (close - t).total_seconds()
                if remaining <= avail:
                    return t + timedelta(seconds=remaining)
                remaining -= avail
        nxt = d + timedelta(days=1)
        t = datetime(nxt.year, nxt.month, nxt.day, tzinfo=ent.tz)


def business_seconds_between(ent: Entity, a: datetime, b: datetime) -> float:
    if b <= a:
        return 0.0
    a, b = a.astimezone(ent.tz), b.astimezone(ent.tz)
    total = 0.0
    d = a.date()
    while d <= b.date():
        if _is_business_day(d):
            open_, close = _day_bounds(ent, d)
            lo, hi = max(a, open_), min(b, close)
            if hi > lo:
                total += (hi - lo).total_seconds()
        d += timedelta(days=1)
    return total


def used_ratio(ent: Entity, created: datetime, due: datetime, now: datetime) -> float:
    span = business_seconds_between(ent, created, due) or 1.0
    return business_seconds_between(ent, created, now) / span


def state_of(ratio: float, on_hold: bool = False) -> SlaState:
    if on_hold:
        return SlaState.ON_HOLD
    if ratio >= 1:
        return SlaState.BREACHED
    if ratio >= 0.75:
        return SlaState.AT_RISK
    return SlaState.ON_TRACK


def escalation_level(ent: Entity, ratio: float, at: datetime) -> int:
    """0 none, 1 warn assignee + lead, 2 breach -> supervisor pool, 3 finance manager."""
    second = 1.25 if in_month_end_window(ent, ent.today(at)) else 1.5
    if ratio >= second:
        return 3
    if ratio >= 1:
        return 2
    if ratio >= 0.75:
        return 1
    return 0


def hold_until(ent: Entity, now: datetime) -> datetime:
    """Max hold: 3 business days."""
    return add_business_seconds(ent, now, 3 * (ent.close_h - ent.open_h) * HOUR)


# --------------------------------------------------------------------------- per-task clock
# The SLA clock counts business time since the task was created, minus time on hold.
# These read only the task, so they are right even before the scheduler has run.


class _TaskLike(Protocol):
    entity_id: str
    created_at: datetime
    sla_seconds: int
    sla_paused_seconds: int
    hold_started_at: datetime | None
    hold_until: datetime | None


def on_hold(task: _TaskLike, now: datetime) -> bool:
    return bool(task.hold_until and task.hold_started_at and task.hold_until > now)


def paused_seconds(ent: Entity, task: _TaskLike, now: datetime) -> float:
    """Banked pause + the current (or expired but not yet banked) hold."""
    paused = float(task.sla_paused_seconds or 0)
    if task.hold_started_at and task.hold_until:
        paused += business_seconds_between(ent, task.hold_started_at, min(now, task.hold_until))
    return paused


def task_ratio(ent: Entity, task: _TaskLike, now: datetime) -> float:
    used = business_seconds_between(ent, task.created_at, now) - paused_seconds(ent, task, now)
    return max(0.0, used) / (task.sla_seconds or 1)


def task_due(ent: Entity, task: _TaskLike, now: datetime) -> datetime:
    """Due time, pushed out by every second spent on hold."""
    return add_business_seconds(
        ent, task.created_at, task.sla_seconds + paused_seconds(ent, task, now)
    )
