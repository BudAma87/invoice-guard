.PHONY: install infra-up infra-down fmt lint typecheck test migrate seed api worker relay ingest web web-install

PY ?= uv run

install:
	uv sync --all-extras
	pre-commit install

infra-up:
	docker compose up -d

infra-down:
	docker compose down

fmt:
	$(PY) ruff format . && $(PY) ruff check --fix .

lint:
	$(PY) ruff check . && $(PY) ruff format --check .

typecheck:
	$(PY) mypy

test: lint typecheck
	$(PY) pytest -q

migrate:
	$(PY) alembic upgrade head

seed:            ## wipe ./data and seed demo invoices through the real pipeline
	$(PY) python -m scripts.seed_demo

api:
	$(PY) uvicorn services.api.main:app --reload --port 8000

worker:
	$(PY) python -m services.worker.pipeline

relay:
	$(PY) python -m services.relay.relay

ingest:
	$(PY) python -m services.ingest.consumer

web-install:
	cd apps/web && npm install

web:
	cd apps/web && npm run dev
