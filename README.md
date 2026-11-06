# invoice-guard

AI-powered supplier invoice validation: OCR → LangGraph agent extraction → deterministic rule validation → human-in-the-loop review → exactly-once ERP posting.

> Status: **scaffold**. Folder structure, tooling and CI are in place; node and rule logic are stubs (`TODO`) pending TSD v2 sign-off.

## What it validates
- **Invoice date** — not future, ≤ 90 days old, ≥ PO date, open ERP period
- **Total amount** — Σ(qty × unit price) + tax + charges = total (± per-currency tolerance)
- **Line items** — product name/code, quantity, unit price, line total vs PO lines + item master

## Architecture (short)
```
Kafka → ingest → preprocess → ocr → supplier_template ─┬─(hit)──────────────┐
                                                        └─(miss)→ extract (LLM) ┤
                       normalise → validate → [repair (LLM, ≤1)] → dedupe → route
route → auto_approve ─────────────────────────────┐
      → human_review (interrupt) → checker (≥ LKR 1M) ─┴→ post_erp (outbox) → ERP
```
- Deterministic LangGraph; LLM only in `extract` and `repair` (Claude Haiku → Sonnet on escalation)
- Checkpointer: SQLite (dev) / Postgres (prod), `thread_id = invoice_id`
- Full design: [`docs/`](docs/)

## Tech stack
| Layer | Tech |
|---|---|
| Orchestration | LangGraph, Anthropic SDK, Pydantic |
| OCR | PaddleOCR + OpenCV |
| API / workers | FastAPI, aiokafka, SQLAlchemy 2, Alembic |
| DB | SQLite (dev) → Postgres 16 (prod) |
| UI | Next.js (App Router), TypeScript, Tailwind, shadcn/ui |
| Infra (local) | Docker Compose: Kafka, Postgres, Redis |

## Quick start
```bash
cp .env.example .env            # add ANTHROPIC_API_KEY
make infra-up                   # Kafka, Postgres, Redis
make install                    # backend deps (uv) + pre-commit hooks
make test                       # lint + types + unit tests
make api                        # FastAPI on http://localhost:8000/docs
```
Frontend: see [`frontend/README.md`](frontend/README.md).

## Repository layout
```
backend/src/invoice_guard/
  graph/      LangGraph state, builder, routing, nodes/
  rules/      deterministic validation rules (no LLM)
  llm/        Anthropic client, model tiers, prompts, output schemas
  ocr/        PaddleOCR wrapper
  erp/        ERP adapter protocol + fake
  db/         SQLAlchemy models, session
  api/        FastAPI app + routes (queue, invoices, ops)
  workers/    Kafka consumer, outbox relay
frontend/     Next.js review UI
evals/        labelled invoices + extraction scoring
docs/         TSD and architecture notes
```

## Contributing
Branch from `main` as `feat/…`, `fix/…`, `chore/…`; open a PR using the template. CI must pass and one review is required.
