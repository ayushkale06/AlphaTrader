PYTHON ?= python

.PHONY: help install lint format typecheck test check healthcheck

help:
	@$(PYTHON) -c "print('AlphaTrader Quality Gate Targets:\n  make install     - Install runtime and dev dependencies in editable mode\n  make lint        - Run Ruff linter and format check\n  make format      - Format codebase with Ruff\n  make typecheck   - Run Mypy in strict mode on src/common\n  make test        - Run Pytest suite with coverage enforcement\n  make check       - Run all quality gates (lint, typecheck, test)\n  make healthcheck - Execute the AlphaTrader CLI healthcheck')"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff check --fix src tests
	$(PYTHON) -m ruff format src tests

typecheck:
	$(PYTHON) -m mypy --strict src/common

test:
	$(PYTHON) -m pytest

healthcheck:
	$(PYTHON) -m src.common.healthcheck

check: lint typecheck test
