"""Structured-output schema for extraction. Compact on purpose (fewer output tokens)."""

from pydantic import BaseModel, Field


class Field_(BaseModel):
    v: str | None = Field(None, description="value as printed")
    c: float = Field(0.0, ge=0, le=1, description="confidence")


class Line(BaseModel):
    desc: Field_
    code: Field_ | None = None
    qty: Field_
    price: Field_
    amt: Field_


class InvoiceExtraction(BaseModel):
    supplier: Field_
    inv_no: Field_
    date: Field_
    currency: Field_
    po: Field_ | None = None
    lines: list[Line]
    subtotal: Field_ | None = None
    tax: Field_ | None = None
    charges: Field_ | None = None
    total: Field_
