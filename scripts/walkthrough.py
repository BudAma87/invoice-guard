"""Walk one dummy invoice through the whole system, narrating each step.

    python -m scripts.walkthrough

Uses the real ingest, worker, rule engine, review API (in-process) and outbox relay
against the demo DB in ./data (run `python -m scripts.seed_demo` first).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from libs.config import settings
from libs.core.runtime import erp_client, fake_erp
from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.models import Base, utcnow
from libs.db.session import SessionLocal, engine
from libs.db.state import verify_chain
from libs.llm_gateway.client import SidecarExtractor
from scripts.demo.invoices import Spec, render
from services.api.main import app
from services.ingest.consumer import receive
from services.relay import relay
from services.worker.pipeline import process_invoice

SPEC = Spec(
    "walkthrough",
    "E01",
    "Ceylon Steel Works",
    "TIN-114455",
    f"CSW-{uuid.uuid4().hex[:5].upper()}",
    "2026-11-06",
    "LKR",
    "PO-55821",
    [
        ("Steel bolt M8 x 40 zinc", "A-100", "5000", "120.00", "600,000.00", None),
        ("Hex nut M8 grade 8", "B-220", "4000", "45.00", "180,000.00", None),
        ("Flat washer M8", "C-330", "9000", "12.00", "108,000.00", None),
    ],
    "888,000.00",
    "18",
    "159,840.00",
    "1,047,480.00",  # printed total has two digits swapped (should be 1,047,840.00)
    conf={"total": 0.78},
)


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n STEP {n}  {title}\n{'=' * 78}")


def show_results(results: list[dict], only_problems: bool = False) -> None:
    for r in results:
        if only_problems and r["status"] == "PASS":
            continue
        mark = {"PASS": "✓", "FAIL": "✕", "WARN": "▲", "SKIPPED": "–"}[r["status"]]
        print(f"   {mark} {r['status']:7} {r['key']:22} {r['message'][:72]}")


async def main() -> None:
    Base.metadata.create_all(engine)
    store, erp = ObjectStore(), erp_client()
    api = TestClient(app)
    maker = {"X-User": "n.silva"}
    checker = {"X-User": "k.fernando"}

    step(1, "Supplier emails a PDF → Kafka → ingest writes the inbox row")
    pdf, extraction = render(SPEC)
    sha = hashlib.sha256(pdf).hexdigest()
    out = Path(settings.object_store_dir).parent / "walkthrough.pdf"
    out.write_bytes(pdf)
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        r = receive(s, store, message_id=msg_id, entity_id="E01", pdf=pdf, source="email")
        s.commit()
    iid = r.invoice_id
    print(f"   invoice {SPEC.invoice_no} → invoice_id {iid[:8]}…  state RECEIVED")
    print(f"   PDF stored write-once as sha256/{sha[:16]}…  (copy at {out})")

    step(2, "Kafka redelivers the same message (at-least-once) → no-op")
    with SessionLocal() as s:
        r2 = receive(s, store, message_id=msg_id, entity_id="E01", pdf=pdf)
        s.commit()
    same = r2.invoice_id == iid
    print(f"   duplicate={r2.duplicate}, same invoice_id={same} → still ONE invoice")

    step(3, "Worker: extract (model reads PDF) → normalise → enrich → rules → route")
    ext = SidecarExtractor({sha: extraction})  # stands in for Claude in this demo
    state = await process_invoice(SessionLocal, erp, ext, store, utcnow())
    d = api.get(f"/invoices/{iid}", headers=maker).json()
    print(
        f"   extracted total {d['header']['total_minor'] / 100:,.2f} LKR, confidence "
        f"{d['confidence']['total']:.2f}  (threshold {d['min_confidence']})"
    )
    print(f"   {len(d['results'])} rules ran in 4 stages. Problems:")
    show_results(d["results"], only_problems=True)
    print(f"   → routed to {state}; task reasons {d['task']['reasons']}")
    print(f"   ≈ LKR {d['amount_lkr_minor'] / 100:,.0f} → needs checker: {d['needs_checker']}")

    step(4, "Reviewer (Nimali, maker) claims it from the queue — 15-min lease")
    q = api.get("/queue", headers=maker).json()
    row = next(x for x in q["items"] if x["invoice_id"] == iid)
    print(
        f"   queue row: priority {row['priority']:.0f}, SLA {row['sla_state']}, "
        f"reasons {row['reasons']}"
    )
    api.post(f"/invoices/{iid}/claim", headers=maker)
    other = api.post(f"/invoices/{iid}/claim", headers={"X-User": "j.perera"})
    print(f"   Janith tries to claim too → HTTP {other.status_code}: {other.json()['detail']}")

    step(5, "Reviewer checks PDF evidence, fixes the total; UI re-validates (nothing saved)")
    fix = {"header": {"total_minor": 1_047_840_00}}
    v = api.post(f"/invoices/{iid}/validate", headers=maker, json={"corrections": fix}).json()
    print(f"   rule changes: {[(c['key'], c['from'], '→', c['to']) for c in v['diff']]}")
    print(f"   approve allowed: {v['approve_allowed']}, needs checker: {v['needs_checker']}")

    step(6, "Reviewer approves → amount ≥ LKR 1M, so it goes to a checker, not the ERP")
    d = api.get(f"/invoices/{iid}", headers=maker).json()
    res = api.post(
        f"/invoices/{iid}/decision",
        headers={**maker, "If-Match": str(d["version"])},
        json={
            "action": "approve",
            "corrections": fix,
            "reason_code": "OCR_CORRECTION",
            "results_digest": v["results_digest"],
        },
    )
    print(f"   HTTP {res.status_code} → {res.json()}")

    step(7, "Separation of duties: maker cannot check own work")
    me_too = api.post(f"/invoices/{iid}/claim", headers={"X-User": "n.silva"})
    print(f"   Nimali tries to check → HTTP {me_too.status_code}: {me_too.json()['detail']}")

    step(8, "Checker (Kasun) takes next check task, sees the maker's diff, approves")
    nxt = api.post("/queue/next", headers=checker, json={"kind": "check"}).json()
    if nxt["invoice_id"] != iid:  # other demo checks may be ahead; claim ours directly
        api.post(f"/invoices/{iid}/release", headers=checker)
        api.post(f"/invoices/{iid}/claim", headers=checker)
    d = api.get(f"/invoices/{iid}", headers=checker).json()
    ch = next(e for e in reversed(d["events"]) if e["action"] == "MAKER_APPROVE")
    for k, (old, new) in ch["payload"]["changed"].items():
        print(f"   maker changed {k}: {old / 100:,.2f} → {new / 100:,.2f}")
    res = api.post(
        f"/invoices/{iid}/check",
        headers={**checker, "If-Match": str(d["version"])},
        json={"action": "approve", "comment": "Total matches the PDF"},
    )
    print(f"   HTTP {res.status_code} → {res.json()}  (outbox row written in same transaction)")

    step(9, "Outbox relay posts to the ERP — looks up external ref first")
    before = fake_erp().post_calls
    n = await relay.run_once(SessionLocal, erp, utcnow())
    d = api.get(f"/invoices/{iid}", headers=checker).json()
    print(f"   relay posted {n}; invoice state {d['state']}; ERP document {d['erp_doc_no']}")
    n2 = await relay.run_once(SessionLocal, erp, utcnow())
    print(
        f"   relay runs again → posted {n2} more; ERP post calls this run "
        f"{fake_erp().post_calls - before}; docs for this invoice: "
        f"{len(fake_erp().docs_for(iid))}"
    )

    step(10, "Audit trail (append-only, hash-chained)")
    for e in d["events"]:
        who = e["actor_name"] + (f" ({e['role']})" if e["role"] else "")
        arrow = f"{e['from'] or '∅'} → {e['to']}" if e["to"] else ""
        print(f"   {e['at'][11:19]}  {e['action']:22} {arrow:36} {who}")
    with SessionLocal() as s:
        print(f"   hash chain verifies: {verify_chain(s, iid)}")
        inv = s.get(m.Invoice, iid)
        print(
            f"\n   FINAL: {SPEC.invoice_no} {inv.state}, ERP {inv.erp_doc_no}, "
            f"total {inv.total_minor / 100:,.2f} LKR"
        )
    print(json.dumps({"invoice_id": iid, "open_in_ui": f"http://localhost:3100/invoices/{iid}"}))


if __name__ == "__main__":
    asyncio.run(main())
