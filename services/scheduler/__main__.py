"""Scheduler process: `python -m services.scheduler`. One pass every 5 minutes:

- SLA escalation for every open review task;
- daily reconciliation per entity once its local time passes 23:30, catching up any day
  of the last week that has no finished run.

Running two copies is safe for escalation (guarded by level and a unique notification key);
for reconciliation a duplicate run only stores a second, identical report.
"""

from __future__ import annotations

import asyncio
import logging
import time

from libs.core.escalation import LogNotifier, deliver_pending, sla_tick
from libs.core.reconcile import due, run_reconciliation
from libs.core.runtime import erp_client, message_ledger
from libs.db.models import utcnow
from libs.db.session import SessionLocal

INTERVAL_S = 300
log = logging.getLogger("scheduler")


def run_once() -> None:
    now = utcnow()
    with SessionLocal() as s:
        rep = sla_tick(s, now)
        s.commit()
    with SessionLocal() as s:
        sent = deliver_pending(s, LogNotifier(), now)
        s.commit()
    log.info(
        "sla tick: %s tasks, holds resumed %s, escalated %s, notifications %s sent %s, "
        "reprioritised %s",
        rep.tasks,
        rep.holds_resumed,
        rep.escalated,
        rep.notifications,
        sent,
        rep.reprioritised,
    )
    asyncio.run(reconcile_due())


async def reconcile_due() -> None:
    erp, ledger = erp_client(), message_ledger()
    with SessionLocal() as s:
        todo = due(s, utcnow())
    for entity_id, day in todo:
        with SessionLocal() as s:
            run = await run_reconciliation(s, erp, ledger, entity_id, day, utcnow())
            s.commit()
            log.info(
                "reconciliation %s %s: %s, %d difference(s)",
                entity_id,
                day,
                run.status,
                run.differences,
            )


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("scheduler pass failed; retrying next interval")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    main()
