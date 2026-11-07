"""In-memory fake ERP for dev, tests and load replay. Optional JSON persistence so the API
and the relay (separate processes) share posted documents. Failure injection for chaos tests.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from libs.erp_adapter.protocol import (
    ERPBusinessError,
    ERPDoc,
    ERPUnavailable,
    PostableInvoice,
)
from libs.rules.text import similarity
from libs.rules.types import Item, PeriodStatus, POLine, PurchaseOrder, Vendor

D = Decimal


@dataclass
class Faults:
    down: bool = False  # every call raises ERPUnavailable
    timeout_after_accept: int = 0  # next N posts are accepted, then raise (ambiguous)
    business_error: str | None = None  # next post raises ERPBusinessError


def _seed_vendors() -> dict[str, list[Vendor]]:
    vat = (D("0.18"),)
    common = [
        Vendor(
            "S-1001",
            "Ceylon Steel Works",
            "TIN-114455",
            allowed_currencies=("LKR",),
            allowed_tax_rates=vat,
        ),
        Vendor(
            "S-1002",
            "Lanka Office Supplies",
            "TIN-220981",
            allowed_currencies=("LKR",),
            allowed_tax_rates=vat,
        ),
        Vendor(
            "S-1003",
            "Asia Pacific Logistics",
            "TIN-330112",
            allowed_currencies=("USD", "LKR"),
            allowed_tax_rates=(D("0"), D("0.18")),
        ),
        Vendor(
            "S-1004",
            "Kandy Packaging",
            "TIN-441287",
            allowed_currencies=("LKR",),
            allowed_tax_rates=vat,
        ),
        Vendor(
            "S-1005",
            "Colombo Chemicals",
            "TIN-559034",
            allowed_currencies=("LKR",),
            allowed_tax_rates=vat,
            blocked=True,
        ),
    ]
    return {"E01": common, "E02": common[:3]}


def _seed_pos() -> dict[tuple[str, str], PurchaseOrder]:
    pos = [
        PurchaseOrder(
            "PO-55821",
            "S-1001",
            "E01",
            "LKR",
            date(2026, 9, 2),
            2_500_000_00,
            lines=(
                POLine("PO-55821-1", 1, "A-100", "Steel bolt M8 x 40 zinc", 120_00, D(5000)),
                POLine("PO-55821-2", 2, "B-220", "Hex nut M8 grade 8", 45_00, D(5000)),
                POLine("PO-55821-3", 3, "C-330", "Flat washer M8", 12_00, D(10000)),
            ),
        ),
        PurchaseOrder(
            "PO-55902",
            "S-1002",
            "E01",
            "LKR",
            date(2026, 10, 1),
            400_000_00,
            lines=(
                POLine("PO-55902-1", 1, "OF-010", "A4 copy paper 80gsm ream", 1_450_00, D(200)),
                POLine("PO-55902-2", 2, "OF-022", "Toner cartridge HP 26A", 18_900_00, D(10)),
            ),
        ),
        PurchaseOrder(
            "PO-56010",
            "S-1003",
            "E01",
            "USD",
            date(2026, 9, 15),
            12_000_00,
            lines=(
                POLine("PO-56010-1", 1, "FR-001", "Sea freight CMB-SIN 40ft", 3_200_00, D(3)),
                POLine("PO-56010-2", 2, "FR-009", "Customs clearance fee", 250_00, D(3)),
            ),
        ),
        PurchaseOrder(
            "PO-56044",
            "S-1004",
            "E01",
            "LKR",
            date(2026, 10, 10),
            900_000_00,
            lines=(
                POLine(
                    "PO-56044-1", 1, "PK-100", "Corrugated carton 12x10x8", 95_00, D(6000), uom="EA"
                ),
                POLine("PO-56044-2", 2, "PK-210", "Stretch wrap film 500mm", 2_400_00, D(120)),
            ),
        ),
        PurchaseOrder(
            "PO-61001",
            "S-1001",
            "E02",
            "LKR",
            date(2026, 10, 5),
            1_800_000_00,
            lines=(
                POLine("PO-61001-1", 1, "A-100", "Steel bolt M8 x 40 zinc", 118_00, D(8000)),
                POLine("PO-61001-2", 2, "D-440", "Anchor bolt M12", 310_00, D(3000)),
            ),
        ),
    ]
    return {(p.entity_id, p.po_number): p for p in pos}


def _seed_items() -> dict[str, Item]:
    codes = ["A-100", "B-220", "C-330", "OF-010", "OF-022", "FR-001", "FR-009", "PK-210", "D-440"]
    items = {c: Item(c) for c in codes}
    items["PK-100"] = Item("PK-100", uom="EA", conversions={"BDL": D(25)})  # bundle of 25
    items["C-330"] = Item("C-330", uom="EA", conversions={"BOX": D(100)})
    return items


# Period calendar: E01 has closed October 2026; E02 still open.
_CLOSED = {("E01", "2026-10"), ("E01", "2026-09"), ("E02", "2026-09")}
_FX = {"LKR": D(1), "USD": D("298.50"), "EUR": D("322.10")}


@dataclass
class FakeERP:
    persist_path: Path | None = None
    faults: Faults = field(default_factory=Faults)
    vendors: dict[str, list[Vendor]] = field(default_factory=_seed_vendors)
    pos: dict[tuple[str, str], PurchaseOrder] = field(default_factory=_seed_pos)
    items: dict[str, Item] = field(default_factory=_seed_items)
    docs: dict[str, ERPDoc] = field(default_factory=dict)  # doc_no -> doc
    post_calls: int = 0
    clock: Callable[[], datetime] | None = None  # tests pin "now" for posted_at
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self._load()

    # ----------------------------------------------------------- persistence
    def _load(self) -> None:
        if self.persist_path and self.persist_path.exists():
            raw = json.loads(self.persist_path.read_text())
            self.docs = {k: ERPDoc(**v) for k, v in raw.get("docs", {}).items()}

    def _save(self) -> None:
        if self.persist_path:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self.persist_path.write_text(
                json.dumps({"docs": {k: v.__dict__ for k, v in self.docs.items()}}, indent=1)
            )

    def _check(self) -> None:
        if self.faults.down:
            raise ERPUnavailable("ERP down")

    # ----------------------------------------------------------- writes
    async def find_by_external_ref(self, entity_id: str, ext_ref: UUID) -> ERPDoc | None:
        self._check()
        self._load()
        return next(
            (
                d
                for d in self.docs.values()
                if d.ext_ref == str(ext_ref) and d.entity_id == entity_id
            ),
            None,
        )

    async def post_invoice(self, inv: PostableInvoice, ext_ref: UUID) -> ERPDoc:
        self._check()
        with self._lock:
            self._load()
            self.post_calls += 1
            if self.faults.business_error:
                msg, self.faults.business_error = self.faults.business_error, None
                raise ERPBusinessError(msg)
            if (inv.entity_id, inv.posting_period) in _CLOSED:
                raise ERPBusinessError(f"Posting period {inv.posting_period} is closed")
            v = await self.get_vendor(inv.entity_id, inv.supplier_id)
            if v is None or v.blocked or not v.active:
                raise ERPBusinessError(f"Vendor {inv.supplier_id} is blocked")
            # A real ERP may or may not dedupe on ext_ref; the fake does NOT, so the
            # relay's lookup-before-post is what the chaos test exercises.
            doc = ERPDoc(
                f"AP-{2026_000000 + len(self.docs) + 1}",
                str(ext_ref),
                inv.entity_id,
                inv.total_minor,
                inv.currency,
                posted_at=(self.clock() if self.clock else datetime.now(UTC)).isoformat(),
            )
            self.docs[doc.doc_no] = doc
            self._save()
            if self.faults.timeout_after_accept > 0:
                self.faults.timeout_after_accept -= 1
                raise ERPUnavailable("timeout after accept", ambiguous=True)
            return doc

    async def list_documents(
        self, entity_id: str, since: datetime, until: datetime
    ) -> list[ERPDoc]:
        self._check()
        self._load()
        out = []
        for d in self.docs.values():
            if d.entity_id != entity_id or not d.posted_at:
                continue
            at = datetime.fromisoformat(d.posted_at)
            if since <= at < until:
                out.append(d)
        return out

    def docs_for(self, ext_ref: str) -> list[ERPDoc]:
        self._load()
        return [d for d in self.docs.values() if d.ext_ref == ext_ref]

    # ----------------------------------------------------------- reads
    async def find_vendors(
        self, entity_id: str, name: str | None, tax_id: str | None
    ) -> list[Vendor]:
        self._check()
        vs = self.vendors.get(entity_id, [])
        if tax_id:
            hit = [v for v in vs if v.tax_id and v.tax_id.upper() == tax_id.upper()]
            if hit:
                return hit
        if name:
            return [v for v in vs if similarity(v.name, name) >= 0.85]
        return []

    async def get_vendor(self, entity_id: str, supplier_key: str) -> Vendor | None:
        self._check()
        return next(
            (v for v in self.vendors.get(entity_id, []) if v.supplier_id == supplier_key), None
        )

    async def get_po(self, entity_id: str, po_number: str) -> PurchaseOrder | None:
        self._check()
        return self.pos.get((entity_id, po_number.upper()))

    async def get_items(self, entity_id: str, codes: list[str]) -> dict[str, Item]:
        self._check()
        return {c.upper(): self.items[c.upper()] for c in codes if c.upper() in self.items}

    async def is_period_open(self, entity_id: str, on: date) -> PeriodStatus:
        self._check()
        p = on.strftime("%Y-%m")
        if (entity_id, p) not in _CLOSED:
            return PeriodStatus(p, True)
        y, m = on.year, on.month
        while (entity_id, f"{y:04d}-{m:02d}") in _CLOSED:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return PeriodStatus(p, False, f"{y:04d}-{m:02d}")

    async def fx_to_lkr(self, currency: str, on: date) -> Decimal:
        self._check()
        return _FX.get(currency.upper(), D(1))
