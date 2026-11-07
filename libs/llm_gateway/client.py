"""The single internal client for the model provider.

The model only reads the invoice: it has no tools that act and no posting rights, and its
output is schema-only data. Deterministic code decides what posts.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from libs.config import settings
from libs.llm_gateway.resilience import CircuitBreaker, TokenBucket
from libs.llm_gateway.schemas import PROMPT_VERSION, InvoiceExtraction

PROMPT = (Path(__file__).parent / "prompts" / "extract.md").read_text(encoding="utf-8")


@dataclass
class ExtractionResult:
    parsed: InvoiceExtraction
    model: str
    prompt_version: str
    raw: str
    latency_ms: int
    tokens: int


class ExtractionFailed(Exception):
    pass


class Extractor(Protocol):
    async def extract(
        self, pdf: bytes, *, hint: dict[str, Any] | None = None
    ) -> ExtractionResult: ...


class AnthropicExtractor:
    """Primary + fallback model; one repair re-prompt on schema violation."""

    def __init__(
        self,
        primary: str | None = None,
        fallback: str | None = None,
        bucket: TokenBucket | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        self.primary = primary or settings.model_primary
        self.fallback = fallback or settings.model_fallback
        self.bucket = bucket or TokenBucket(settings.llm_rpm, settings.llm_tpm)
        self.breaker = breaker or CircuitBreaker()

    async def _call(self, model: str, messages: list[dict[str, Any]]) -> tuple[Any, str, int]:
        """One structured-output call. Returns (parsed_or_None, raw_text, tokens)."""
        await self.bucket.acquire(4000)
        self.breaker.before()
        try:
            resp = await self._client.messages.parse(
                model=model,
                max_tokens=16000,
                system=PROMPT,
                messages=messages,  # type: ignore[arg-type]
                output_format=InvoiceExtraction,
            )
        except ValidationError as e:  # schema-shaped but fails our constraints
            self.breaker.record(True)
            return None, str(e), 0
        except Exception:
            self.breaker.record(False)
            raise
        self.breaker.record(True)
        if resp.stop_reason == "refusal":
            raise ExtractionFailed(f"model declined ({model})")
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        tokens = resp.usage.input_tokens + resp.usage.output_tokens
        return resp.parsed_output, raw, tokens

    async def extract(self, pdf: bytes, *, hint: dict[str, Any] | None = None) -> ExtractionResult:
        doc = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(pdf).decode(),
            },
        }
        text = "Extract this supplier invoice." + (
            f" Supplier hints: {json.dumps(hint)}" if hint else ""
        )
        base: list[dict[str, Any]] = [
            {"role": "user", "content": [doc, {"type": "text", "text": text}]}
        ]
        last_err = ""
        for model in (self.primary, self.fallback):
            t0 = time.monotonic()
            parsed, raw, tokens = await self._call(model, base)
            if parsed is None:  # one repair re-prompt with the validation errors
                repair = base + [
                    {"role": "assistant", "content": "(previous output failed validation)"},
                    {"role": "user", "content": "Fix these schema errors and resend:\n" + raw},
                ]
                parsed, raw, t2 = await self._call(model, repair)
                tokens += t2
            if parsed is None:
                last_err = raw
                continue
            return ExtractionResult(
                parsed, model, PROMPT_VERSION, raw, int((time.monotonic() - t0) * 1000), tokens
            )
        raise ExtractionFailed(last_err or "extraction failed")


class SidecarExtractor:
    """Dev/test extractor: reads `<sha>.json` next to the PDF instead of calling a model."""

    def __init__(self, sidecars: dict[str, dict[str, Any]]):
        self.sidecars = sidecars

    async def extract(self, pdf: bytes, *, hint: dict[str, Any] | None = None) -> ExtractionResult:
        import hashlib

        sha = hashlib.sha256(pdf).hexdigest()
        if sha not in self.sidecars:
            raise ExtractionFailed("no sidecar for this PDF")
        raw = json.dumps(self.sidecars[sha])
        return ExtractionResult(
            InvoiceExtraction.model_validate_json(raw), "sidecar", PROMPT_VERSION, raw, 5, 0
        )
