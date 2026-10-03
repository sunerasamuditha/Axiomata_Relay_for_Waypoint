# Relay developer commands. `make` (or `make help`) lists them.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV       ?= uv
PNPM     ?= pnpm
API_PORT ?= 8000
DB_PORT  ?= $(or $(shell sed -n 's/^DB_PORT_HOST=//p' .env 2>/dev/null),5433)
E2E_BASE_URL ?= http://localhost:5173
RELOAD_DIRS := --reload-dir apps/api/relay_api --reload-dir packages/engine/relay_engine --reload-dir packages/ml/relay_ml

.PHONY: help doctor bootstrap db db-stop db-reset migrate api web dev up down logs test test-api test-engine \
        lint format typecheck check e2e-install e2e e2e-smoke e2e-docker check-plan reset-demo \
        train forecast profiles task2b build deploy reset-demo-prod smoke-prod clean

help: ## List the commands
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---- setup ---------------------------------------------------------------------------------------
doctor: ## Check every tool this repo needs and print versions
	@./scripts/doctor.sh

bootstrap: ## Install Python + Node dependencies and create .env (run once)
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	$(UV) sync
	$(PNPM) install

# ---- local development ---------------------------------------------------------------------------
db: ## Start Postgres in Docker on :5433 (skipped if something already listens there)
	@if (exec 3<>/dev/tcp/127.0.0.1/$(DB_PORT)) 2>/dev/null; then \
	  echo "Postgres is already listening on :$(DB_PORT)"; \
	else \
	  docker compose up -d db && printf "waiting for Postgres" && \
	  until docker compose exec -T db pg_isready -U relay -d relay >/dev/null 2>&1; do printf "."; sleep 1; done && echo " ready"; \
	fi

db-stop: ## Stop Postgres (keeps the data)
	docker compose stop db

db-reset: ## Delete the local database volume and start from an empty Postgres
	docker compose down -v
	$(MAKE) migrate

migrate: db ## Apply database migrations
	$(UV) run alembic -c apps/api/alembic.ini upgrade head

api: migrate ## API with auto-reload on :8000 (docs at /api/docs)
	$(UV) run uvicorn relay_api.main:app --reload --port $(API_PORT) $(RELOAD_DIRS)

web: ## Web app with hot reload on :5173 (proxies /api to :8000)
	$(PNPM) dev

dev: migrate ## API + web together; Ctrl+C stops both
	@$(UV) run uvicorn relay_api.main:app --reload --port $(API_PORT) $(RELOAD_DIRS) & api=$$!; \
	trap 'kill $$api 2>/dev/null; wait $$api 2>/dev/null' EXIT INT TERM; \
	$(PNPM) dev

# ---- the full stack, the way judges run it -------------------------------------------------------
up: ## docker compose up --build (http://localhost:8080)
	docker compose up --build

down: ## Stop the compose stack (add -v yourself to wipe the database)
	docker compose down

logs: ## Follow the app container's logs
	docker compose logs -f app

# ---- quality ---------------------------------------------------------------------------------------
test: db ## Python tests: engine, ML and the API flows (needs Postgres, started for you)
	$(UV) run pytest

test-engine: ## Engine tests only (no database)
	$(UV) run pytest packages/engine/tests packages/ml/tests

lint: ## Ruff + ESLint + Prettier check
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(PNPM) lint
	$(PNPM) format:check

format: ## Format Python (ruff) and TypeScript/CSS (prettier)
	$(UV) run ruff format .
	$(UV) run ruff check --fix .
	$(PNPM) format

typecheck: ## TypeScript type check (web + e2e)
	$(PNPM) typecheck

check: lint typecheck test check-plan ## Everything CI runs except the browser tests

check-plan: db ## Plan the demo day under every policy and validate it with our port of check_allocation.py
	$(UV) run python -m relay_api.check_plan

# ---- browser tests -------------------------------------------------------------------------------
e2e-install: ## Download the Chromium build Playwright uses (once)
	$(PNPM) --filter @relay/e2e exec playwright install chromium

e2e: ## Four-role Playwright walkthrough + smoke (needs `make dev` running)
	E2E_BASE_URL=$(E2E_BASE_URL) $(PNPM) e2e

e2e-smoke: ## Quick smoke tests only
	E2E_BASE_URL=$(E2E_BASE_URL) $(PNPM) --filter @relay/e2e test:smoke

e2e-docker: ## Playwright against `docker compose up` on :8080
	E2E_BASE_URL=http://localhost:8080 $(PNPM) e2e

# ---- demo ----------------------------------------------------------------------------------------
reset-demo: db ## Wipe and reseed the local shared demo workspace (open browsers reload)
	$(UV) run python -m relay_api.seed --reset main

# ---- data and models (need the private competition CSVs in data/private/) ------------------------
train: ## Train the service-time and lateness models into packages/ml/models/
	$(UV) run python -m relay_ml.train

forecast: ## Rebuild data/demo/forecast_weekly.json (weekly aggregates only)
	$(UV) run python -m relay_ml.forecast

profiles: ## Rebuild data/demo/outlet_profiles.json (per-outlet aggregates only)
	$(UV) run python -m relay_ml.profiles

task2b: ## Plan the Datathon S1 peak day with the same engine and validate it
	$(UV) run python -m relay_engine.task2b

# ---- Google Cloud --------------------------------------------------------------------------------
build: ## Build the production image locally for linux/amd64 (what Cloud Run runs)
	docker buildx build --platform linux/amd64 -t relay-app:amd64 --load .

deploy: ## Build with Cloud Build and deploy to Cloud Run (see docs/DEPLOY_GCP.md)
	./infra/gcp/deploy.sh

reset-demo-prod: ## Reset the shared demo on the live service
	./infra/gcp/reset-demo.sh

smoke-prod: ## Run the smoke tests against the live service
	@source infra/gcp/env.sh && URL="$$(gcloud run services describe "$$SERVICE" --region "$$REGION" --format='value(status.url)')" && \
	E2E_BASE_URL="$$URL" $(PNPM) --filter @relay/e2e test:smoke

clean: ## Remove build output and caches (keeps dependencies and the database)
	rm -rf apps/web/dist apps/web/dev-dist e2e/test-results e2e/playwright-report .pytest_cache .ruff_cache
	find apps packages -name __pycache__ -type d -prune -exec rm -rf {} +
