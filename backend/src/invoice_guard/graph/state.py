"""Graph state. Keep it small: large payloads (PDF, OCR text) live in the object
store and are referenced by URI, so checkpoints stay cheap."""

import operator
from typing import Annotated, Literal, TypedDict

Route = Literal["auto_approve", "human_review", "blocked"]


class RuleResult(TypedDict):
    code: str
    status: Literal["PASS", "WARN", "FAIL", "SKIPPED"]
    severity: Literal["BLOCK", "FAIL", "WARN"]
    message: str


class InvoiceState(TypedDict, total=False):
    invoice_id: str
    message_id: str
    entity_id: str
    pdf_uri: str
    ocr_uri: str
    ocr_quality: float
    template_hit: bool
    model_used: str
    extracted: dict  # normalised invoice (header + lines)
    confidence: dict[str, float]
    rule_results: list[RuleResult]
    repair_attempts: int
    route: Route
    review_decision: dict  # set when the human_review interrupt resumes
    checker_decision: dict
    erp_doc_no: str
    tokens_used: Annotated[int, operator.add]
    errors: Annotated[list[str], operator.add]
