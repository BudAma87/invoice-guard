"""SLA scheduler process: `python -m services.scheduler`. One pass every 5 minutes.

Running two copies is safe: escalations are guarded by the task's escalation level and
notifications by a unique (task, kind) key.
"""

from __future__ import annotations

import logging
import time

from libs.core.escalation import LogNotifier, deliver_pending, sla_tick
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


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("sla tick failed; retrying next interval")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    main()
