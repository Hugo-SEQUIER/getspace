.PHONY: setup backend web memory-worker webhook-worker install check check-database api-reference \
	check-memory-models benchmark-memory evaluate-retrieval up down logs backup

BACKEND_PORT ?= 8000
BACKEND_WORKERS ?= 1
STRATEGY ?= exact
WEB_PORT ?= 3000
SPACE_WEB_URL ?= http://127.0.0.1:$(WEB_PORT)
export BACKEND_PORT
export BACKEND_WORKERS
export SPACE_WEB_URL

# --- Self-hosting (Docker) -------------------------------------------------------

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

backup:
	scripts/backup.sh

# --- Development ----------------------------------------------------------------

setup:
	uv sync --directory backend --locked --group dev
	npm --prefix apps/web ci

# Create the restricted logins and apply migrations, from backend/.env.
install:
	uv run --directory backend --locked python -m space.install

backend:
	uv run --directory backend --locked uvicorn space.app:create_app --factory --host 127.0.0.1 --port "$(BACKEND_PORT)" --workers "$(BACKEND_WORKERS)" --no-access-log

web:
	npm --prefix apps/web run dev -- --port "$(WEB_PORT)"

memory-worker:
	uv run --directory backend --locked python -m space.job_worker $(ARGS)

webhook-worker:
	uv run --directory backend --locked python -m space.webhook_worker --loop $(ARGS)

# Regenerate docs/openapi.json after changing public routes or schemas.
api-reference:
	uv run --directory backend --locked python -m space.api.reference

check:
	uv run --directory backend --locked ruff check src tests migrations scripts
	uv run --directory backend --locked ruff format --check src tests migrations scripts
	uv run --directory backend --locked pytest
	npm --prefix apps/web test
	npm --prefix apps/web run build
	npm --prefix apps/web run typecheck

# Database tests start disposable pgvector/pgvector:pg17 containers; Docker is required.
check-database:
	SPACE_RUN_DATABASE_TESTS=1 uv run --directory backend --locked pytest

check-memory-models:
	uv run --directory backend --locked python scripts/check_memory_models.py

benchmark-memory:
	uv run --directory backend --locked python scripts/benchmark_memory.py

evaluate-retrieval:
	uv run --directory backend --locked python scripts/evaluate_retrieval.py \
		--corpus evaluations/retrieval/corpus.json --strategy "$(STRATEGY)"
