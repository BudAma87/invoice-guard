"""What the delivery channel says arrived, read independently of our inbox.

Reconciliation compares this against `inbox`: a message here with no inbox row is a lost
invoice. The ledger must NOT be derived from the inbox, or the check proves nothing.

- `KafkaLedger` reads `invoices.received` for a time window (offsets by timestamp).
- `FileLedger` is the dev/demo stand-in: the seed appends every message it "publishes".
- Returning None means "source unavailable": the check is skipped and the run is PARTIAL,
  never silently passed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Protocol

log = logging.getLogger("ledger")


class MessageLedger(Protocol):
    async def message_ids(
        self, entity_id: str, since: datetime, until: datetime
    ) -> set[str] | None: ...


class NullLedger:
    async def message_ids(self, entity_id: str, since: datetime, until: datetime) -> None:
        return None


class FileLedger:
    """JSON lines: {"message_id", "entity_id", "at"} (ISO-8601 with offset)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, message_id: str, entity_id: str, at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"message_id": message_id, "entity_id": entity_id, "at": at.isoformat()})
                + "\n"
            )

    async def message_ids(
        self, entity_id: str, since: datetime, until: datetime
    ) -> set[str] | None:
        if not self.path.exists():
            return None
        out: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            at = datetime.fromisoformat(row["at"])
            if row["entity_id"] == entity_id and since <= at < until:
                out.add(row["message_id"])
        return out


class KafkaLedger:  # pragma: no cover - needs a broker; exercised in the shadow run
    """Scan the topic between two timestamps. Retention (14 days) bounds how far back."""

    def __init__(self, bootstrap: str, topic: str):
        self.bootstrap, self.topic = bootstrap, topic

    async def message_ids(
        self, entity_id: str, since: datetime, until: datetime
    ) -> set[str] | None:
        from aiokafka import AIOKafkaConsumer, TopicPartition

        c = AIOKafkaConsumer(
            bootstrap_servers=self.bootstrap, enable_auto_commit=False, group_id=None
        )
        try:
            await c.start()
            parts = [
                TopicPartition(self.topic, p) for p in (c.partitions_for_topic(self.topic) or set())
            ]
            if not parts:
                return None
            c.assign(parts)
            lo = await c.offsets_for_times({tp: int(since.timestamp() * 1000) for tp in parts})
            hi = await c.offsets_for_times({tp: int(until.timestamp() * 1000) for tp in parts})
            ends = await c.end_offsets(parts)
            out: set[str] = set()
            for tp in parts:
                if lo[tp] is None:
                    continue
                stop = hi[tp].offset if hi[tp] else ends[tp]
                c.seek(tp, lo[tp].offset)
                while (await c.position(tp)) < stop:
                    batch = await c.getmany(tp, timeout_ms=1000, max_records=500)
                    for msg in batch.get(tp, []):
                        if msg.offset >= stop:
                            break
                        body = json.loads(msg.value)
                        if body.get("entity_id") == entity_id:
                            out.add(body["message_id"])
                    if not batch:
                        break
            return out
        except Exception:
            log.exception("kafka ledger unavailable")
            return None
        finally:
            await c.stop()
