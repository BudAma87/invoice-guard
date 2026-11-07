"""Review workflow through the HTTP API (queue -> workspace -> approve, maker -> checker)."""

from libs.db import models as m
from tests.conftest import Env


def H(user: str, version: int | None = None) -> dict:
    h = {"X-User": user}
    if version is not None:
        h["If-Match"] = str(version)
    return h


async def test_wireframe_fix_override_approve_posts_in_open_period(env: Env, api):
    inv = await env.run("wireframe")
    iid = inv.invoice_id
    q = api.get("/queue", headers=H("n.silva")).json()
    assert any(x["invoice_id"] == iid for x in q["items"])

    assert api.post(f"/invoices/{iid}/claim", headers=H("n.silva")).status_code == 200
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    fails = {r["key"] for r in d["results"] if r["status"] == "FAIL"}
    assert {"DATE_PARSE", "TOTAL_RECONCILE", "DATE_PERIOD_OPEN", "LINE_PO_MATCH:3"} <= fails

    # Reviewer corrects: confirm date, fix the total, drop the unmatched desk organiser line
    lines = [
        {
            k: ln[k]
            for k in (
                "line_no",
                "description",
                "item_code",
                "uom",
                "qty",
                "unit_price_minor",
                "amount_minor",
                "po_line_id",
            )
        }
        for ln in d["lines"]
        if ln["line_no"] != 3
    ]
    corrections = {
        "header": {
            "invoice_date": "2026-10-03",
            "subtotal_minor": 5275360,
            "tax_minor": 949565,
            "total_minor": 6224925,
        },
        "lines": lines,
    }
    v = api.post(
        f"/invoices/{iid}/validate", headers=H("n.silva"), json={"corrections": corrections}
    ).json()
    still = {r["key"] for r in v["results"] if r["status"] == "FAIL"}
    assert still == {"DATE_PERIOD_OPEN"}
    assert not v["approve_allowed"]
    overrides = {"DATE_PERIOD_OPEN": {"reason_code": "PERIOD_CLOSED"}}
    v = api.post(
        f"/invoices/{iid}/validate",
        headers=H("n.silva"),
        json={"corrections": corrections, "overrides": overrides},
    ).json()
    assert v["approve_allowed"] and not v["needs_checker"]
    # /validate never saves
    assert env.get(iid).total_minor == 6401975

    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={
            "action": "approve_with_override",
            "corrections": corrections,
            "overrides": overrides,
            "results_digest": v["results_digest"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == m.State.APPROVED_MANUAL
    assert await env.relay() == 1
    inv = env.get(iid)
    assert inv.state == m.State.POSTED and inv.posting_period == "2026-11"


async def test_maker_checker_separation_of_duties(env: Env, api):
    inv = await env.run("checker-usd")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("k.fernando"))
    d = api.get(f"/invoices/{iid}", headers=H("k.fernando")).json()
    assert d["needs_checker"]
    ov = {"DATE_PERIOD_OPEN": {"reason_code": "PERIOD_CLOSED"}}
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("k.fernando", d["version"]),
        json={"action": "approve_with_override", "overrides": ov},
    )
    assert r.json()["state"] == m.State.PENDING_CHECKER, r.text

    # maker cannot check own work, even holding the Checker role
    r = api.post(f"/invoices/{iid}/claim", headers=H("k.fernando"))
    assert r.status_code == 403
    # reviewer without Checker role cannot claim the check task
    assert api.post(f"/invoices/{iid}/claim", headers=H("n.silva")).status_code == 403


async def test_checker_send_back_returns_to_same_maker(env: Env, api, monkeypatch):
    from libs.core.review import User
    from services.api import auth

    monkeypatch.setitem(
        auth.DEV_USERS,
        "c.two",
        User("c.two", "Checker Two", frozenset({"checker"}), frozenset({"E01"})),
    )
    inv = await env.run("checker-usd")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("n.silva"))
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={
            "action": "approve_with_override",
            "overrides": {"DATE_PERIOD_OPEN": {"reason_code": "PERIOD_CLOSED"}},
        },
    )
    assert api.post(f"/invoices/{iid}/claim", headers=H("c.two")).status_code == 200
    d = api.get(f"/invoices/{iid}", headers=H("c.two")).json()
    assert d["task"]["kind"] == "CHECK" and d["task"]["maker_id"] == "n.silva"
    r = api.post(
        f"/invoices/{iid}/check", headers=H("c.two", d["version"]), json={"action": "send_back"}
    )
    assert r.status_code == 422  # comment required
    r = api.post(
        f"/invoices/{iid}/check",
        headers=H("c.two", d["version"]),
        json={"action": "send_back", "comment": "Attach the supplier's FX confirmation"},
    )
    assert r.json()["state"] == m.State.NEEDS_REVIEW
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    assert d["task"]["assignee"] is None or d["task"]["assignee"] == "n.silva"


async def test_checker_approve_posts(env: Env, api):
    inv = await env.run("checker-usd")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("n.silva"))
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={
            "action": "approve_with_override",
            "overrides": {"DATE_PERIOD_OPEN": {"reason_code": "PERIOD_CLOSED"}},
        },
    )
    nxt = api.post("/queue/next", headers=H("k.fernando"), json={"kind": "check"}).json()
    assert nxt["invoice_id"] == iid
    d = api.get(f"/invoices/{iid}", headers=H("k.fernando")).json()
    r = api.post(
        f"/invoices/{iid}/check", headers=H("k.fernando", d["version"]), json={"action": "approve"}
    )
    assert r.json()["state"] == m.State.APPROVED_MANUAL, r.text
    assert await env.relay() == 1


async def test_entity_scope_is_404(env: Env, api):
    inv = await env.run("clean-e02")
    assert api.get(f"/invoices/{inv.invoice_id}", headers=H("j.perera")).status_code == 404
    assert api.get(f"/invoices/{inv.invoice_id}", headers=H("n.silva")).status_code == 200


async def test_stale_version_and_foreign_claim_conflict(env: Env, api):
    inv = await env.run("low-conf")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("n.silva"))
    assert api.post(f"/invoices/{iid}/claim", headers=H("j.perera")).status_code == 409
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"] - 1),
        json={"action": "approve"},
    )
    assert r.status_code == 409
    r = api.post(f"/invoices/{iid}/decision", headers=H("n.silva"), json={"action": "approve"})
    assert r.status_code == 428


async def test_reject_needs_reason_and_block_cannot_approve(env: Env, api):
    inv = await env.run("blocked")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("n.silva"))
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={
            "action": "approve_with_override",
            "overrides": {"SUPPLIER_INACTIVE": {"reason_code": "OTHER", "comment": "x" * 30}},
        },
    )
    assert r.status_code == 422
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={"action": "reject", "reason_code": "OTHER", "comment": "short"},
    )
    assert r.status_code == 422
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={"action": "reject", "reason_code": "NOT_OUR_INVOICE"},
    )
    assert r.json()["state"] == m.State.REJECTED


async def test_ops_metrics_requires_supervisor(env: Env, api):
    await env.run("clean-steel")
    await env.relay()
    assert api.get("/ops/metrics", headers=H("n.silva")).status_code == 403
    t = api.get("/ops/metrics", headers=H("a.wijesinghe")).json()["tiles"]
    assert t["received_today"] == 1 and t["posted_today"] == 1 and t["auto_pass_rate"] == 1.0


async def test_hold_pauses_sla_and_cannot_stack(env: Env, api):
    inv = await env.run("low-conf")
    iid = inv.invoice_id
    api.post(f"/invoices/{iid}/claim", headers=H("n.silva"))
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={"action": "hold", "comment": "Asked supplier to confirm qty"},
    )
    assert r.status_code == 200, r.text
    d = api.get(f"/invoices/{iid}", headers=H("n.silva")).json()
    assert d["task"]["sla_state"] == "on_hold" and d["task"]["hold_until"]
    r = api.post(
        f"/invoices/{iid}/decision",
        headers=H("n.silva", d["version"]),
        json={"action": "hold", "comment": "again"},
    )
    assert r.status_code == 409
