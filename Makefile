# Tools run through .venv/bin/python, not `uv run`: a bare `uv run` locks the
# whole project including the org-internal extras (edutap.data_provider,
# edutap.pass_builder), which no public index carries, so resolution fails.
# CI and tox are unaffected — they install the `dev` extra explicitly.

.DEFAULT_GOAL := help
.PHONY: help venv lint reformat test-local test-integration
VENV := .venv
PYTHON := $(VENV)/bin/python

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Create .venv and install the package with its dev extra
	test -d $(VENV) || uv venv
	uv pip install -U -e ".[dev]"

lint: venv ## Run ruff checks and the type checker
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m ty check src

reformat: venv ## Autoformat and autofix
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

test-local: venv ## Unit tests, no database needed
	$(PYTHON) -m pytest -v

test-integration: venv ## Integration tests against a PostgreSQL container
	$(PYTHON) -m pytest -m integration -v
