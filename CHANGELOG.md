# Changelog

All notable changes to **AlphaTrader** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-16

### Added
- **Full Containerization & Multi-Stage Production Runtime (Sprint 17)**:
  - Multi-stage distroless-style non-root Docker runtime (`deploy/docker/Dockerfile`) running as UID/GID `10001:10001` with built-in healthchecks.
  - Production Docker Compose manifest (`deploy/docker/docker-compose.full.yml`) connecting all 6 MCP servers, PostgreSQL 16, Redis 7, Redpanda 24.x, Prometheus, and Grafana.
- **Production Kubernetes & Helm Charts (Sprint 18)**:
  - Complete Helm deployment templates (`deploy/k8s/templates/`) including `deployment.yaml`, `configmap.yaml`, `secret.yaml`, `pdb.yaml`, and `networkpolicy.yaml` with read-only root filesystems, resource limits, and liveness/readiness probes.
- **Historical Backtesting Replay Runner & Institutional Tear Sheet (Sprints 19 & 20)**:
  - Event-driven zero-lookahead backtest replay runner (`scripts/run_backtest.py`) simulating chronological trading on historical NIFTY 50 blue-chip bars.
  - Institutional performance tear-sheet engine (`src/backtesting/tearsheet.py`) calculating Sharpe, Sortino, Calmar, MDD, win rates, and statutory Indian tax friction drag (STT, GST, stamp duty).
  - Unit test suite (`tests/unit/test_tearsheet.py`) with 100% test coverage.
- **Infrastructure Chaos & Adversarial Fuzzing Test Suites (Sprints 21 & 22)**:
  - Redis connection pause and network drop fail-closed tests (`tests/integration/chaos/test_infrastructure_chaos.py`).
  - Synthetic flash-crash (-5%) and circuit breaker tripping tests at exactly 2.0% daily drawdown (`tests/integration/chaos/test_flash_crash_and_adversarial.py`).
  - Rogue agent adversarial fuzzing verifying 100% rejection of inverted stop losses, excessive position concentration (> 5%), and order flooding (> 30 orders/min).
- **Cryptographic Audit Verification & Sub-Millisecond Latency Benchmarks (Sprint 23)**:
  - Cryptographic hash-chain verifier (`scripts/verify_audit_chain.py`) validating SHA-256 sequential integrity across immutable `risk_audit_log` records.
  - Latency benchmarking runner (`scripts/benchmark_latency.py`) verifying sub-millisecond SLAs (HMAC token signing < 15us, deterministic risk evaluation < 60us).
- **Turnkey Production Demonstration (Sprint 24)**:
  - Turnkey interactive demo runner (`scripts/run_turnkey_demo.py`) executing complete multi-agent pipeline traversal from live tick ingestion to double-entry ledger settlement and quantitative tear-sheet emission.

## [0.15.0-alpha] - 2026-10-16

### Added
- **Prometheus Operational & Financial Metrics (`src/observability/metrics.py`)**:
  - `AlphaTraderMetrics`: Registry and standard exposition formatter exposing real-time financial gauges (`alphatrader_equity_inr`, `alphatrader_portfolio_cash_inr`, `alphatrader_intraday_drawdown_pct`, `alphatrader_open_positions_count`), circuit breaker state (`alphatrader_risk_halt_state`), order counters (`alphatrader_orders_total`), and tick-to-fill latency histograms (`alphatrader_tick_to_fill_latency_seconds`).
  - Thread-safe `MetricsRegistry` with exact bucket distribution, sum/count metrics, and label serialization.
- **Automated Grafana Dashboards & Provisioning**:
  - Provisioned Grafana dashboard manifest `deploy/docker/grafana/dashboards/alphatrader-ops.json` visualizing live portfolio equity, intraday drawdown gauge, circuit breaker state, and p95 tick-to-fill latency.
  - Auto-provisioning manifests for Grafana dashboards and Prometheus datasource in `deploy/docker/grafana/provisioning/`.
- **Metrics Quality Gate & Unit Tests**:
  - Unit test suite (`tests/unit/test_metrics.py`) testing gauge updates, counter increments, histogram bucket distributions, and empty registry formatting.
  - Observability package coverage maintained at **94.80%** ($\ge 90\%$ gate satisfied).

## [0.14.0-alpha] - 2026-10-16

### Added
- **OpenTelemetry Distributed Tracing (`src/observability/tracing.py`)**:
  - `AlphaTraderTracer`: Central distributed tracing bridge integrating `opentelemetry.trace` with automatic `correlation_id` injection and non-negotiable paper trading invariants (`trading_mode: "PAPER"`).
  - Context manager `trace_span(name, attributes)` for tracing asynchronous microservice and MCP operations.
  - Decorator `@traced_tool(server_name, tool_name)` instrumenting MCP tool handlers with execution spans and symbol tags.
  - Decorator `@traced_node(node_name)` instrumenting LangGraph multi-agent workflow node transitions.
  - `InMemorySpanRecorder`: Fast, in-memory span buffer for deterministic trace assertion and distributed trace waterfall validation without external collector dependencies.
- **Observability Testing & CI Coverage Gate**:
  - Dedicated unit test suite (`tests/unit/test_tracing.py`) testing attributes, exceptions, decorators, and multi-stage trace waterfalls with **97.03% branch coverage** ($\ge 90\%$ gate satisfied).
  - Added `test-observability-coverage` target to `Makefile` and CI workflow `.github/workflows/ci.yml`.

## [0.13.0-alpha] - 2026-10-15

### Added
- **Consumer Offset Management & Crash Recovery (`src/mcp_servers/portfolio_ledger/consumer.py`)**:
  - Implemented `poll_and_process()` on `PortfolioLedgerConsumer` with explicit synchronous offset commit (`asynchronous=False`) only after successful double-entry ledger persistence and Redis projection.
  - Added partition assignment and revocation tracking (`_on_assign`, `_on_revoke`) to prevent committing unassigned partitions during rebalance events.
- **Ledger-to-Projection State Reconciler (`src/mcp_servers/portfolio_ledger/reconciliation.py`)**:
  - `PortfolioStateReconciler`: Startup and post-crash recovery service reading the authoritative PostgreSQL ledger under `REPEATABLE READ` transaction isolation and atomically rebuilding Redis projection caches (`alphatrader:portfolio:meta` and `alphatrader:portfolio:positions`).
  - Added CLI runner `scripts/reconcile_state.py` for manual/preflight state reconciliation.
- **Crash Recovery & DLQ Integration Test Suite (`tests/integration/test_crash_recovery.py`)**:
  - Validates consumer crash & restart mid-stream: repeated message delivery results in zero duplicate ledger entries and zero lost accounting postings.
  - Validates poison pill and malformed payload handling: invalid envelopes and short-selling violations are routed to `orders.fills.dlq` with complete diagnostic headers (`x-dlq-reason`, `x-original-topic`, `x-correlation-id`).
  - Verifies exact debit-credit balance preservation throughout crash recovery and re-delivery.

## [0.12.0-alpha] - 2026-10-15

### Added
- **End-to-End Tick-to-Paper-Fill Pipeline Integration (`tests/integration/test_tick_to_fill_e2e.py`)**:
  - Integration test verifying complete event trace continuity from synthetic NIFTY 50 tick ingestion (`market.ticks`), candle aggregation, through the 4-agent stateful orchestrator (`TradingFloorOrchestrator`), generating an executed paper fill (`orders.fills`), and posting to the PostgreSQL double-entry accounting ledger (`journal_entries`).
  - Verifies exact debit-credit balance preservation and immutable provenance propagation (`correlation_id`, `trigger_event_id`, `causation_id`) through every pipeline boundary.
  - Zero-float conversions using strictly typed `Decimal` arithmetic.
- **Tick Replay Pipeline Runner (`scripts/replay_pipeline.py`)**:
  - Standalone CLI runner simulating streaming ticks for NSE equities (e.g., `RELIANCE.NS`), aggregating 5-minute candles, evaluating orchestrator transitions, and reporting paper fills.

## [0.11.0-alpha] - 2026-10-15

### Added
- **Stateful Multi-Agent Orchestrator (`src/orchestrator/`)**:
  - `TradingFloorOrchestrator` (`src/orchestrator/graph.py`): Stateful directed workflow machine coordinating `AnalystAgent`, `QuantAgent`, `RiskAgent`, and `ExecutionAgent`.
  - `TradingFloorState` (`src/orchestrator/state.py`): Typed state dataclass tracking `OrchestratorStage`, market inputs, agent artifacts, timestamps, and unbroken event lineage (`correlation_id`, `trigger_event_id`, `causation_id`).
  - `OrchestratorCheckpointer` (`src/orchestrator/checkpoint.py`): Persistent state snapshot serializer to Redis under `alphatrader:orchestrator:state:<correlation_id>` with in-memory fallback.
  - Dedicated unit test suite (`tests/unit/test_orchestrator.py`) verifying all conditional edges (flat signal, zero sizing, risk rejection, floor halt, broker rejection) with 98.74% branch coverage ($\ge 90\%$ gate satisfied).
  - Pre-flight floor circuit-breaker evaluation checking `alphatrader:floor_halted` before graph traversal.
  - Hard paper-trading mode invariant enforcing `TRADING_MODE="PAPER"` (ADR-002).
- **Audit Defect Resolutions (Sprint 11 Findings)**:
  - Augmented `PaperExecutionBroker.get_order_status` in Server 5 to index orders by both `order_id` and client `order_request_id` (`orders_by_request_id`), enabling deterministic status recovery after ambiguous dispatch timeouts.
  - Augmented `PaperExecutionBroker.execute_order` and `publish_fill` to accept and preserve `trigger_event_id` as the fill envelope's `causation_id`, resolving event provenance lineage.
- **Architectural Documentation**:
  - `docs/adr/012-stateful-multi-agent-directed-orchestrator.md` capturing orchestrator graph topology, short-circuit rules, and state checkpointing.

## [0.10.0-alpha] - 2026-10-15

### Added
- **Specialized Autonomous Agents (`src/agents/`)**:
  - `AnalystAgent` (`src/agents/analyst/`): Wraps MCP Server 2 (`technical-analyst`) to evaluate technical indicators, determine market regime (`MarketRegime`), and output structured `TechnicalSignal`s.
  - `QuantAgent` (`src/agents/quant/`): Wraps MCP Server 3 (`quant-strategy-engine`) to score multi-factor alpha models, apply ATR bracket stop-loss and fractional-Kelly sizing, and construct candidate `OrderRequest`s.
  - `RiskAgent` (`src/agents/risk/`): Wraps MCP Server 4 (`risk-guardrail`) to evaluate pre-trade guardrails, concentration limits, and circuit breakers, retrieving signed HMAC `RiskApprovalToken`s with zero LLM override authority.
  - `ExecutionAgent` (`src/agents/execution/`): Wraps MCP Server 5 (`paper_execution_broker`) to match paper orders, compute exact statutory taxes, and perform safe ambiguous-timeout reconciliation.
  - Base abstraction layer (`src/agents/base.py`) defining typed request/result dataclasses and `BaseAgent`.
  - Unified MCP client abstraction (`src/agents/mcp_client.py`) supporting both in-memory test mocks and live client sessions.
  - Comprehensive unit test suite (`tests/unit/test_agents.py`) verifying all four agents, provenance tracking, and error branches with 93.96% branch coverage ($\ge 90\%$ gate satisfied).
  - Dedicated 10,000-fill stress test (`tests/unit/test_portfolio_ledger_stress.py`) verifying exact debit-credit equality at scale.
- **Architectural Documentation**:
  - `docs/adr/011-individual-autonomous-agents-and-mcp-contracts.md` defining autonomous agent contracts, provenance tracking, and security invariants.

## [0.9.0-alpha] - 2026-10-15

### Added
- **MCP Server 6: Portfolio Ledger (`src/mcp_servers/portfolio_ledger`)**:
  - PostgreSQL-backed system-of-record double-entry ledger (`ledger.py`) strictly enforcing $\sum \text{Debits} \equiv \sum \text{Credits}$ across all journal entries.
  - Initial paper seed capital funding (`initialize_genesis_funding`) recording an auditable INR 1,000,000.00 funding entry with idempotent restart protection (`GENESIS_EVENT_ID`).
  - Strict no-short cash delivery policy: sell fills exceeding available inventory are rejected (`InsufficientInventoryError`) and routed to `orders.fills.dlq`.
  - Immediate fee and statutory tax expensing (STT, exchange charges, SEBI fees, stamp duty, GST) excluded from inventory Weighted Average Cost (WAC).
  - Exact inventory book cost reconciliation: full liquidation credits the exact remaining book balance of `Asset:Inventory:{SYMBOL}`, returning inventory identically to 0.0000.
  - Ephemeral Redis read projection (`projection.py`) under `alphatrader:portfolio:*` with atomic compare-and-set using 20-digit zero-padded revision strings, stale update rejection, and closed position deletion.
  - Kafka consumer loop (`consumer.py`) ingesting `orders.fills`, updating Redis projection, publishing `PortfolioSnapshot` to `portfolio.updates`, and routing unprocessable messages to `orders.fills.dlq`.
  - FastMCP Server 6 (`server.py`) exposing `get_portfolio_summary`, `get_open_positions`, `get_audit_trail`, and `get_ledger_health`.
  - Migration script `deploy/docker/migrations/003_portfolio_double_entry_ledger.sql` introducing deferred integrity constraint triggers and table immutability protections.
  - Comprehensive unit test suite (`tests/unit/test_portfolio_ledger.py`) achieving 90.85% code coverage across `src.mcp_servers.portfolio_ledger`.
- **Architectural Documentation**:
  - `docs/adr/010-postgresql-double-entry-portfolio-ledger.md` defining double-entry chart of accounts, deferred integrity triggers, WAC accounting, monotonic revisions, and failure modes.

## [0.8.0-alpha] - 2026-10-15

### Added
- **MCP Server 5: Paper Execution Broker (`src/mcp_servers/paper_execution_broker`)**:
  - Exact Indian statutory transaction tax engine (`costs.py`) computing STT, NSE exchange turnover fees, SEBI charges, stamp duty, and 18% GST with exact non-float `Decimal` precision matching `Fill.total_fees` invariant.
  - Simulated matching engine (`matcher.py`) modeling bid-ask spread-crossing and volume-scaled slippage with strict limit order execution bounds.
  - Cryptographic `RiskApprovalToken` verification enforcing token signature validity, non-tampering, expiration (60s TTL), and order parameter matching (`order_request_id`, `symbol`, `side`, `quantity`).
  - Synchronous verified Kafka publisher (`publisher.py`) delivering `EventEnvelope[Fill]` to topic `orders.fills` (`TopicName.ORDERS_FILLS`) with delivery report verification.
  - FastMCP Server 5 (`server.py`) exposing `execute_paper_order`, `get_paper_order_status`, and `get_broker_health`.
  - Comprehensive unit test suite (`tests/unit/test_paper_execution_broker.py`) achieving 95.11% code coverage.
- **Architectural Documentation**:
  - `docs/adr/009-paper-execution-engine-and-statutory-costs.md` defining matching simulation, risk token verification, and statutory fee accounting.

## [0.7.0-alpha] - 2026-10-15

### Added
- **MCP Server 4: Deterministic Risk Guardrail & Floor Circuit Breaker (`src/mcp_servers/risk_guardrail`)**:
  - Pure-Python non-LLM pre-trade risk rule evaluator (`evaluator.py`) validating single-position concentration ($\le 5.0\%$), max open positions ($\le 10$), order rate limiting ($\le 30/\text{min}$), and stop-loss distance validity ($[0.1\%, 15.0\%]$).
  - Cryptographic HMAC-SHA256 `RiskApprovalToken` issuer and verifier (`token.py`) binding order parameters with constant-time signature comparison and 60s TTL.
  - Real-time Redis risk state manager (`state.py`) with atomic Lua script updating equity, advancing High-Water Mark monotonically, computing drawdown, and latching `alphatrader:floor_halted = "1"` if drawdown $\ge 2.0\%$.
  - Session date rollover semantics in `Asia/Kolkata` with strict circuit breaker latching (halts never cleared by rollover or restart).
  - Stale and duplicate event protection rejecting out-of-order updates.
  - FastMCP Server 4 (`server.py`) exposing 3 tools: `validate_order_proposal`, `get_floor_risk_status`, and `trigger_manual_floor_halt`.
  - Verified Kafka publisher (`publisher.py`) delivering `RiskDecision` and `SystemEvent` (HALT) to `risk.decisions` and `system.events`.
  - Comprehensive unit test suites (`tests/unit/test_risk_guardrail.py`, `tests/unit/test_risk_state.py`) achieving 91.38% branch coverage with static AST proof of zero LLM imports.
- **Architectural Documentation**:
  - `docs/adr/008-deterministic-risk-guardrail-and-circuit-breaker.md` specifying risk state models, atomic transitions, and recovery invariants.

## [0.6.0-alpha] - 2026-10-14

### Added
- **Quant Strategy Engine & Backtesting Framework (`src/backtesting`, `src/mcp_servers/quant_strategy_engine`)**:
  - **Phase A (Backtesting & Cost Realism)**:
    - Provider-agnostic event-driven backtesting engine (`src/backtesting/engine.py`) strictly enforcing no-lookahead fill execution on bar $t+1$ Open.
    - Full Indian equity cash delivery transaction cost model (`src/backtesting/costs.py`) computing exact brokerage, STT, exchange turnover fees, SEBI charges, stamp duty, GST, and linear slippage in exact `Decimal` arithmetic.
    - Historical daily data loader (`src/backtesting/data_loader.py`) validating corporate actions and price continuity.
    - Comprehensive test suite (`tests/unit/test_backtesting.py`) covering 5-bar exact hand-calculated accounting, 200-seed Monte Carlo null tests on gross/net returns, and known-edge injection.
  - **Phase B (Quant Strategy Engine & FastMCP Server)**:
    - Versioned deterministic strategy registry (`src/mcp_servers/quant_strategy_engine/strategies.py`) implementing `ema_trend_following`, `rsi_bollinger_mean_reversion`, and `volatility_breakout`.
    - ATR bracket stop-loss / take-profit calculator and fractional-Kelly position sizer (`src/mcp_servers/quant_strategy_engine/sizing.py`).
    - Multi-factor alpha evaluator (`src/mcp_servers/quant_strategy_engine/evaluator.py`) combining Trend, Reversion, and Breakout factors with transparent conflict resolution.
    - FastMCP read-only server (`src/mcp_servers/quant_strategy_engine/server.py`) exposing 8 tools: `list_strategies`, `get_strategy_signal`, `get_target_portfolio`, `get_backtest_report`, `explain_strategy_signal`, `get_engine_health`, `evaluate_alpha_factors`, `propose_sized_order`.
    - Streaming Kafka worker (`src/mcp_servers/quant_strategy_engine/worker.py`) consuming `signals.technical` and closed `market.candles`, publishing to `signals.quant` with causation tracking and DLQ routing.
    - Cryptographic out-of-sample backtest report verification publisher gate (`src/mcp_servers/quant_strategy_engine/publisher.py`).
- **Schema 1.3.0 (`src/common/schemas/signals.py`)**:
  - `QuantSignal` enforces long-only cash delivery constraints: direction `SHORT` strictly prohibited, `FLAT` required for exits with `target_weight = Decimal("0")`.
  - `expected_net_edge_bps_per_trade` gated: strictly requires `EdgeBasis.OUT_OF_SAMPLE` and registered 64-hex SHA-256 backtest report hash.
- **Build & Quality Gates**:
  - Makefile and `make.ps1` targets: `quant`, `mcp-quant`, `backtest`, and `test-quant-coverage` ($\ge 90\%$).
  - Strict mypy compliance on all modules and 100% passing tests.

## [0.5.0-alpha] - 2026-10-13

### Added
- **Technical Analyst MCP Server (`src/mcp_servers/technical_analyst`)**:
  - Incremental, $O(1)$ streaming indicator implementations: SMA, EMA (recursive with warmup), RSI (Wilder), MACD (12, 26, 9), Bollinger Bands (20, 2 std), ATR (14, Wilder), Rolling VWAP (session reset), ADX (14 with +DI/-DI), and Stochastic (14, 3, 3).
  - Explicit warmup handling: indicators report `is_ready=False` and return `None` (never NaN or zero).
  - Verified no-lookahead invariants: tested via truncated history series.
  - Golden tests against hand-calculated vectors and cross-checked against `ta==0.11.0`.
  - Property-based tests via Hypothesis validating mathematical identities and bounds.
  - Deterministic rule engine (`SignalEngine`) driven by versioned rules (`config/technical_rules.json`).
  - Transparent confidence score bounded in $[0, 1]$ defined as rule-agreement fraction.
  - ADR-006 Data Provenance Merge Rule: least trustworthy mode wins (`SIMULATED` > `REPLAY` > `LIVE`), logical OR for `is_delayed`, max `delay_seconds`, and `"mixed"` provider attribution.
  - Streaming service (`TechnicalAnalystService`): Redpanda consumer on `market.candles` (group `technical-analyst-worker`), filters `is_final=True`, deduplicates, caches state in Redis, and publishes validated `TechnicalSignal` envelopes to `signals.technical`.
  - FastMCP server (`server.py`): exposes 5 read-only tools over STDIO (`get_indicators`, `get_signal`, `explain_signal`, `list_supported_indicators`, `get_analyzer_health`).
- **Schema 1.2.0 (`src/common/schemas`)**:
  - `Candle.is_final: bool | None = None`: non-null for schema $\ge 1.2.0$, enforcing $is\_final=\text{True} \implies close\_time \le envelope.produced\_at$.
  - `TechnicalSignal`: required `interval`, `regime`, `config_version="1.0.0"`, `rules_fired`, and validated `provenance`.
  - Regenerated 24 JSON Schemas and updated `docs/contracts.md`.
- **Architectural Documentation**:
  - `docs/adr/006-incremental-indicators-and-explainable-signals.md`.
  - `docs/mcp-servers/technical-analyst.md`.
  - Added Makefile and `make.ps1` targets: `analyst`, `mcp-analyst`, and `test-analyst-coverage` ($\ge 90\%$).

## [0.4.0-alpha] - 2026-10-13

### Added
- **Market Data Streamer MCP Server (`src/mcp_servers/market_data_streamer`)**:
  - Multi-provider abstraction (`MarketDataProvider`) supporting three operational regimes: `SIMULATED` (deterministic seeded walk with NSE price bands and tick size), `REPLAY` (historical sample playback at configurable speed), and `LIVE` (Yahoo Finance delayed REST feed with transparent disclosure).
  - Versioned NIFTY 50 constituent universe (`config/nifty50_universe.json`) and configurable NSE holiday calendar (`config/nse_holidays_2026.json`).
  - Historical tick recording dataset (`data/historical/nifty50_sample_ticks.jsonl`) for reproducible demos and audits.
  - Asynchronous normalization pipeline (`TickNormalizer`): validates positive prices, sequence monotonicity, bid <= ask, and routes malformed payloads to `market.ticks.dlq`.
  - Candle aggregation engine (`CandleAggregator`): aggregates 1m and 5m bars aligned to IST boundaries, strictly preserving OHLCV and VWAP invariants across minute bucket transitions.
  - Real-time exchange calendar evaluator (`MarketCalendar`): evaluates trading sessions (`PRE_OPEN`, `OPEN`, `POST_CLOSE`, `CLOSED`) with holiday detection in `Asia/Kolkata`.
  - Redis cache store (`MarketDataCache`): caches latest quotes, OHLCV candle sorted sets, and streamer health heartbeats.
  - Event publisher (`StreamerPublisher`): publishes validated ticks to `market.ticks`, candles to `market.candles`, and operational heartbeats/stalls to `system.events`.
  - Background streaming worker (`MarketDataStreamerService`): manages lifecycle, signal handling, and graceful shutdown flushing.
  - Official MCP Server (`server.py`): exposes 5 read-only tools over `stdio` transport (`get_latest_quote`, `get_candles`, `get_market_status`, `list_instruments`, `get_feed_health`).
- **Data Provenance & Schema Version 1.1.0 (`src/common/schemas/market.py`)**:
  - `DataProvenance` schema: tracks provider name, `FeedMode`, `is_delayed`, `delay_seconds`, and `ingested_at`.
  - Additive, backward-compatible field on `Tick` and `Candle` models with strict contract enforcement for schema version >= 1.1.0.
  - Regenerated 24 JSON Schemas and updated `docs/contracts.md`.
- **Architectural Documentation & Tests**:
  - `docs/adr/005-market-data-provider-abstraction-and-provenance.md`: evaluation of data sources, rate limits, licensing, and provenance semantics.
  - `docs/mcp-servers/market-data-streamer.md`: architectural Mermaid diagram, tool catalogue with sample payloads, and operational runbook.
  - Unit tests (`tests/unit/test_market_data_streamer.py`) with Hypothesis property verification.
  - End-to-end integration test (`tests/integration/test_market_data_streamer.py`) verifying ingestion across Redpanda, Redis, and MCP tool queries.
  - Added `stream` and `mcp-market` runner targets in `Makefile` and `make.ps1`.

## [0.3.0-alpha] - 2026-10-13

### Added
- Production-grade local infrastructure compose specification (`deploy/docker/docker-compose.yml`):
  - Services: PostgreSQL 16 (`postgres:16.8-alpine`), Redis 7 (`redis:7.4.2-alpine` with AOF), Redpanda 24.x (`docker.redpanda.com/redpandadata/redpanda:v24.3.4`), one-shot `redpanda-init`, and `redpanda-console` (`v2.8.2` under `tools` profile).
  - Pinned non-latest image tags, dedicated bridge network (`alphatrader_net`), ports bound strictly to `127.0.0.1`, bounded laptop-friendly resource limits, and real healthchecks.
- Relational schema of record (`deploy/docker/init.sql`):
  - 6 initial tables: `orders`, `fills`, `positions`, `portfolio_snapshots`, `risk_audit_log`, `schema_migrations`.
  - Exact `NUMERIC(20, 6)` arithmetic, `TIMESTAMPTZ`, UUID primary keys, and CHECK constraints matching domain schema enums.
  - `orders.idempotency_key` UNIQUE constraint preventing duplicate order executions.
  - Append-only PostgreSQL trigger (`trg_protect_risk_audit_log`) strictly raising exceptions on any `UPDATE` or `DELETE` attempt on `risk_audit_log`.
- Idempotent Kafka topic provisioning (`scripts/create_topics.py` and `deploy/docker/init_topics.sh`):
  - Reads `TOPIC_REGISTRY` from `src.common.contracts` as the single source of truth.
  - Provisions all 9 business topics and 8 Dead Letter Queue (`.dlq`) topics with exact partition counts and retention policies.
- Comprehensive end-to-end infrastructure smoke test (`scripts/smoke_test.py`):
  - Validates PostgreSQL table existence, baseline migration, idempotency constraint, and append-only trigger.
  - Validates Redis PING, SET/GET/TTL, and `alphatrader:floor_halted` circuit breaker flag.
  - Validates Redpanda by producing and consuming live `EventEnvelope` instances for all 9 topics and validating them against `validate_topic_message()`.
- Pytest integration test suite (`tests/integration/`):
  - `test_database_schema.py`: Verifies tables, migration baseline, idempotency unique constraint, check constraints, and audit-log immutability.
  - `test_redis_cache.py`: Verifies round-trip caching, circuit-breaker flags, and atomic rate-limiting counters.
  - `test_redpanda_contracts.py`: Verifies all 17 topics exist in cluster and round-trip event delivery against registered topic schemas.
  - Auto-skips cleanly when backing services are offline.
- Native cross-platform tooling:
  - Extended `Makefile` targets: `up`, `down`, `reset`, `ps`, `logs`, `psql`, `redis-cli`, `topics`, `smoke`, `test-integration`.
  - Created `make.ps1` providing native Windows PowerShell parity for all Makefile targets.
- GitHub Actions CI workflow integration (`.github/workflows/ci.yml`):
  - Added `infrastructure-integration` job running Docker Compose, `make topics`, `make smoke`, and `make test-integration`.
- Architectural documentation:
  - `docs/adr/004-redpanda-event-bus-and-postgres-system-of-record.md` (ADR-004).
  - `docs/infrastructure.md` (topology diagram, service inventory, schema mapping, disaster recovery, troubleshooting).

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
