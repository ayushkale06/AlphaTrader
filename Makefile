PYTHON ?= python
DOCKER_COMPOSE ?= docker compose --env-file .env -f deploy/docker/docker-compose.yml

.PHONY: help install lint format typecheck schemas schemas-check test test-unit test-integration check healthcheck up down reset logs ps psql redis-cli topics smoke

help:
	@$(PYTHON) -c "print('AlphaTrader Targets:\n  make install          - Install runtime and dev dependencies in editable mode\n  make lint             - Run Ruff linter and format check\n  make format           - Format codebase with Ruff\n  make typecheck        - Run Mypy in strict mode on src/common and scripts\n  make schemas          - Export JSON Schemas to docs/contracts/ and docs/contracts.md\n  make schemas-check    - Verify committed JSON Schemas and docs/contracts.md are current\n  make test             - Run Pytest suite with coverage enforcement\n  make test-unit        - Run unit tests only\n  make test-integration - Run integration tests against local infrastructure\n  make check            - Run all quality gates (lint, typecheck, schemas-check, test)\n  make healthcheck      - Execute the AlphaTrader CLI healthcheck\n  make up               - Start local Docker infrastructure (PostgreSQL, Redis, Redpanda)\n  make down             - Stop local Docker infrastructure\n  make reset            - Tear down containers and destroy named data volumes\n  make ps               - Show status of local infrastructure containers\n  make logs             - Tail infrastructure container logs\n  make psql             - Open interactive psql shell in PostgreSQL container\n  make redis-cli        - Open interactive redis-cli shell in Redis container\n  make topics           - Create all Kafka topics from TOPIC_REGISTRY\n  make smoke            - Run end-to-end infrastructure smoke test')"

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m ruff format --check src tests scripts

format:
	$(PYTHON) -m ruff check --fix src tests scripts
	$(PYTHON) -m ruff format src tests scripts

typecheck:
	$(PYTHON) -m mypy --strict src/common scripts src/mcp_servers

schemas:
	$(PYTHON) -m scripts.export_schemas
	$(PYTHON) -m scripts.create_topics --generate-init-script

schemas-check:
	$(PYTHON) -m scripts.export_schemas --check

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest -m "not integration"

test-integration:
	$(PYTHON) -m pytest -m integration --no-cov

healthcheck:
	$(PYTHON) -m src.common.healthcheck

up:
	$(DOCKER_COMPOSE) up -d

down:
	$(DOCKER_COMPOSE) down

reset:
	$(DOCKER_COMPOSE) down -v

ps:
	$(DOCKER_COMPOSE) ps

logs:
	$(DOCKER_COMPOSE) logs -f

psql:
	$(DOCKER_COMPOSE) exec postgres psql -U alphatrader -d alphatrader_ledger

redis-cli:
	$(DOCKER_COMPOSE) exec redis redis-cli

topics:
	$(PYTHON) -m scripts.create_topics --generate-init-script
	$(PYTHON) -m scripts.create_topics

smoke:
	$(PYTHON) -m scripts.smoke_test

stream:
	$(PYTHON) -m src.mcp_servers.market_data_streamer.streamer

mcp-market:
	$(PYTHON) -m src.mcp_servers.market_data_streamer.server

check: lint typecheck schemas-check test


