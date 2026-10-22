.DEFAULT_GOAL := help
UV ?= uv
COMMA := ,
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

engine-image-sandbox: ## Sandbox only: add torch's locked CUDA libs when download.pytorch.org is blocked
	docker build $(if $(REGISTRY_PREFIX),--build-arg REGISTRY_PREFIX=$(REGISTRY_PREFIX)) \
	  $(if $(EXTRA_CA_BUNDLE),--secret id=extra_ca$(COMMA)src=$(EXTRA_CA_BUNDLE)) \
	  -t coreset/mirofish-engine:dev -f infra/live/Dockerfile.sandbox-cuda infra/live

live-preflight: ## Phase 0: check image (torch, oasis), Zep key and LLM key; no spend
	PREFLIGHT_ONLY=1 infra/compose/phase0-live-run.sh

live-run: ## Phase 0 live run: 10 agents x 3 rounds, real LLM + Zep behind deny-all egress (SPENDS, capped)
	infra/compose/phase0-live-run.sh

down: ## Stop the local stack
	docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml --profile engine --profile spike down

.PHONY: help install lint fmt typecheck test cov engine-image up spike-up down engine-image-sandbox live-preflight live-run
