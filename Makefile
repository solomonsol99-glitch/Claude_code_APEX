.DEFAULT_GOAL := help
UV ?= uv
TYPED_PACKAGES := packages/core/src packages/net/src packages/security/src packages/engine_adapter/src packages/reporting/src

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'

install: ## Install workspace + dev tools
	$(UV) sync --all-packages
	$(UV) run pre-commit install

lint: ## ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy --strict on packages/*
	$(UV) run mypy $(TYPED_PACKAGES)

test: ## Unit, integration (needs Postgres binaries or DATABASE_URL), contract, license, red-team
	$(UV) run pytest

cov: ## Tests with coverage gate on the core packages
	$(UV) run pytest --cov=packages --cov-report=term-missing --cov-fail-under=85

engine-image: ## Build the pinned MiroFish engine image
	docker build -t coreset/mirofish-engine:dev engines/mirofish

up: ## Start the local stack (copy infra/compose/.env.example to .env first)
	docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml up -d

spike-up: ## Phase 0: engine + fake LLM, no spend
	docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile engine --profile spike up -d engine-1 fake-llm

live-run: ## Phase 0 live run behind the egress proxy (needs ZEP_API_KEY, LLM_* env; spends)
	infra/compose/phase0-live-run.sh

down: ## Stop the local stack
	docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile engine --profile spike down

.PHONY: help install lint fmt typecheck test cov engine-image up spike-up live-run down
