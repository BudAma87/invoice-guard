"""Graph nodes. Only `extract` and `repair` call an LLM; everything else is code."""

from invoice_guard.graph.state import InvoiceState


def ingest(state: InvoiceState) -> dict:
    # TODO: inbox insert (unique message_id + pdf_sha256), store PDF
    return {}


def preprocess(state: InvoiceState) -> dict:
    # TODO: split pages, deskew/denoise (OpenCV), page quality score
    return {}


def ocr(state: InvoiceState) -> dict:
    # TODO: PaddleOCR -> words + boxes + confidence; write to object store
    return {"ocr_quality": state.get("ocr_quality", 1.0)}


def supplier_template(state: InvoiceState) -> dict:
    # TODO: apply learned supplier layout template; zero LLM tokens on a hit
    return {"template_hit": False}


def extract(state: InvoiceState) -> dict:
    # TODO: Claude (Haiku -> Sonnet on escalation), structured output, prompt caching
    return {"model_used": "stub", "tokens_used": 0}


def normalise(state: InvoiceState) -> dict:
    # TODO: Pydantic parse, Decimal minor units, ISO dates/currency
    return {}


def validate(state: InvoiceState) -> dict:
    # TODO: run invoice_guard.rules.engine.evaluate
    return {"rule_results": []}


def repair(state: InvoiceState) -> dict:
    # TODO: re-read only failing fields' OCR snippets with Haiku (max 1 attempt)
    return {"repair_attempts": state.get("repair_attempts", 0) + 1}


def dedupe(state: InvoiceState) -> dict:
    # TODO: business_key + fuzzy duplicate checks
    return {}


def decide(state: InvoiceState) -> dict:
    # TODO: compute route from rule results + field confidence
    return {"route": state.get("route", "human_review")}


def human_review(state: InvoiceState) -> dict:
    from langgraph.types import interrupt

    decision = interrupt({"invoice_id": state.get("invoice_id"), "reason": "review"})
    return {"review_decision": decision}


def checker(state: InvoiceState) -> dict:
    from langgraph.types import interrupt

    decision = interrupt({"invoice_id": state.get("invoice_id"), "reason": "maker-checker"})
    return {"checker_decision": decision}


def post_erp(state: InvoiceState) -> dict:
    # TODO: write outbox row; relay posts with ERP lookup-before-retry
    return {}


def close_rejected(state: InvoiceState) -> dict:
    return {}
