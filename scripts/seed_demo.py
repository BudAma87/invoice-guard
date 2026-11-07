"""Seed a demo DB by running the REAL pipeline (ingest -> worker -> relay) on generated PDFs.

python -m scripts.seed_demo            # wipes ./data and seeds
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from libs.config import settings
from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.models import Base, utcnow
from libs.erp_adapter.fake import FakeERP
from libs.llm_gateway.client import SidecarExtractor
from scripts.demo.invoices import SCENARIOS, render


async def seed() -> None:
    data = Path(settings.object_store_dir).parent
    if data.exists():
        shutil.rmtree(data)
    (data / "sidecars").mkdir(parents=True)

    from libs.core.enrich import CachedERP
    from libs.db.session import SessionLocal, engine
    from services.ingest.consumer import receive
    from services.relay import relay
    from services.worker.pipeline import process_invoice

    Base.metadata.create_all(engine)
    store = ObjectStore()
    erp = CachedERP(FakeERP(persist_path=data / "fake_erp.json"))
    sidecars: dict[str, dict] = {}
    now = utcnow()

    # Order matters: originals must post before their duplicates arrive.
    for i, spec in enumerate(SCENARIOS):
        pdf, ext = render(spec)
        sha = hashlib.sha256(pdf).hexdigest()
        if ext:
            sidecars[sha] = ext
            (data / "sidecars" / f"{sha}.json").write_text(json.dumps(ext))
        with SessionLocal() as s:
            r = receive(
                s,
                store,
                message_id=f"msg-{spec.key}",
                entity_id=spec.entity,
                pdf=pdf,
                source="email",
            )
            s.commit()
        at = now - timedelta(minutes=5 * (len(SCENARIOS) - i))
        state = await process_invoice(SessionLocal, erp, SidecarExtractor(sidecars), store, at)
        await relay.run_once(SessionLocal, erp, utcnow())
        print(f"{spec.key:12} {spec.entity} -> {state:20} {spec.note}  ({r.invoice_id[:8]})")

    # Redelivery of a Kafka message: no-op, one invoice
    pdf, _ = render(SCENARIOS[0])
    with SessionLocal() as s:
        r = receive(s, store, message_id="msg-clean-steel", entity_id="E01", pdf=pdf)
        s.commit()
        print(f"{'redelivery':12} E01 -> duplicate message acknowledged ({r.duplicate})")

    # Age some tasks so the queue shows at-risk and breached SLAs.
    with SessionLocal() as s:
        tasks = s.scalars(
            select(m.ReviewTask)
            .where(m.ReviewTask.status == "OPEN")
            .order_by(m.ReviewTask.created_at)
        ).all()
        # Target SLA used, in business time (weekends and nights don't count):
        # 160 % = second escalation, 110 % = breached, 80 % = warned.
        from libs.core import sla
        from libs.core.entities import entity

        now = utcnow()
        for t, target in zip(tasks, (1.6, 1.1, 0.8), strict=False):
            ent = entity(t.entity_id)
            while sla.task_ratio(ent, t, now) < target:
                t.created_at -= timedelta(minutes=15)
            t.sla_due_at = sla.task_due(ent, t, now)
        s.commit()

    # One SLA scheduler pass: escalations and priorities as the queue would show them.
    from libs.core.escalation import LogNotifier, deliver_pending, sla_tick

    with SessionLocal() as s:
        rep = sla_tick(s, utcnow())
        deliver_pending(s, LogNotifier(), utcnow())
        s.commit()
    print(f"SLA pass: escalated {rep.escalated}, notifications {rep.notifications}")
    print(f"\nSeeded {len(SCENARIOS)} invoices into {settings.database_url}")


if __name__ == "__main__":
    asyncio.run(seed())
