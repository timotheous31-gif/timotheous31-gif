# OSINT Investigation Platform — development commands
.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND  := backend
FRONTEND := frontend
VENV     := $(BACKEND)/.venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
COMPOSE  := docker compose

.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend \
        worker test test-backend test-frontend cov lint lint-fix format typecheck \
        migrate migration seed cli docker-up docker-down docker-build docker-logs \
        clean check

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: install-backend install-frontend ## Install backend and frontend dependencies

install-backend: ## Create the venv and install the backend (editable, with dev extras)
	python3.12 -m venv $(VENV) 2>/dev/null || python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	$(VENV)/bin/pre-commit install || true

install-frontend: ## Install frontend dependencies
	cd $(FRONTEND) && npm install --no-audit --no-fund

dev: ## Run backend and frontend dev servers together
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend: ## Run the API with autoreload on :8000
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run the Next.js dev server on :3000
	cd $(FRONTEND) && npm run dev

worker: ## Run a Celery worker
	cd $(BACKEND) && .venv/bin/celery -A app.workers.celery_app.celery_app worker --loglevel=info

test: test-backend test-frontend ## Run backend and frontend test suites

test-backend: ## Run backend tests
	cd $(BACKEND) && .venv/bin/python -m pytest

test-frontend: ## Run frontend tests and typecheck
	cd $(FRONTEND) && npm run typecheck && npm test

cov: ## Run backend tests with a coverage report
	cd $(BACKEND) && .venv/bin/python -m pytest --cov=app --cov-report=term-missing --cov-report=html

lint: ## Run ruff, black --check and mypy over the backend and scripts
	cd $(BACKEND) && .venv/bin/ruff check . && \
	  .venv/bin/ruff check --config pyproject.toml ../scripts && \
	  .venv/bin/black --check . && \
	  .venv/bin/black --check --config pyproject.toml ../scripts && \
	  .venv/bin/mypy app

lint-fix: ## Auto-fix lint issues and format
	cd $(BACKEND) && .venv/bin/ruff check --fix . && \
	  .venv/bin/ruff check --fix --config pyproject.toml ../scripts && \
	  .venv/bin/black . && .venv/bin/black --config pyproject.toml ../scripts

format: lint-fix ## Alias for lint-fix

typecheck: ## Run mypy only
	cd $(BACKEND) && .venv/bin/mypy app

check: lint test ## Lint and test — what CI runs

migrate: ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

migration: ## Create a migration: make migration m="add table"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

seed: ## Load the safe demonstration case (fictional targets only)
	$(PY) scripts/seed.py --reset

cli: ## Show the CLI help
	cd $(BACKEND) && .venv/bin/osint --help

docker-build: ## Build all container images
	$(COMPOSE) build

docker-up: ## Start the full stack
	$(COMPOSE) up -d --build
	@echo "API      http://localhost:8000/docs"
	@echo "Frontend http://localhost:3000"

docker-down: ## Stop the stack
	$(COMPOSE) down

docker-logs: ## Follow container logs
	$(COMPOSE) logs -f --tail=100

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache \
	       $(BACKEND)/htmlcov $(BACKEND)/.coverage $(FRONTEND)/.next
