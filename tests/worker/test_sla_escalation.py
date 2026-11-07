"""SLA scheduler: business-hour clock, holds, escalation levels, supervisor pool, idempotency.

Entity E01 works 08:30-17:30 Asia/Colombo (UTC+5:30), Mon-Fri. Review SLA = 8 business hours,
4 in the month-end window (last 3 / first 2 business days).
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from libs.core import sla
from libs.core.entities import SLT, entity
from libs.core.escalation import LogNotifier, deliver_pending, sla_tick
from libs.core.review import Forbidden, User, claim, next_task
from libs.db import models as m
from tests.conftest import Env

REVIEWER = User("n.silva", "Nimali", frozenset({"reviewer"}), frozenset({"E01"}))
SUPERVISOR = User(
    "a.wijesinghe", "Anushka", frozenset({"supervisor", "reviewer"}), frozenset({"E01"})
)


def at(day: str, hhmm: str) -> datetime:
    """Local Colombo wall-clock time."""
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=SLT)


async def make_task(env: Env, created: datetime, key: str = "low-conf") -> str:
    """A real review task, re-dated so its SLA clock starts at `created`."""
    inv = await env.run(key)
    with env.factory() as s:
        t = s.scalar(
            select(m.ReviewTask).where(
                m.ReviewTask.invoice_id == inv.invoice_id, m.ReviewTask.status == "OPEN"
            )
        )
        ent = entity(t.entity_id)
        t.created_at = created
        t.sla_seconds = sla.sla_seconds(ent, created)
        t.sla_due_at = sla.add_business_seconds(ent, created, t.sla_seconds)
        t.escalation_level, t.pool = 0, None
        s.commit()
        return t.task_id


def tick(env: Env, now: datetime):
    with env.factory() as s:
        rep = sla_tick(s, now)
        s.commit()
        return rep


def task(env: Env, task_id: str) -> m.ReviewTask:
    with env.factory() as s:
        return s.get(m.ReviewTask, task_id)


def notes(env: Env, task_id: str) -> list[str]:
    with env.factory() as s:
        return [
            n.kind
            for n in s.scalars(
                select(m.Notification)
                .where(m.Notification.task_id == task_id)
                .order_by(m.Notification.id)
            )
        ]


async def test_clock_counts_business_hours_only(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))  # Wed
    t = task(env, tid)
    assert t.sla_seconds == 8 * 3600
    assert sla.task_due(entity("E01"), t, at("2026-11-11", "09:00")) == at("2026-11-11", "16:30")
    # Overnight and the weekend don't count: Fri 17:30 -> Mon 08:30 adds nothing
    fri = await make_task(env, at("2026-11-13", "13:30"), key="price-var")
    assert sla.task_due(entity("E01"), task(env, fri), at("2026-11-13", "14:00")) == at(
        "2026-11-16", "12:30"
    )


async def test_escalates_once_per_level(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    assert tick(env, at("2026-11-11", "14:00")).escalated == {1: 0, 2: 0, 3: 0}  # 5.5 h = 69 %
    assert tick(env, at("2026-11-11", "14:30")).escalated[1] == 1  # 6 h = 75 % -> warn
    assert tick(env, at("2026-11-11", "15:00")).escalated[1] == 0  # idempotent
    assert task(env, tid).escalation_level == 1 and task(env, tid).pool is None

    tick(env, at("2026-11-11", "16:30"))  # 8 h = breach
    t = task(env, tid)
    assert t.escalation_level == 2 and t.pool == "supervisor"

    tick(env, at("2026-11-12", "12:30"))  # 12 h = 150 % -> finance manager
    assert task(env, tid).escalation_level == 3
    assert notes(env, tid) == ["SLA_WARNING", "SLA_BREACH", "SLA_ESCALATION"]
    tick(env, at("2026-11-12", "17:00"))
    assert len(notes(env, tid)) == 3  # never sent twice


async def test_jump_raises_every_missed_level(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    rep = tick(env, at("2026-11-12", "17:00"))  # scheduler was down for a day
    assert rep.escalated == {1: 1, 2: 1, 3: 1}
    assert notes(env, tid) == ["SLA_WARNING", "SLA_BREACH", "SLA_ESCALATION"]


async def test_month_end_window_escalates_sooner(env: Env):
    tid = await make_task(env, at("2026-11-26", "08:30"))  # Thu, last 3 business days
    assert task(env, tid).sla_seconds == 4 * 3600
    tick(env, at("2026-11-26", "13:30"))  # 5 h of 4 h = 125 % -> second escalation
    assert task(env, tid).escalation_level == 3


async def test_hold_pauses_clock_then_resumes(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    with env.factory() as s:
        t = s.get(m.ReviewTask, tid)
        t.hold_started_at = at("2026-11-11", "10:30")  # 2 h used
        t.hold_until = sla.hold_until(entity("E01"), t.hold_started_at)
        s.commit()
    assert task(env, tid).hold_until == at("2026-11-16", "10:30")  # 3 business days, Mon

    # On hold: no escalation however long it sits
    tick(env, at("2026-11-13", "17:00"))
    assert task(env, tid).escalation_level == 0
    assert (
        sla.state_of(
            sla.task_ratio(entity("E01"), task(env, tid), at("2026-11-13", "17:00")),
            sla.on_hold(task(env, tid), at("2026-11-13", "17:00")),
        )
        == "on_hold"
    )

    # Hold ends Mon 10:30; at 11:30 only 2 h + 1 h = 3 h of SLA used
    rep = tick(env, at("2026-11-16", "11:30"))
    t = task(env, tid)
    assert rep.holds_resumed == 1 and t.hold_until is None
    assert t.sla_paused_seconds == 27 * 3600
    assert sla.task_ratio(entity("E01"), t, at("2026-11-16", "11:30")) == pytest.approx(3 / 8)
    assert t.sla_due_at == at("2026-11-16", "16:30")  # 5 h left from 11:30
    assert t.escalation_level == 0


async def test_priority_rises_as_sla_burns(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    tick(env, at("2026-11-11", "08:30"))
    p0 = task(env, tid).priority
    tick(env, at("2026-11-11", "16:30"))
    assert task(env, tid).priority == pytest.approx(p0 + 25, abs=0.01)  # 25 x SLA used


async def test_breached_task_goes_to_supervisor_pool(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    tick(env, at("2026-11-11", "17:00"))
    now = at("2026-11-11", "17:05")
    with env.factory() as s:
        t = s.get(m.ReviewTask, tid)
        with pytest.raises(Forbidden):
            claim(s, t, REVIEWER, now)
        assert (
            next_task(s, REVIEWER, "review", now) is None
            or next_task(s, REVIEWER, "review", now).task_id != tid
        )
        s.rollback()
    with env.factory() as s:
        got = next_task(s, SUPERVISOR, "review", now)
        assert got is not None and got.task_id == tid
        s.commit()


async def test_current_holder_can_finish_after_breach(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    with env.factory() as s:
        claim(s, s.get(m.ReviewTask, tid), REVIEWER, at("2026-11-11", "17:20"))
        s.commit()
    tick(env, at("2026-11-11", "17:25"))
    with env.factory() as s:  # lease still valid: re-claim (heartbeat) is allowed
        claim(s, s.get(m.ReviewTask, tid), REVIEWER, at("2026-11-11", "17:26"))


async def test_notifications_delivered_once(env: Env):
    tid = await make_task(env, at("2026-11-11", "08:30"))
    tick(env, at("2026-11-11", "17:00"))
    sent: list[str] = []

    class Capture(LogNotifier):
        def send(self, note):
            sent.append(f"{note.kind}:{','.join(note.recipients)}")

    with env.factory() as s:
        assert deliver_pending(s, Capture(), at("2026-11-11", "17:01")) == 2
        s.commit()
    with env.factory() as s:
        assert deliver_pending(s, Capture(), at("2026-11-11", "17:06")) == 0
    assert sent[0].startswith("SLA_WARNING:") and "role:supervisor@E01" in sent[0]
    assert sent[1].startswith("SLA_BREACH:")
    assert notes(env, tid) == ["SLA_WARNING", "SLA_BREACH"]


async def test_failing_sender_retries_then_gives_up(env: Env):
    await make_task(env, at("2026-11-11", "08:30"))
    tick(env, at("2026-11-11", "14:30"))

    class Down:
        def send(self, note):
            raise RuntimeError("smtp down")

    for i in range(5):
        with env.factory() as s:
            deliver_pending(s, Down(), at("2026-11-11", "14:30") + timedelta(minutes=5 * i))
            s.commit()
    with env.factory() as s:
        n = s.scalar(select(m.Notification))
        assert n.status == "FAILED" and n.attempts == 5 and "smtp down" in n.last_error
