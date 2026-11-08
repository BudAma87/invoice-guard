from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from libs.core.enrich import CachedERP
from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.models import Base, utcnow
from libs.db.session import make_engine
from libs.erp_adapter.fake import FakeERP
from libs.llm_gateway.client import SidecarExtractor
from scripts.demo.invoices import SCENARIOS, render
from services.ingest.consumer import receive
from services.relay import relay
from services.worker.pipeline import process_invoice


class Env:
    def __init__(self, tmp: Path):
        self.engine = make_engine(f"sqlite:///{tmp / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.store = ObjectStore(tmp / "blobs")
        self.fake = FakeERP()
        self.erp = CachedERP(self.fake, ttl_s=0)
        self.sidecars: dict[str, dict] = {}
        self.ids: dict[str, str] = {}

    async def ingest(self, key: str, message_id: str | None = None, salt: str | None = None):
        spec = next(s for s in SCENARIOS if s.key == key)
        if salt:
            from dataclasses import replace

            spec = replace(spec, salt=salt)
        pdf, ext = render(spec)
        if ext:
            self.sidecars[hashlib.sha256(pdf).hexdigest()] = ext
        with self.factory() as s:
            r = receive(
                s, self.store, message_id=message_id or f"msg-{key}", entity_id=spec.entity, pdf=pdf
            )
            s.commit()
        return r

    async def run(self, key: str, **kw) -> m.Invoice:
        r = await self.ingest(key, **kw)
        await process_invoice(
            self.factory, self.erp, SidecarExtractor(self.sidecars), self.store, utcnow()
        )
        self.ids[key] = r.invoice_id
        return self.get(r.invoice_id)

    async def relay(self) -> int:
        return await relay.run_once(self.factory, self.erp, utcnow())

    def get(self, invoice_id: str) -> m.Invoice:
        with self.factory() as s:
            inv = s.get(m.Invoice, invoice_id)
            assert inv
            inv.lines  # noqa: B018 - load
            return inv

    def count(self, model, *where) -> int:
        with self.factory() as s:
            return len(s.scalars(select(model).where(*where)).all())


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def api(env: Env, monkeypatch):
    from libs.config import settings
    from services.api import deps, main

    monkeypatch.setattr(settings, "dev_auth", True)  # tests use the X-User dev identities
    main.limiter.hits.clear()

    def _db():
        s = env.factory()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[deps.db] = _db
    monkeypatch.setattr(main, "erp_client", lambda: env.erp)
    monkeypatch.setattr(main, "_create_schema", lambda: None)
    client = TestClient(main.app)
    yield client
    main.app.dependency_overrides.clear()
