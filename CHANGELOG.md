# Changelog

All notable changes to **AlphaTrader** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0-alpha] - 2026-10-13

### Added
- Clean-slate repository structure for Sprint 1 Foundation (`src/orchestrator`, `src/agents/*`, `src/mcp_servers/*`, `src/common`, `src/observability`, `deploy/docker`, `deploy/k8s`, `docs/adr`, `scripts`, `tests/unit`, `tests/integration`).
- Single-responsibility `README.md` specifications for all six Model Context Protocol (MCP) microservices:
  1. `market_data_streamer`
  2. `technical_analyst`
  3. `quant_strategy_engine`
  4. `risk_guardrail` (deterministic, non-LLM)
  5. `paper_execution_broker`
  6. `portfolio_ledger`
- Typed runtime settings in `src/common/config.py` (`AlphaTraderSettings`) backed by `pydantic-settings` with code-enforced ADR-002 paper-trading locks (`TRADING_MODE=PAPER`, `LIVE_TRADING_ENABLED=False`), risk guardrail parameter bounds, masked PostgreSQL DSN builder, and `.env.example`.
- Structured single-line JSON logging in `src/common/logging.py` with automatic `contextvars.ContextVar` `correlation_id` propagation across log lines.
- CLI healthcheck entry point (`python -m src.common.healthcheck`) verifying package version, configuration validity, and paper-trading safety locks.
- Unit test suite (`tests/unit/test_config.py`, `tests/unit/test_logging.py`, `tests/unit/test_healthcheck.py`) achieving 98.37% coverage on `src/common`.
- Tooling and CI quality gates: `pyproject.toml`, `Makefile` (`install`, `lint`, `format`, `typecheck`, `test`, `check`, `healthcheck`), `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.editorconfig`, `.gitignore`, and MIT `LICENSE`.
- Architecture and project documentation: `README.md`, 24-sprint `ROADMAP.md`, `docs/architecture.md` (with Mermaid component, tick-to-fill sequence, and risk-halt diagrams), `ADR-001`, `ADR-002`, `CONTRIBUTING.md`, and `DEV_LOG.md`.
