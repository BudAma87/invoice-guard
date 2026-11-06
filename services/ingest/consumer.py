"""Ingest: Kafka invoices.received -> inbox. Offset is committed only after the inbox row.

Dedupe at two layers in one transaction: message_id (redelivery) and pdf_sha256 (same file
sent twice). Either conflict links to the existing invoice and is acknowledged as a no-op.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.state import record_event

log = logging.getLogger("ingest")


@dataclass(frozen=True)
class Received:
    invoice_id: str
    duplicate: bool


def receive(
    session: Session,
    store: ObjectStore,
    *,
    message_id: str,
    entity_id: str,
    pdf: bytes,
    source: str = "kafka",
) -> Received:
    """Idempotent. Caller commits, then acks the message."""
    sha = hashlib.sha256(pdf).hexdigest()
    hit = session.scalar(
        select(m.Inbox).where((m.Inbox.message_id == message_id) | (m.Inbox.pdf_sha256 == sha))
    )
    if hit:
        record_event(
            session,
            hit.invoice_id,
            "DUPLICATE_MESSAGE",
            payload={"message_id": message_id, "same_file": hit.pdf_sha256 == sha},
        )
        return Received(hit.invoice_id, True)
    store.put(pdf)  # write-once, content-addressed
    inv = m.Invoice(entity_id=entity_id, state=m.State.RECEIVED, pdf_sha256=sha)
    session.add(inv)
    session.flush()
    session.add(
        m.Inbox(message_id=message_id, pdf_sha256=sha, source=source, invoice_id=inv.invoice_id)
    )
    record_event(
        session,
        inv.invoice_id,
        "RECEIVED",
        payload={"message_id": message_id, "source": source, "pdf_sha256": sha},
        to_state=m.State.RECEIVED,
    )
    return Received(inv.invoice_id, False)


async def main() -> None:  # pragma: no cover - needs a broker
    from aiokafka import AIOKafkaConsumer

    from libs.config import settings
    from libs.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO)
    store = ObjectStore()
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_in,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id="invoice-ingest",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            body = json.loads(msg.value)
            if "pdf_hex" in body:
                pdf = bytes.fromhex(body["pdf_hex"])
            else:
                with open(body["pdf_path"], "rb") as fh:
                    pdf = fh.read()
            with SessionLocal() as s:
                r = receive(
                    s,
                    store,
                    message_id=body["message_id"],
                    entity_id=body["entity_id"],
                    pdf=pdf,
                    source=body.get("source", "kafka"),
                )
                s.commit()
            await consumer.commit()  # only after the inbox write
            log.info("received %s dup=%s", r.invoice_id, r.duplicate)
    finally:
        await consumer.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
