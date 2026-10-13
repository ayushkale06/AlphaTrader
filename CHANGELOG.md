# Changelog

All notable changes to **AlphaTrader** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0-alpha] - 2026-10-13

### Added
- Strictly typed Pydantic v2 domain schema package (`src/common/schemas/`):
  - `base.py`: `AlphaTraderBaseModel` (`frozen=True`, `extra="forbid"`), `UtcDatetime` validator rejecting naive timestamps, `Decimal` financial types (`PositivePrice`, `NonNegativePrice`, `PositiveQuantity`, `NonNegativeQuantity`, `SignedMoney`, `NonNegativeMoney`, `BoundedFraction`, `SignedWeight`) rejecting binary `float` and non-finite `Decimal`, and `NseSymbol` validator.
  - `envelope.py`: Generic `EventEnvelope[PayloadT]` wrapper with `event_id`, `correlation_id`, `causation_id`, `schema_version` (`1.0.0` major-version guard), `produced_at` (`UtcDatetime`), `source` (`ComponentSource` enum), and deterministic JSON bytes serialization (`to_json_bytes` / `from_json_bytes`).
  - `market.py`: `Tick`, `Candle` (`CandleInterval` enum + OHLC bar invariants), and `MarketStatus` (`MarketSessionState` enum + NSE `Asia/Kolkata` session classification helper `MarketStatus.from_timestamp`).
  - `signals.py`: `Direction`, `IndicatorSnapshot`, `TechnicalSignal`, and `QuantSignal` with directional and edge consistency validators.
  - `risk.py`: `RiskVerdict` (`APPROVE`, `REJECT`, `HALT`), `RiskRuleId`, `RiskReasonCode`, `RiskInputSnapshot`, `RiskDecision`, and `HaltEvent`.
  - `orders.py`: `Side`, `OrderType`, `OrderStatus` state machine enum, `validate_order_status_transition`, `IllegalOrderTransitionError`, `OrderRequest` (with `idempotency_key`), `Order`, and `Fill`.
  - `portfolio.py`: `Position` and `PortfolioSnapshot` (enforcing `equity == cash + gross_exposure` and drawdown consistency).
  - `system.py`: `SystemEventType`, `SystemSeverity`, and `SystemEvent`.
- Canonical Kafka/Redpanda topic contract registry (`src/common/contracts.py`) mapping all 9 topics (`market.ticks`, `market.candles`, `signals.technical`, `signals.quant`, `risk.decisions`, `orders.requests`, `orders.fills`, `portfolio.updates`, `system.events`) to payload/envelope models, partition counts, key extraction rules, retention, DLQ configuration, allowed producers/consumers, and `validate_topic_message()`.
- Contract artifact generator (`scripts/export_schemas.py`) exporting 23 JSON Schema Draft 2020-12 files to `docs/contracts/*.schema.json` and generating `docs/contracts.md` from live validated model instances, plus `make schemas` and `make schemas-check` CI drift gates.
- Unit, Hypothesis property-based, and topic contract test suites (`tests/unit/test_schemas.py`, `tests/unit/test_schemas_property.py`, `tests/unit/test_contracts.py`) raising coverage on `src/common` to 98.88% (with `--cov-fail-under=95`).
- `ADR-003: Typed Event Contracts, Decimal Financial Types, and Topic Registry` (`docs/adr/003-typed-event-contracts.md`) and updated `docs/architecture.md` with Mermaid core model class diagram and `OrderStatus` state machine diagram.

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
