.PHONY: install infra-up infra-down lint typecheck test api worker relay fmt

install:
	cd backend && uv sync --all-extras
	pre-commit install

infra-up:
	docker compose up -d

infra-down:
	docker compose down

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd backend && uv run mypy src

test: lint typecheck
	cd backend && uv run pytest -q

api:
	cd backend && uv run uvicorn invoice_guard.api.main:app --reload --port 8000

worker:
	cd backend && uv run python -m invoice_guard.workers.consumer

relay:
	cd backend && uv run python -m invoice_guard.workers.relay
