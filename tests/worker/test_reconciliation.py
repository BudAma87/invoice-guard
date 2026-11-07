"""Daily reconciliation: the lost-invoice net (TSD §9)."""

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from libs.core.entities import SLT, entity
from libs.core.ledger import FileLedger, NullLedger
from libs.core.reconcile import InvoiceFacts, compare, due, run_reconciliation
from libs.db import models as m
from libs.db.models import utcnow
from libs.erp_adapter.protocol import ERPDoc
from tests.conftest import Env

TODAY = lambda: entity("E01").today(utcnow())  # noqa: E731


# ------------------------------------------------------------------ pure comparison


def facts(iid="i1", state="POSTED", total=100, ccy="LKR", doc="AP-1"):
    return InvoiceFacts(iid, state, total, ccy, doc)


def doc(ref="i1", no="AP-1", total=100, ccy="LKR"):
    return ERPDoc(no, ref, "E01", total, ccy)


def kinds(diffs):
    return sorted(d.kind for d in diffs)


def test_clean_day_has_no_differences():
    f = facts()
    assert (
        compare(
            messages={"m1"},
            inbox_message_ids={"m1"},
            posted=[f],
            erp_doc_for_posted={"i1": doc()},
            day_docs=[doc()],
            invoices={"i1": f},
        )
        == []
    )


def test_each_difference_kind():
    posted_ok, posted_missing = facts("i1"), facts("i2", doc="AP-2")
    stuck = facts("i3", state="POSTING", doc=None)
    diffs = compare(
        messages={"m1", "m-lost"},
        inbox_message_ids={"m1"},
        posted=[posted_ok, posted_missing],
        erp_doc_for_posted={"i1": doc(total=150), "i2": None},
        day_docs=[doc(total=150), doc(no="AP-9", total=150), doc("i3", "AP-3"), doc("zz", "AP-4")],
        invoices={"i1": posted_ok, "i3": stuck},
    )
    assert kinds(diffs) == sorted(
        [
            "MESSAGE_WITHOUT_INBOX",  # m-lost
            "POSTED_WITHOUT_ERP_DOC",  # i2
            "AMOUNT_MISMATCH",  # i1: 150 vs 100
            "DUPLICATE_ERP_DOC",  # i1: AP-1 and AP-9
            "ERP_DOC_WITHOUT_POSTED",  # i3 still POSTING
            "ERP_DOC_WITHOUT_POSTED",  # zz unknown to the pipeline
        ]
    )
    assert all(d.severity == "critical" for d in diffs if d.kind != "AMOUNT_MISMATCH")


def test_doc_number_mismatch():
    diffs = compare(
        messages=set(),
        inbox_message_ids=set(),
        posted=[facts(doc="AP-1")],
        erp_doc_for_posted={"i1": doc(no="AP-7")},
        day_docs=[],
        invoices={},
    )
    assert kinds(diffs) == ["DOC_NO_MISMATCH"]


def test_ledger_unavailable_skips_message_check():
    assert (
        compare(
            messages=None,
            inbox_message_ids=set(),
            posted=[],
            erp_doc_for_posted={},
            day_docs=[],
            invoices={},
        )
        == []
    )


# ------------------------------------------------------------------ real pipeline + fake ERP


class Pages:
    def __init__(self):
        self.runs = []

    def page(self, run, items):
        self.runs.append((run.status, [i.kind for i in items]))


async def posted_invoice(env: Env, key="clean-steel") -> str:
    inv = await env.run(key)
    assert await env.relay() == 1
    return inv.invoice_id


async def reconcile(env: Env, ledger, pager=None, day=None):
    with env.factory() as s:
        run = await run_reconciliation(
            s, env.erp, ledger, "E01", day or TODAY(), utcnow(), pager=pager or Pages()
        )
        s.commit()
        return run.status, sorted(i.kind for i in run.items), dict(run.counts), list(run.skipped)


def ledger_with(tmp_path, *ids) -> FileLedger:
    led = FileLedger(tmp_path / "ledger.jsonl")
    for i in ids:
        led.append(i, "E01", utcnow())
    return led


async def test_clean_day_is_ok(env: Env, tmp_path):
    await posted_invoice(env)
    status, items, counts, _ = await reconcile(env, ledger_with(tmp_path, "msg-clean-steel"))
    assert status == "OK" and items == []
    assert counts == {"messages": 1, "inbox_matched": 1, "posted": 1, "erp_documents": 1}


async def test_lost_message_is_caught_and_paged(env: Env, tmp_path):
    await posted_invoice(env)
    pager = Pages()
    status, items, _, _ = await reconcile(
        env, ledger_with(tmp_path, "msg-clean-steel", "msg-never-ingested"), pager
    )
    assert status == "DIFFERENCES" and items == ["MESSAGE_WITHOUT_INBOX"]
    assert pager.runs == [("DIFFERENCES", ["MESSAGE_WITHOUT_INBOX"])]


async def test_posted_but_missing_in_erp(env: Env, tmp_path):
    iid = await posted_invoice(env)
    env.fake.docs.clear()  # someone reversed the document in the ERP
    env.fake._save()
    status, items, _, _ = await reconcile(env, ledger_with(tmp_path, "msg-clean-steel"))
    assert status == "DIFFERENCES" and items == ["POSTED_WITHOUT_ERP_DOC"]
    with env.factory() as s:  # the invoice's audit trail records it
        assert (
            s.scalar(
                select(m.InvoiceEvent).where(
                    m.InvoiceEvent.invoice_id == iid,
                    m.InvoiceEvent.action == "RECONCILIATION_DIFFERENCE",
                )
            )
            is not None
        )


async def test_double_posting_is_caught(env: Env, tmp_path):
    iid = await posted_invoice(env)
    d = next(iter(env.fake.docs.values()))
    env.fake.docs["AP-DUP"] = replace(d, doc_no="AP-DUP")  # a second document, same ref
    status, items, _, _ = await reconcile(env, ledger_with(tmp_path, "msg-clean-steel"))
    assert "DUPLICATE_ERP_DOC" in items and status == "DIFFERENCES"
    assert iid in {d.ext_ref for d in env.fake.docs.values()}


async def test_unhealed_relay_crash_then_healed(env: Env, tmp_path):
    inv = await env.run("clean-steel")
    env.fake.faults.timeout_after_accept = 1  # ERP accepted; we never heard back
    assert await env.relay() == 0
    led = ledger_with(tmp_path, "msg-clean-steel")
    status, items, _, _ = await reconcile(env, led)
    assert status == "DIFFERENCES" and items == ["ERP_DOC_WITHOUT_POSTED"]

    with env.factory() as s:  # next relay pass looks the document up and marks it POSTED
        ob = s.scalar(select(m.Outbox).where(m.Outbox.invoice_id == inv.invoice_id))
        ob.next_attempt_at = ob.created_at
        s.commit()
    assert await env.relay() == 1
    status, items, _, _ = await reconcile(env, led)
    assert status == "OK" and items == []


async def test_missing_ledger_is_partial_not_ok(env: Env):
    await posted_invoice(env)
    status, items, counts, skipped = await reconcile(env, NullLedger())
    assert status == "PARTIAL" and items == [] and counts["messages"] is None
    assert skipped == ["messages→inbox: message ledger unavailable"]


async def test_erp_down_is_error_and_retried(env: Env, tmp_path):
    await posted_invoice(env)
    env.fake.faults.down = True
    pager = Pages()
    status, _, _, _ = await reconcile(env, ledger_with(tmp_path, "msg-clean-steel"), pager)
    assert status == "ERROR" and pager.runs[0][0] == "ERROR"
    with env.factory() as s:  # an ERROR run doesn't count as done
        assert ("E01", TODAY()) in due(s, utcnow() + timedelta(days=1))


# ------------------------------------------------------------------ scheduling


def local(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=SLT)


def test_runs_after_2330_local_and_catches_up(env: Env):
    with env.factory() as s:
        before = due(s, local("2026-11-11", "23:00"), lookback_days=2)
        after = due(s, local("2026-11-11", "23:31"), lookback_days=2)
    assert ("E01", date(2026, 11, 11)) not in before
    assert ("E01", date(2026, 11, 10)) in before  # yesterday was missed: catch up
    assert ("E01", date(2026, 11, 11)) in after


@pytest.mark.parametrize(
    "status,done", [("OK", True), ("DIFFERENCES", True), ("PARTIAL", True), ("ERROR", False)]
)
def test_finished_days_are_not_rerun(env: Env, status, done):
    with env.factory() as s:
        s.add(m.ReconciliationRun(entity_id="E01", business_date=date(2026, 11, 11), status=status))
        s.commit()
        todo = due(s, local("2026-11-11", "23:45"), lookback_days=1)
    assert (("E01", date(2026, 11, 11)) not in todo) is done
