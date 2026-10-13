PYTHON ?= python

.PHONY: help install lint format typecheck schemas schemas-check test check healthcheck

help:
	@$(PYTHON) -c "print('AlphaTrader Quality Gate Targets:\n  make install       - Install runtime and dev dependencies in editable mode\n  make lint          - Run Ruff linter and format check\n  make format        - Format codebase with Ruff\n  make typecheck     - Run Mypy in strict mode on src/common and scripts\n  make schemas       - Export JSON Schemas to docs/contracts/ and docs/contracts.md\n  make schemas-check - Verify committed JSON Schemas and docs/contracts.md are current\n  make test          - Run Pytest suite with coverage enforcement\n  make check         - Run all quality gates (lint, typecheck, schemas-check, test)\n  make healthcheck   - Execute the AlphaTrader CLI healthcheck')"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m ruff format --check src tests scripts

format:
	$(PYTHON) -m ruff check --fix src tests scripts
	$(PYTHON) -m ruff format src tests scripts

typecheck:
	$(PYTHON) -m mypy --strict src/common scripts

schemas:
	$(PYTHON) -m scripts.export_schemas

schemas-check:
	$(PYTHON) -m scripts.export_schemas --check

test:
	$(PYTHON) -m pytest

healthcheck:
	$(PYTHON) -m src.common.healthcheck

check: lint typecheck schemas-check test
