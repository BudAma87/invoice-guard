"""Thin Anthropic client: model tiers, prompt caching, structured output. TODO."""

from invoice_guard.config import settings


def pick_model(ocr_quality: float, line_count: int) -> str:
    """Cascade: fast tier by default; strong tier for poor scans or long invoices."""
    if ocr_quality < 0.80 or line_count > 40:
        return settings.model_strong
    return settings.model_fast
