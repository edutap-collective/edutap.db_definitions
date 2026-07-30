.DEFAULT_GOAL := help
VENV := .venv
PYTHON := $(VENV)/bin/python

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'

lint: ## Run ruff checks and the type checker
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m ty check src

reformat: ## Autoformat and autofix
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

test-local: ## Unit tests, no database needed
	$(PYTHON) -m pytest -v

test-integration: ## Integration tests against a PostgreSQL container
	$(PYTHON) -m pytest -m integration -v
