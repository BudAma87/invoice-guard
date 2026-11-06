# invoice-guard

Supplier invoice PDF → **exactly one** ERP posting. A model only *reads* the invoice; a
deterministic rule engine decides; anything uncertain goes to a person. Built to the
[Invoice Validation Pipeline TSD v2](docs/TSD_v2.pdf).

> Status: **Phase 1 (foundation)** — state DB, inbox, rule engine, fake ERP, outbox relay,
> review API and the review UI on SQLite. Kafka/LLM/Postgres paths are wired but run in
> dev with a fake ERP and demo extraction sidecars.

## Guarantees
| Goal | How |
|---|---|
| No double posting | Unique keys on message, file (`pdf_sha256`) and business document; transactional outbox; relay looks up the ERP by external ref (`invoice_id`) before any post |
| No lost invoice | Inbox written before the Kafka offset commits; every step commits before the next; lease sweeper; extraction failures become manual-entry tasks |
| Uncertain means human | Any FAIL/SKIPPED rule or field confidence < 0.85 → review queue |
| Controls | Maker-checker ≥ LKR 1M (checker ≠ maker), mandatory reason codes, SLA timers in business hours, entity-scoped access (404 outside scope), append-only hash-chained audit log |

## Quick start (no Docker, no API key)
```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or: uv sync --all-extras
python -m scripts.seed_demo                 # runs the real pipeline on 19 generated PDFs
uvicorn services.api.main:app --port 8000   # review API  → http://localhost:8000/docs
cd apps/web && npm install && npm run dev   # review UI   → http://localhost:3000
```
Switch identity in the sidebar (dev auth) to try maker → checker: e.g. approve the
*Asia Pacific Logistics* invoice as **Nimali Silva**, then check it as **Kasun Fernando**.
Ops dashboard: **Anushka Wijesinghe** (supervisor).

Demo scenarios: auto-pass, the TSD wireframe invoice (ambiguous date, closed period, +0.50
diff, unmatched line), USD above LKR 1M, exact re-scan duplicate, near duplicate
(KP-1023 vs KP-1023A), blocked vendor, price/qty variance, low confidence only, unreadable PDF.

## Layout (TSD §10)
```
apps/web/          Next.js 15: review queue, workspace, checker view, ops dashboard
services/ingest/   Kafka consumer → inbox (dedupe by message id + file hash)
services/worker/   extract → normalise → enrich → validate → route; lease sweeper
services/api/      review, validate, decision, checker, ops endpoints
services/relay/    outbox → ERP adapter (the only ERP writer)
libs/rules/        pure, versioned rule engine (no I/O)
libs/core/         normalisation, enrichment, routing, SLA, review workflow
libs/erp_adapter/  protocol + fake ERP with fault injection
libs/llm_gateway/  structured outputs, token bucket, circuit breaker, fallback model
libs/db/           SQLAlchemy models, state machine, Alembic migrations
evals/             labelled invoices + scoring (Phase 2)
```

## Commands
`make test` (ruff + mypy + 68 pytest incl. relay-crash chaos test) · `make migrate` ·
`make seed` · `make api` · `make worker` · `make relay` · `make web`.
Set `ANTHROPIC_API_KEY` to extract real PDFs (primary `claude-haiku-5-5`, fallback
`claude-sonnet-5-5`, configurable in `.env`).

## Not yet (later phases)
OIDC SSO (dev header auth today) · Postgres + `SKIP LOCKED` load test · Redis token bucket
across workers · OCR for scanned pages · daily reconciliation job · OpenTelemetry · S3/Blob
store · Playwright + axe-core CI · config-admin UI for versioned thresholds.
