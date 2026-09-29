SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND := backend
FRONTEND := frontend
EVALS := evals
TF := infra/terraform
API_URL ?= http://localhost:8000

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------- Setup ----------
install: ## Install backend, evals and frontend dependencies
	cd $(BACKEND) && uv sync --all-groups
	cd $(EVALS) && uv sync
	cd $(FRONTEND) && pnpm install

# ---------- Local stack ----------
up: ## Start the full stack (qdrant, dynamodb, backend, frontend) in Docker
	docker compose up -d --build qdrant dynamodb backend frontend

down: ## Stop the local stack
	docker compose down

logs: ## Tail backend logs
	docker compose logs -f backend

ingest: ## Run the ingestion job in Docker against the compose Qdrant
	docker compose --profile tools run --rm ingest

infra-local: ## Start only qdrant + dynamodb (for running backend/frontend natively)
	docker compose up -d qdrant dynamodb

ingest-local: ## Run ingestion natively against localhost Qdrant
	cd $(BACKEND) && uv run python -m app.ingestion.run --recreate

dev-backend: ## Run the API natively with reload
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run Next.js dev server
	cd $(FRONTEND) && pnpm dev

# ---------- Quality ----------
lint: ## Lint backend + frontend
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && pnpm lint

typecheck: ## Type-check backend
	cd $(BACKEND) && uv run mypy app

test: ## Run backend unit + RBAC leakage tests
	cd $(BACKEND) && uv run pytest -q

# ---------- Evals ----------
evals-fast: ## Security gates + small Ragas sample (used in PR CI)
	cd $(EVALS) && uv run python run_evals.py --api-url $(API_URL) --suite fast

evals: ## Full eval suite (used after every deploy)
	cd $(EVALS) && uv run python run_evals.py --api-url $(API_URL) --suite full

# ---------- AWS ----------
tf-init: ## terraform init
	cd $(TF) && terraform init

tf-plan: ## terraform plan
	cd $(TF) && terraform plan

tf-apply: ## terraform apply
	cd $(TF) && terraform apply

destroy: ## Tear down all AWS resources (stops all cost)
	cd $(TF) && terraform destroy

.PHONY: help install up down logs ingest infra-local ingest-local dev-backend dev-frontend lint typecheck test evals-fast evals tf-init tf-plan tf-apply destroy
