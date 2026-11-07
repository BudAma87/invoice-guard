"""Process-wide singletons wired from settings."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from libs.config import settings
from libs.core.enrich import CachedERP
from libs.core.ledger import FileLedger, KafkaLedger, MessageLedger, NullLedger
from libs.erp_adapter.fake import FakeERP
from libs.llm_gateway.client import AnthropicExtractor, Extractor, SidecarExtractor


@lru_cache
def fake_erp() -> FakeERP:
    return FakeERP(persist_path=Path(settings.object_store_dir).parent / "fake_erp.json")


@lru_cache
def erp_client() -> CachedERP:
    return CachedERP(fake_erp())  # swap for a vendor adapter per entity


def extractor_client() -> Extractor:
    sidecar_dir = Path(settings.object_store_dir).parent / "sidecars"
    if not settings.anthropic_api_key and sidecar_dir.exists():
        return SidecarExtractor(
            {p.stem: json.loads(p.read_text()) for p in sidecar_dir.glob("*.json")}
        )
    return AnthropicExtractor()


def message_ledger() -> MessageLedger:
    if settings.message_ledger == "kafka":
        return KafkaLedger(settings.kafka_bootstrap, settings.kafka_topic_in)
    if settings.message_ledger == "file":
        return FileLedger(ledger_path())
    return NullLedger()


def ledger_path() -> Path:
    return Path(settings.object_store_dir).parent / "kafka_ledger.jsonl"
