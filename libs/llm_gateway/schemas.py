"""Structured-output schema for extraction. Every field carries confidence and evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] = Field(
        description="x0, y0, x1, y1 as fractions of page width/height"
    )
    text: str = Field(description="source text exactly as printed")


class F(BaseModel):
    """One extracted field."""

    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(None, description="value as printed; null if absent")
    confidence: float = Field(ge=0, le=1)
    evidence: Evidence | None = None


class LineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: F
    item_code: F | None = None
    uom: F | None = None
    qty: F
    unit_price: F
    amount: F


class TaxOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_percent: F
    base: F
    amount: F


class InvoiceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_name: F
    supplier_tax_id: F | None = None
    invoice_no: F
    invoice_date: F = Field(description="as printed, e.g. 03/10/2026")
    currency: F = Field(description="ISO 4217 code if determinable")
    po_number: F | None = None
    lines: list[LineOut]
    subtotal: F | None = None
    tax_lines: list[TaxOut] = Field(default_factory=list)
    tax_total: F | None = None
    charges: F | None = None
    discount: F | None = None
    total: F


PROMPT_VERSION = "extract-v2.0"
