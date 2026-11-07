"""Exactly-once and no-lost-invoice guarantees (TSD §9, §10 'Idempotency')."""

from sqlalchemy import select

from libs.db import models as m
from libs.db.state import verify_chain
from tests.conftest import Env


async def test_clean_invoice_auto_posts_once(env: Env):
    inv = await env.run("clean-steel")
    assert inv.state == m.State.APPROVED_AUTO
    assert await env.relay() == 1
    inv = env.get(inv.invoice_id)
    assert inv.state == m.State.POSTED and inv.erp_doc_no
    assert len(env.fake.docs_for(inv.invoice_id)) == 1


async def test_kafka_redelivery_and_same_file_are_noops(env: Env):
    a = await env.ingest("clean-steel", message_id="m1")
    b = await env.ingest("clean-steel", message_id="m1")  # redelivery
    c = await env.ingest("clean-steel", message_id="m2")  # same PDF, second email
    assert a.invoice_id == b.invoice_id == c.invoice_id
    assert b.duplicate and c.duplicate
    assert env.count(m.Invoice) == 1
    assert env.count(m.InvoiceEvent, m.InvoiceEvent.action == "DUPLICATE_MESSAGE") == 2


async def test_relay_crash_after_erp_accept_posts_exactly_once(env: Env):
    """Chaos: ERP accepts, then the call times out before our DB update."""
    inv = await env.run("clean-steel")
    env.fake.faults.timeout_after_accept = 1
    assert await env.relay() == 0  # ambiguous outcome -> UNKNOWN
    with env.factory() as s:
        ob = s.scalar(select(m.Outbox).where(m.Outbox.invoice_id == inv.invoice_id))
        assert ob.status == m.OutboxStatus.UNKNOWN
        ob.next_attempt_at = ob.created_at  # make it due now
        s.commit()
    assert await env.relay() == 1  # lookup by external ref finds the doc; no second post
    assert env.fake.post_calls == 1
    assert len(env.fake.docs_for(inv.invoice_id)) == 1
    assert env.get(inv.invoice_id).state == m.State.POSTED


async def test_erp_down_waits_in_approved(env: Env):
    inv = await env.run("clean-steel")
    env.fake.faults.down = True
    await env.relay()
    assert env.get(inv.invoice_id).state == m.State.APPROVED_AUTO
    assert env.fake.post_calls == 0


async def test_rescan_is_duplicate_suspected_never_posted(env: Env):
    await env.run("clean-steel")
    await env.relay()
    dup = await env.run("dup-exact")
    assert dup.state == m.State.DUPLICATE_SUSPECTED
    assert dup.business_key is None and dup.duplicate_of == env.ids["clean-steel"]
    await env.relay()
    assert env.get(dup.invoice_id).state == m.State.DUPLICATE_SUSPECTED


async def test_unreadable_pdf_becomes_manual_task_not_lost(env: Env):
    inv = await env.run("unreadable")
    assert inv.state == m.State.EXTRACTION_FAILED
    assert env.count(m.ReviewTask, m.ReviewTask.kind == m.TaskKind.MANUAL_ENTRY) == 1


async def test_audit_chain_verifies(env: Env):
    inv = await env.run("clean-steel")
    await env.relay()
    with env.factory() as s:
        assert verify_chain(s, inv.invoice_id)
        ev = s.scalars(
            select(m.InvoiceEvent).where(m.InvoiceEvent.invoice_id == inv.invoice_id)
        ).first()
        ev.payload_json = {"tampered": True}
        s.commit()
        assert not verify_chain(s, inv.invoice_id)
