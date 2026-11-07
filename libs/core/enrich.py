"""Step 7: gather reference data for the rule engine (vendor, PO, items, period, duplicates).

ERP reads go through a 15-minute cache. An ERP outage never fails the invoice: the
context is marked erp_available=False and reference rules come back SKIPPED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db import models as m
from libs.erp_adapter.protocol import ERPAdapter, ERPUnavailable
from libs.rules import types as rt
from libs.rules.money import to_lkr_minor


class CachedERP:
    """Read-through TTL cache over the adapter's read API. Writes pass straight through."""

    def __init__(self, inner: Any, ttl_s: float = 900):
        self.inner = inner
        self.ttl = ttl_s
        self._c: dict[tuple, tuple[float, Any]] = {}

    async def _get(self, key: tuple, fn, *args):
        hit = self._c.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        val = await fn(*args)
        self._c[key] = (time.monotonic(), val)
        return val

    def invalidate(self) -> None:
        self._c.clear()

    async def find_vendors(self, e, name, tax):
        return await self._get(("v", e, name, tax), self.inner.find_vendors, e, name, tax)

    async def get_vendor(self, e, k):
        return await self._get(("gv", e, k), self.inner.get_vendor, e, k)

    async def get_po(self, e, po):
        return await self._get(("po", e, po), self.inner.get_po, e, po)

    async def get_items(self, e, codes):
        return await self._get(("it", e, tuple(sorted(codes))), self.inner.get_items, e, codes)

    async def is_period_open(self, e, on):
        return await self._get(("p", e, on), self.inner.is_period_open, e, on)

    async def fx_to_lkr(self, c, on):
        return await self._get(("fx", c, on), self.inner.fx_to_lkr, c, on)

    async def find_by_external_ref(self, e, ref):
        return await self.inner.find_by_external_ref(e, ref)

    async def post_invoice(self, inv, ref):
        return await self.inner.post_invoice(inv, ref)


def business_key(supplier_id: str | None, inv: rt.Invoice) -> str | None:
    if not (
        supplier_id
        and inv.invoice_no
        and inv.invoice_date
        and inv.currency
        and inv.total_minor is not None
    ):
        return None
    return "|".join(
        [
            supplier_id,
            inv.invoice_no,
            inv.invoice_date.isoformat(),
            str(inv.total_minor),
            inv.currency,
        ]
    )


def _cand(i: m.Invoice) -> rt.DupCandidate | None:
    if not (
        i.supplier_id
        and i.invoice_no_norm
        and i.invoice_date
        and i.currency
        and i.total_minor is not None
    ):
        return None
    return rt.DupCandidate(
        i.invoice_id,
        i.supplier_id,
        i.invoice_no_norm,
        i.invoice_date,
        i.total_minor,
        i.currency,
        i.state,
    )


@dataclass
class Enriched:
    context: rt.Context
    supplier_id: str | None
    business_key: str | None
    fx_to_lkr: Decimal | None
    amount_lkr_minor: int | None


async def build_context(
    session: Session, erp: ERPAdapter, inv: rt.Invoice, today: date, fuzzy_days: int = 30
) -> Enriched:
    erp_ok = True
    vendors: list[rt.Vendor] = []
    po = None
    items: dict[str, rt.Item] = {}
    period = None
    fx = None
    try:
        vendors = await erp.find_vendors(inv.entity_id, inv.supplier_name, inv.supplier_tax_id)
        if inv.po_number:
            po = await erp.get_po(inv.entity_id, inv.po_number)
        codes = {ln.item_code.upper() for ln in inv.lines if ln.item_code}
        if po:
            codes |= {pl.item_code.upper() for pl in po.lines}
        items = await erp.get_items(inv.entity_id, sorted(codes)) if codes else {}
        if inv.invoice_date:
            period = await erp.is_period_open(inv.entity_id, inv.invoice_date)
        if inv.currency:
            fx = await erp.fx_to_lkr(inv.currency, today)
    except ERPUnavailable:
        erp_ok = False

    supplier_id = vendors[0].supplier_id if len(vendors) == 1 else None
    key = business_key(supplier_id, inv)
    exact: list[rt.DupCandidate] = []
    fuzzy: list[rt.DupCandidate] = []
    if key:
        for row in session.scalars(
            select(m.Invoice).where(
                m.Invoice.business_key == key,
                m.Invoice.invoice_id != inv.invoice_id,
                m.Invoice.state != m.State.REJECTED,
            )
        ):
            if c := _cand(row):
                exact.append(c)
    if supplier_id and inv.invoice_date and inv.total_minor is not None:
        lo = inv.invoice_date - timedelta(days=fuzzy_days)
        hi = inv.invoice_date + timedelta(days=fuzzy_days)
        for row in session.scalars(
            select(m.Invoice)
            .where(
                m.Invoice.supplier_id == supplier_id,
                m.Invoice.total_minor == inv.total_minor,
                m.Invoice.invoice_date.between(lo, hi),
                m.Invoice.invoice_id != inv.invoice_id,
                m.Invoice.state.not_in([m.State.REJECTED, m.State.DUPLICATE]),
            )
            .limit(20)
        ):
            if (c := _cand(row)) and c.invoice_id not in {e.invoice_id for e in exact}:
                fuzzy.append(c)

    amount_lkr = (
        to_lkr_minor(inv.total_minor, inv.currency, fx)
        if fx is not None and inv.total_minor is not None and inv.currency
        else None
    )
    ctx = rt.Context(
        today=today,
        vendor_matches=tuple(vendors),
        po=po,
        items=items,
        period=period,
        exact_duplicates=tuple(exact),
        fuzzy_candidates=tuple(fuzzy),
        erp_available=erp_ok,
    )
    return Enriched(ctx, supplier_id, key, fx, amount_lkr)
