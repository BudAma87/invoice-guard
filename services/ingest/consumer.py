"""Ingest: Kafka invoices.received -> inbox. Offset is committed only after the inbox row.

Dedupe at two layers in one transaction: message_id (redelivery) and pdf_sha256 (same file
sent twice). Either conflict links to the existing invoice and is acknowledged as a no-op.

Every message is untrusted input. `parse_message` refuses anything that is not a well-formed
invoice message: unknown legal entity, oversized or non-PDF content, or a file reference outside
the configured spool directory. A refused message goes to the dead-letter topic with its reason
and is never written to the inbox, the object store or the model.
"""

from __future__ import annotations

import asyncio
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.config import settings
from libs.core.entities import ENTITIES
from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.state import record_event

log = logging.getLogger("ingest")

PDF_MAGIC = b"%PDF-"
MESSAGE_ID_MAX = 128  # inbox.message_id
SOURCE_MAX = 32  # inbox.source


class IngestRejected(ValueError):
    """A message that must not enter the pipeline. `code` is stable for alerting and the DLQ."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Received:
    invoice_id: str
    duplicate: bool


@dataclass(frozen=True)
class IncomingInvoice:
    message_id: str
    entity_id: str
    pdf: bytes
    source: str


# ------------------------------------------------------------------ validation


def check_pdf(pdf: bytes, max_bytes: int | None = None) -> None:
    limit = settings.ingest_max_pdf_bytes if max_bytes is None else max_bytes
    if not pdf:
        raise IngestRejected("EMPTY_PDF", "message carries no PDF bytes")
    if len(pdf) > limit:
        raise IngestRejected("PDF_TOO_LARGE", f"{len(pdf)} bytes exceeds the {limit}-byte limit")
    if not pdf.startswith(PDF_MAGIC):
        raise IngestRejected("NOT_A_PDF", "content does not start with %PDF-")


def check_entity(entity_id: object) -> str:
    if not isinstance(entity_id, str) or entity_id not in ENTITIES:
        raise IngestRejected("UNKNOWN_ENTITY", f"entity_id {entity_id!r} is not a known entity")
    return entity_id


def _text(body: dict[str, Any], key: str, max_len: int, default: str | None = None) -> str:
    value = body.get(key, default)
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise IngestRejected("BAD_FIELD", f"{key} must be a non-empty string of at most {max_len}")
    return value


def read_spool_file(
    name: object, spool_dir: str | None = None, max_bytes: int | None = None
) -> bytes:
    """Read a PDF named by a message, only from inside the spool directory.

    The name is resolved (following `..` and symlinks) and must stay under the spool root; the
    size is checked before the file is read.
    """
    root_setting = settings.ingest_spool_dir if spool_dir is None else spool_dir
    limit = settings.ingest_max_pdf_bytes if max_bytes is None else max_bytes
    if not root_setting:
        raise IngestRejected("FILE_REF_DISABLED", "pdf_path is not accepted: no spool directory")
    if not isinstance(name, str) or not name or "\x00" in name:
        raise IngestRejected("BAD_FIELD", "pdf_path must be a non-empty string")
    root = Path(root_setting).resolve()
    name = name.replace("\\", "/")  # one separator on every OS, so `..\x` is caught everywhere
    target = (root / name).resolve()  # an absolute `name` replaces root and fails the check below
    if not target.is_relative_to(root) or target == root:
        raise IngestRejected("PATH_OUTSIDE_SPOOL", "pdf_path resolves outside the spool directory")
    if not target.is_file():
        raise IngestRejected("FILE_NOT_FOUND", "pdf_path does not name a file in the spool")
    size = target.stat().st_size
    if size > limit:
        raise IngestRejected("PDF_TOO_LARGE", f"{size} bytes exceeds the {limit}-byte limit")
    return target.read_bytes()


def parse_message(raw: bytes | str) -> IncomingInvoice:
    """Turn one raw Kafka message into a validated invoice, or raise IngestRejected."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise IngestRejected("BAD_JSON", "message is not valid JSON") from e
    if not isinstance(body, dict):
        raise IngestRejected("BAD_JSON", "message must be a JSON object")

    message_id = _text(body, "message_id", MESSAGE_ID_MAX)
    entity_id = check_entity(body.get("entity_id"))
    source = _text(body, "source", SOURCE_MAX, default="kafka")

    has_hex, has_path = "pdf_hex" in body, "pdf_path" in body
    if has_hex == has_path:
        raise IngestRejected("BAD_FIELD", "exactly one of pdf_hex or pdf_path is required")
    if has_hex:
        hex_text = body["pdf_hex"]
        if not isinstance(hex_text, str):
            raise IngestRejected("BAD_FIELD", "pdf_hex must be a string")
        if len(hex_text) > 2 * settings.ingest_max_pdf_bytes:  # refuse before decoding
            raise IngestRejected("PDF_TOO_LARGE", "pdf_hex exceeds the size limit")
        try:
            pdf = bytes.fromhex(hex_text)
        except (ValueError, binascii.Error) as e:
            raise IngestRejected("BAD_FIELD", "pdf_hex is not valid hex") from e
    else:
        pdf = read_spool_file(body["pdf_path"])

    check_pdf(pdf)
    return IncomingInvoice(message_id=message_id, entity_id=entity_id, pdf=pdf, source=source)


# ------------------------------------------------------------------ inbox


def receive(
    session: Session,
    store: ObjectStore,
    *,
    message_id: str,
    entity_id: str,
    pdf: bytes,
    source: str = "kafka",
) -> Received:
    """Idempotent. Caller commits, then acks the message.

    Validates entity and content again, so no caller can put an unknown entity or a non-PDF
    into the inbox, whichever channel it came from.
    """
    check_entity(entity_id)
    check_pdf(pdf)
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


def dead_letter(raw: bytes, err: IngestRejected, offset: int | None = None) -> bytes:
    """DLQ record: the reason and a hash of the message, never the message body itself."""
    return json.dumps(
        {
            "reason": err.code,
            "detail": err.detail,
            "message_sha256": hashlib.sha256(raw).hexdigest(),
            "offset": offset,
        }
    ).encode()


async def main() -> None:  # pragma: no cover - needs a broker
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

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
    dlq = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await consumer.start()
    await dlq.start()
    try:
        async for msg in consumer:
            try:
                inc = parse_message(msg.value)
                with SessionLocal() as s:
                    r = receive(
                        s,
                        store,
                        message_id=inc.message_id,
                        entity_id=inc.entity_id,
                        pdf=inc.pdf,
                        source=inc.source,
                    )
                    s.commit()
            except IngestRejected as e:
                # Refused, never retried: park it with its reason, then move past it.
                await dlq.send_and_wait(
                    settings.kafka_topic_dlq, dead_letter(msg.value, e, msg.offset)
                )
                log.warning("rejected offset=%s reason=%s detail=%s", msg.offset, e.code, e.detail)
                await consumer.commit()
                continue
            await consumer.commit()  # only after the inbox write
            log.info("received %s dup=%s", r.invoice_id, r.duplicate)
    finally:
        await dlq.stop()
        await consumer.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
