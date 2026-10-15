# AlphaTrader 24-Sprint Engineering Roadmap

> **Audit Cadence:** Every 5–6 days (24 Sprints / ~20 Weeks)  
> **Safety Boundary:** **PAPER TRADING ONLY** ([ADR-002](docs/adr/002-paper-trading-only.md))  
> **Current Active Sprint:** **Sprint 24 — Final Release & Verification (COMPLETED — v1.0.0 RELEASE READY)**

---

## Executive Summary Table

| Sprint | Cadence (Days) | Phase / Domain | Status |
|---|---|---|---|
| **Sprint 1** | Days 1–5 | Repository Foundation, Typed Config, JSON Logging & CI Gates | ✅ Completed |
| **Sprint 2** | Days 6–10 | Canonical Schemas & Event Contracts (`src/common/schemas`, `src/common/contracts.py`) | ✅ Completed |
| **Sprint 3** | Days 11–15 | Local Infrastructure Stack (PostgreSQL 16, Redis 7, Redpanda 24.x via Docker Compose) | ✅ Completed |
| **Sprint 4** | Days 16–20 | MCP Server 1: Market Data Streamer (NIFTY 50 Tick Ingestion, Redpanda, Redis & MCP Tools) | ✅ Completed |
| **Sprint 5** | Days 21–25 | MCP Server 2: Technical Analyst (Indicators & Regime Classification) | ✅ Completed |
| **Sprint 6** | Days 26–30 | MCP Server 3: Quant Strategy Engine (Factor Alpha & Fractional Kelly) | ✅ Completed |
| **Sprint 7** | Days 36–41 | MCP Server 4 (Part 1): Deterministic Pre-Trade Risk Guardrail (Non-LLM) | ✅ Completed |
| **Sprint 8** | Days 42–47 | MCP Server 4 (Part 2): Intraday Drawdown Monitor & Floor Circuit Breaker | ✅ Completed |
| **Sprint 9** | Days 48–53 | MCP Server 5: Paper Execution Broker (Simulated Book, Slippage & NSE Taxes) | ✅ Completed |
| **Sprint 10** | Days 54–59 | MCP Server 6: Portfolio Ledger (PostgreSQL Double-Entry & Redis State) | ✅ Completed |
| **Sprint 11** | Days 60–65 | Individual Autonomous Agents (Analyst, Quant, Risk, Execution MCP Clients) | ✅ Completed |
| **Sprint 12** | Days 66–71 | LangGraph Stateful Orchestrator & Conditional Fallback Routing | ✅ Completed |
| **Sprint 13** | Days 72–77 | End-to-End Tick-to-Paper-Fill Pipeline Integration | ✅ Completed |
| **Sprint 14** | Days 78–83 | Idempotency, Dead-Letter Queues (DLQ) & Crash Recovery | ✅ Completed |
| **Sprint 15** | Days 84–89 | Observability Part 1: OpenTelemetry Distributed Tracing & Span Propagation | ✅ Completed |
| **Sprint 16** | Days 90–95 | Observability Part 2: Prometheus Metrics & Grafana Audit Dashboards | ✅ Completed |
| **Sprint 17** | Days 96–101 | Containerization & Multi-Stage Production Dockerfiles | ✅ Completed |
| **Sprint 18** | Days 102–107 | Kubernetes & Helm Chart Deployment (`deploy/k8s`) | ✅ Completed |
| **Sprint 19** | Days 108–113 | Historical NIFTY 50 Backtesting & Walk-Forward Replay Engine | ✅ Completed |
| **Sprint 20** | Days 114–119 | Quantitative Performance Analytics (Sharpe, Sortino, Max Drawdown, Attribution) | ✅ Completed |
| **Sprint 21** | Days 120–125 | Chaos Engineering Part 1: Broker/Feed Latency & Network Partition Injection | ✅ Completed |
| **Sprint 22** | Days 126–131 | Chaos Engineering Part 2: Flash-Crash Circuit Breaker & Adversarial Agent Tests | ✅ Completed |
| **Sprint 23** | Days 132–137 | Security Hardening, Audit Trail Verification & Load Benchmarking | ✅ Completed |
| **Sprint 24** | Days 138–143 | Final System Documentation, Reproducible Demo & v1.0.0 Release | ✅ Completed |

---

## Detailed Sprint Specifications

### Sprint 1 (Days 1–5): Repository Foundation, Typed Config, JSON Logging & CI Gates ✅
- **Goal:** Establish a clean, reproducible, strictly typed Python 3.11+ repository with automated quality gates, code-enforced paper-trading locks, structured correlation-ID logging, and architectural documentation.
- **Deliverables:**
  - Package layout under `src/` (`orchestrator`, `agents/*`, `mcp_servers/*`, `common`, `observability`), `deploy/docker/`, `deploy/k8s/`, `docs/adr/`, `tests/unit/`, `tests/integration/`.
  - `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`.
  - `src/common/config.py` with `.env.example` and ADR-002 paper-trading validators.
  - `src/common/logging.py` with `ContextVar` `correlation_id` propagation and single-line JSON output.
  - `src/common/healthcheck.py` CLI entry point (`python -m src.common.healthcheck`).
  - Unit tests (`tests/unit/test_config.py`, `test_logging.py`, `test_healthcheck.py`) with $\ge 90\%$ coverage.
  - `README.md`, `ROADMAP.md`, `docs/architecture.md`, `ADR-001`, `ADR-002`, `DEV_LOG.md`, `CHANGELOG.md`.
- **Definition of Done:** From a clean clone, `make install`, `make check` (`ruff`, `mypy --strict src/common`, `pytest` with $\ge 90\%$ coverage), and `python -m src.common.healthcheck` all succeed with exit code `0`.

---

### Sprint 2 (Days 6–10): Canonical Schemas & Event Contracts ✅
- **Goal:** Define immutable Pydantic v2 domain models, generic `EventEnvelope[T]` wrappers, and the Kafka topic contract registry shared across all 6 MCP servers and 4 agents.
- **Deliverables:**
  - `src/common/schemas/base.py`: `AlphaTraderBaseModel` (`frozen=True`, `extra="forbid"`), `UtcDatetime` validator rejecting naive datetimes, exact `Decimal` financial types rejecting `float`, and `NseSymbol` validator.
  - `src/common/schemas/envelope.py`: Generic `EventEnvelope[PayloadT]` with `event_id`, `correlation_id`, `causation_id`, `schema_version`, `produced_at`, `source`, deterministic `to_json_bytes()` / `from_json_bytes()`, and `SchemaVersionMismatchError`.
  - Domain modules: `market.py` (`Tick`, `Candle`, `MarketStatus`), `signals.py` (`TechnicalSignal`, `QuantSignal`, `Direction`), `risk.py` (`RiskDecision`, `RiskInputSnapshot`, `HaltEvent`), `orders.py` (`OrderRequest`, `Order`, `OrderStatus`, `Fill`, `validate_order_status_transition`), `portfolio.py` (`Position`, `PortfolioSnapshot`), and `system.py` (`SystemEvent`).
  - `src/common/contracts.py`: Typed `TOPIC_REGISTRY` for all 9 topics (`market.ticks`, `market.candles`, `signals.technical`, `signals.quant`, `risk.decisions`, `orders.requests`, `orders.fills`, `portfolio.updates`, `system.events`) and `validate_topic_message()`.
  - `scripts/export_schemas.py`, `docs/contracts/*.schema.json` (23 exported schemas), `docs/contracts.md`, and `ADR-003`.
- **Definition of Done:** 100% `mypy --strict` compliance on `src/common` and `scripts`, $\ge 95\%$ coverage on `src/common` (achieved $98.88\%$), Hypothesis property-based tests passing, and `make schemas-check` enforced in CI.

---

### Sprint 3 (Days 11–15): Production-Grade Local Infrastructure ✅
- **Goal:** Provision deterministic local backing services via Docker Compose with healthchecks, persistent volumes, automated schema initialization, and every Kafka topic created from the contract registry.
- **Deliverables:**
  - `deploy/docker/docker-compose.yml`: PostgreSQL 16 (`postgres:16.8-alpine`), Redis 7 (`redis:7.4.2-alpine` with AOF), Redpanda 24.x (`v24.3.4`), `redpanda-init` one-shot topic creator, and `redpanda-console` (`v2.8.2` under `tools` profile). Bounded resource limits, dedicated bridge network (`alphatrader_net`), and `127.0.0.1` port bindings.
  - `deploy/docker/init.sql`: Idempotent schema initializing `orders`, `fills`, `positions`, `portfolio_snapshots`, `risk_audit_log`, and `schema_migrations`. Strict `NUMERIC(20, 6)` arithmetic, `TIMESTAMPTZ`, UUID primary keys, and append-only trigger blocking `UPDATE`/`DELETE` on `risk_audit_log`.
  - `scripts/create_topics.py` & `deploy/docker/init_topics.sh`: Idempotent topic provisioner reading `TOPIC_REGISTRY` from `src.common.contracts` to create all 9 topics and 8 DLQs with exact partition and retention rules.
  - `scripts/smoke_test.py`: End-to-end infrastructure validation for PostgreSQL constraints/triggers, Redis SET/GET/TTL and circuit-breaker flag, and Redpanda round-trip envelope publish/consume for all 9 topics.
  - `tests/integration/`: Pytest integration test suite with `@pytest.mark.integration` (`test_database_schema.py`, `test_redis_cache.py`, `test_redpanda_contracts.py`) auto-skipping when stack is down.
  - `make.ps1` & `Makefile` targets: `up`, `down`, `reset`, `ps`, `logs`, `psql`, `redis-cli`, `topics`, `smoke`, `test-integration`.
  - Documentation: `docs/adr/004-redpanda-event-bus-and-postgres-system-of-record.md` and `docs/infrastructure.md`.
- **Definition of Done:** `make up` brings all three services healthy, `make smoke` and `make test-integration` pass 100%, strict mypy clean, and CI includes an integration job.

---

### Sprint 4 (Days 16–20): MCP Server 1 — Market Data Streamer ✅
- **Goal:** Ingest NIFTY 50 market data, normalize into Sprint 2 contracts with provenance metadata, publish to Redpanda, aggregate 1m/5m candles, cache quotes in Redis, and expose 5 read-only MCP tools over stdio.
- **Deliverables:**
  - `src/mcp_servers/market_data_streamer/providers/`: Typed `MarketDataProvider` interface with `SimulatedProvider` (seeded walk), `ReplayProvider` (historical replay at configurable speed), and `LiveProvider` (delayed public REST API with 900s transparent disclosure).
  - Schema Version `1.1.0` additive change: `DataProvenance` on `Tick` and `Candle` models with delay constraints and dual-timestamp semantics (`Tick.timestamp` as market event time, `DataProvenance.ingested_at` as local receive time).
  - Pipeline modules: `TickNormalizer` (sequence monotonicity, bid <= ask, DLQ routing), `CandleAggregator` (OHLCV & VWAP invariant preservation across IST boundaries), `MarketCalendar` (IST hours, holidays, pre-open, open, post-close), `MarketDataCache` (Redis sorted sets and quote hashes), and `StreamerPublisher`.
  - MCP Server (`src/mcp_servers/market_data_streamer/server.py`): Exposes 5 read-only tools (`get_latest_quote`, `get_candles`, `get_market_status`, `list_instruments`, `get_feed_health`) via the standard MCP SDK.
  - Test suites: Unit tests (`tests/unit/test_market_data_streamer.py`) with Hypothesis property verification and end-to-end integration tests (`tests/integration/test_market_data_streamer.py`).
  - Documentation: `docs/adr/005-market-data-provider-abstraction-and-provenance.md` and `docs/mcp-servers/market-data-streamer.md`.
- **Definition of Done:** `make check` passing with $\ge 95\%$ coverage on `src/common` (98.59%), strict mypy clean, and all integration tests passing against live Redpanda and Redis.

---

### Sprint 5 (Days 24–29): MCP Server 2 — Technical Analyst
- **Goal:** Implement pure, side-effect-free technical indicator calculation and volatility regime classification exposed via MCP.
- **Deliverables:**
  - Vectorized indicator engine in `src/mcp_servers/technical_analyst/` computing EMA (9/21/50/200), Wilder's RSI (14), ATR (14), intraday VWAP, MACD, and Bollinger Bands.
  - Volatility & trend regime classifier (`TRENDING_UP`, `TRENDING_DOWN`, `MEAN_REVERTING`, `HIGH_VOL_CHOP`).
  - MCP tools: `compute_technical_snapshot`, `classify_market_regime`.
- **Definition of Done:** Indicator outputs match reference golden datasets to within $10^{-6}$ tolerance and pass strict type checking and unit tests.

---

### Sprint 6 (Days 30–35): MCP Server 3 — Quant Strategy Engine & Factor Alpha ✅
- **Goal:** Build the provider-agnostic backtesting harness (Phase A) and deterministic quantitative strategy engine & streaming worker with FastMCP server (Phase B).
- **Deliverables:**
  - **Phase A (Backtesting & Cost Realism):**
    - `src/backtesting/engine.py`: Event-driven backtester enforcing strict no-lookahead fill on $t+1$ Open.
    - `src/backtesting/costs.py`: Exact NSE statutory transaction cost model (STT, brokerage, exchange turnover, SEBI charges, stamp duty, GST, slippage) in exact `Decimal`.
    - `src/backtesting/data_loader.py`: Historical CSV loader with corporate action adjustment tracking and calendar validation.
    - `tests/unit/test_backtesting.py`: 5-bar hand-calculated exact accounting test, 200-seed Monte Carlo null tests on gross/net returns, known-edge injection, and $\ge 90\%$ test coverage.
  - **Phase B (Quant Strategy Engine & Factor Alpha):**
    - `config/quant_strategies.json`: Configuration for `ema_trend_following`, `rsi_bollinger_mean_reversion`, and `volatility_breakout`.
    - `src/mcp_servers/quant_strategy_engine/strategies.py`: Deterministic versioned strategy registry and evaluation logic.
    - `src/mcp_servers/quant_strategy_engine/sizing.py`: ATR bracket stop-loss / take-profit calculator and fractional-Kelly sizing engine.
    - `src/mcp_servers/quant_strategy_engine/evaluator.py`: Multi-factor alpha evaluation (Trend, Reversion, Breakout) with conflict resolution.
    - `src/mcp_servers/quant_strategy_engine/publisher.py`: Cryptographic out-of-sample backtest report verification and `signals.quant` publisher.
    - `src/mcp_servers/quant_strategy_engine/worker.py`: Streaming Kafka consumer correlating technical signals and closed candles with causation tracking and DLQ routing.
    - `src/mcp_servers/quant_strategy_engine/server.py`: FastMCP server exposing 8 tools (`list_strategies`, `get_strategy_signal`, `get_target_portfolio`, `get_backtest_report`, `explain_strategy_signal`, `get_engine_health`, `evaluate_alpha_factors`, `propose_sized_order`).
    - Quality gates: Strict mypy compliant across all modules, $\ge 90\%$ coverage on `src/mcp_servers/quant_strategy_engine` (achieved $90.58\%$).
- **Definition of Done:** Every candidate proposal adheres to Schema 1.3.0 cash delivery invariants (`SHORT` prohibited, `expected_net_edge_bps_per_trade` gated to verified out-of-sample reports), `.\make.ps1 check` and `make check` pass with zero failures.

---

### Sprint 7 (Days 36–41): MCP Server 4 (Part 1) — Deterministic Pre-Trade Risk Guardrail
- **Goal:** Implement the non-LLM pre-trade risk gatekeeper that validates every candidate order before execution.
- **Deliverables:**
  - Pure-Python deterministic rule evaluator in `src/mcp_servers/risk_guardrail/`:
    - Single-position concentration check ($\le 5.0\%$ equity).
    - Maximum open positions check ($\le 10$ symbols).
    - Order rate limiter ($\le 30$ orders/min).
    - Mandatory stop-loss distance and notional sanity checks.
  - HMAC-SHA256 deterministic `RiskApprovalToken` issuer binding `(correlation_id, symbol, side, qty, limit_price, expires_at)`.
- **Definition of Done:** 100% branch coverage on `src/mcp_servers/risk_guardrail`; zero LLM imports permitted by static lint checks.

---

### Sprint 8 (Days 42–47): MCP Server 4 (Part 2) — Intraday Drawdown Monitor & Floor Circuit Breaker
- **Goal:** Implement continuous intraday drawdown tracking and the global trading-floor kill switch.
- **Deliverables:**
  - High-water-mark (HWM) drawdown evaluator comparing real-time equity against `MAX_DAILY_DRAWDOWN_PCT` ($2.0\%$).
  - Atomic Redis circuit-breaker key (`alphatrader:floor_halted`) and `risk.halt` Redpanda publisher.
  - MCP tools: `validate_order_proposal`, `get_floor_risk_status`, `trigger_manual_floor_halt`.
- **Definition of Done:** Simulated $2.01\%$ intraday drawdown triggers floor halt in $< 5\text{ ms}$ and blocks 100% of subsequent order approval requests.

---

### Sprint 9 (Days 48–53): MCP Server 5 — Paper Execution Broker
- **Goal:** Build the simulated NSE paper execution engine with cryptographic risk-token verification and realistic Indian transaction cost modeling.
- **Deliverables:**
  - `src/mcp_servers/paper_execution_broker/` verifying `RiskApprovalToken` authenticity and expiration before matching.
  - Simulated spread-crossing and volatility-proportional slippage model.
  - Exact NSE statutory cost calculator (STT, NSE exchange turnover charges, SEBI regulatory fees, stamp duty, 18% GST).
  - Publishes `PaperOrderFill` events to `orders.filled.paper`.
- **Definition of Done:** Unapproved orders or expired tokens are rejected 100% of the time; statutory tax math verified against published NSE brokerage calculators.

---

### Sprint 10 (Days 54–59): MCP Server 6 — Portfolio Ledger
- **Goal:** Build the double-entry accounting ledger in PostgreSQL with real-time intraday state synchronization in Redis.
- **Deliverables:**
  - `src/mcp_servers/portfolio_ledger/` consuming `orders.filled.paper` with idempotent `event_id` deduplication.
  - ACID double-entry journal writer (Cash Account $\leftrightarrow$ Equity Position Account $\leftrightarrow$ Fee/Tax Expense Account).
  - Real-time mark-to-market unrealized PnL, realized PnL, and daily HWM updater in Redis.
  - MCP tools: `get_portfolio_summary`, `get_open_positions`, `get_audit_trail`.
- **Definition of Done:** Sum of debits equals sum of credits across 10,000 randomized paper fills; zero balance drift between PostgreSQL and Redis.

---

### Sprint 11 (Days 60–65): Individual Autonomous Agents (Analyst, Quant, Risk, Execution)
- **Goal:** Implement the four specialized agent wrappers that consume the MCP servers via structured tool contracts.
- **Deliverables:**
  - `src/agents/analyst/`: invokes Technical Analyst MCP tools and synthesizes structured regime assessments.
  - `src/agents/quant/`: invokes Quant Strategy Engine MCP tools to construct candidate trades.
  - `src/agents/risk/`: submits proposals to the Risk Guardrail MCP server and inspects rejection reasons.
  - `src/agents/execution/`: dispatches risk-approved orders to the Paper Execution Broker MCP server.
- **Definition of Done:** Deterministic mock-LLM and live-MCP contract tests pass for all four agents with full `correlation_id` propagation.

---

### Sprint 12 (Days 66–71): LangGraph Stateful Orchestrator & Conditional Routing
- **Goal:** Wire the four agents into a cyclic/directed LangGraph state machine with explicit halt and rejection edges.
- **Deliverables:**
  - `src/orchestrator/graph.py`: Typed `TradingFloorState` graph (`Analyst -> Quant -> Risk -> Execution | Rejected | Halted`).
  - Pre-flight check consulting Redis `alphatrader:floor_halted` before every graph invocation.
  - Bounded retry and timeout policies per node.
- **Definition of Done:** Graph state transitions are deterministic, serializable to Redis checkpoints, and verified across normal, rejected-trade, and floor-halt scenarios.

---

### Sprint 13 (Days 72–77): End-to-End Tick-to-Paper-Fill Pipeline Integration
- **Goal:** Connect the full pipeline from `market_data_streamer` through Redpanda, the LangGraph orchestrator, all 6 MCP servers, to PostgreSQL ledger settlement.
- **Deliverables:**
  - End-to-end integration test suite (`tests/integration/test_tick_to_fill_e2e.py`).
  - CLI runner to replay a full day of NIFTY 50 ticks through the live local stack.
- **Definition of Done:** A single market tick (`correlation_id`) can be queried across Redpanda topics, MCP logs, and PostgreSQL `journal_entries` with 100% audit continuity.

---

### Sprint 14 (Days 78–83): Idempotency, Dead-Letter Queues (DLQ) & Crash Recovery
- **Goal:** Harden event consumers and state transitions against duplicate delivery, poison messages, and process restarts.
- **Deliverables:**
  - Consumer offset management and idempotency keys on all Redpanda consumers.
  - Dead-Letter Queue (`*.dlq`) routing for malformed payloads.
  - State reconciliation startup routine rebuilding Redis cache from PostgreSQL ledger on boot.
- **Definition of Done:** Killing and restarting the `portfolio_ledger` or `orchestrator` mid-stream results in zero duplicate fills and zero lost accounting entries.

---

### Sprint 15 (Days 84–89): Observability Part 1 — OpenTelemetry Distributed Tracing
- **Goal:** Instrument all agents, MCP servers, and Redpanda producers/consumers with OpenTelemetry spans.
- **Deliverables:**
  - `src/observability/tracing.py`: OTLP span exporter bridged with `correlation_id` context variables.
  - Automatic span creation for every MCP tool call, LangGraph node transition, and SQL transaction.
- **Definition of Done:** Full distributed trace waterfall visible from tick ingestion to ledger commit with sub-millisecond span timing.

---

### Sprint 16 (Days 90–95): Observability Part 2 — Prometheus Metrics & Grafana Dashboards
- **Goal:** Expose real-time operational and financial metrics to Prometheus and provision automated Grafana dashboards.
- **Deliverables:**
  - `src/observability/metrics.py`: Counters, Gauges, and Histograms (`alphatrader_equity_inr`, `alphatrader_intraday_drawdown_pct`, `alphatrader_orders_total`, `alphatrader_risk_halt_state`, `alphatrader_tick_to_fill_latency_seconds`).
  - Provisioned Grafana dashboard JSON manifests in `deploy/docker/grafana/`.
- **Definition of Done:** Grafana renders live paper PnL, drawdown gauge, risk rejection breakdown, and p95 latency histograms out of the box on `docker compose up`.

---

### Sprint 17 (Days 96–101): Containerization & Multi-Stage Production Dockerfiles
- **Goal:** Package all AlphaTrader microservices into minimal, non-root, security-scanned Docker images.
- **Deliverables:**
  - Multi-stage `Dockerfile` in `deploy/docker/` using `python:3.11-slim`, non-root `alphatrader` UID, and locked dependencies.
  - Full-stack `docker-compose.full.yml` running infrastructure + all 6 MCP servers + orchestrator.
- **Definition of Done:** All container images build reproducibly, run as non-root, and pass `python -m src.common.healthcheck` as their Docker `HEALTHCHECK`.

---

### Sprint 18 (Days 102–107): Kubernetes & Helm Chart Deployment
- **Goal:** Complete the Kubernetes Helm chart in `deploy/k8s/` for cloud-native deployment on Minikube/Kind/EKS/GKE.
- **Deliverables:**
  - Helm templates for Deployments, Services, ConfigMaps, Secrets, PodDisruptionBudgets, and NetworkPolicies.
  - Liveness and readiness probes wired to healthcheck endpoints.
- **Definition of Done:** `helm lint deploy/k8s` and `helm install alphatrader deploy/k8s --dry-run=client` succeed with zero warnings, and workloads deploy cleanly to a local Kind/Minikube cluster.

---

### Sprint 19 (Days 108–113): Historical NIFTY 50 Backtesting & Replay Engine
- **Goal:** Enable deterministic high-speed historical replay of multi-month NIFTY 50 datasets through the exact same MCP and risk guardrail code paths.
- **Deliverables:**
  - Simulated clock and deterministic replay controller in `scripts/run_backtest.py`.
  - Curated historical NIFTY 50 sample fixture dataset for reproducible professor audits.
- **Definition of Done:** Running the same historical seed twice produces bit-for-bit identical paper fills, ledger balances, and risk decisions.

---

### Sprint 20 (Days 114–119): Quantitative Performance Analytics & Attribution
- **Goal:** Generate institutional-grade quantitative tear sheets from PostgreSQL paper ledger history.
- **Deliverables:**
  - Analytics module computing annualized Sharpe ratio, Sortino ratio, Calmar ratio, maximum drawdown duration, win rate, profit factor, and NSE transaction cost drag.
  - Automated Markdown/HTML tear-sheet generator.
- **Definition of Done:** Verified tear-sheet output generated automatically at the end of every backtest run and unit-tested against known return series.

---

### Sprint 21 (Days 120–125): Chaos Engineering Part 1 — Infrastructure & Latency Faults
- **Goal:** Prove system resilience under Redis restarts, Redpanda broker stalls, and high network latency.
- **Deliverables:**
  - Chaos test suite in `tests/integration/chaos/` injecting artificial latency and container pauses.
  - Fail-closed verification ensuring that if Redis or the Portfolio Ledger is unreachable, `risk_guardrail` rejects all new orders rather than failing open.
- **Definition of Done:** 100% fail-closed behavior verified when backing state stores are degraded or unreachable.

---

### Sprint 22 (Days 126–131): Chaos Engineering Part 2 — Flash-Crash & Adversarial Agent Tests
- **Goal:** Stress-test the deterministic risk guardrail against synthetic NIFTY 50 flash crashes and adversarial/hallucinating agent outputs.
- **Deliverables:**
  - Synthetic $-5\%$ NIFTY 50 flash-crash tick scenario and rogue-agent fuzzing suite (attempting oversized orders, negative quantities, or missing stop-losses).
- **Definition of Done:** Risk guardrail halts the trading floor at the $2.0\%$ drawdown boundary and blocks 100% of malformed/oversized rogue agent proposals.

---

### Sprint 23 (Days 132–137): Security Hardening, Audit Trail Verification & Load Benchmarking
- **Goal:** Conduct comprehensive static security auditing, dependency vulnerability scanning, and throughput benchmarking.
- **Deliverables:**
  - Cryptographic hash-chain verification script for the PostgreSQL `risk_audit_log` table.
  - Least-privilege PostgreSQL role configuration granting the runtime application role strictly `INSERT` and `SELECT` on `risk_audit_log` (prohibiting DDL, `UPDATE`, `DELETE`, and `TRUNCATE` at the database role level in defense-in-depth with triggers; see ADR-004).
  - Latency & throughput benchmark report documenting p50/p95/p99 tick-to-decision latencies.
- **Definition of Done:** Zero high/critical findings in static analysis, intact audit hash chain, application role privilege boundary verified, and documented benchmark results.

---

### Sprint 24 (Days 138–143): Final System Documentation, Reproducible Demo & v1.0.0 Release
- **Goal:** Deliver a turnkey, single-command reproducible demonstration and complete architectural dossier for final evaluation.
- **Deliverables:**
  - End-to-end walkthrough guide, recorded terminal/Grafana benchmark artifacts, and final `v1.0.0` release tag.
- **Definition of Done:** Fresh clone to full multi-agent paper-trading demonstration completes via a single documented workflow with all unit, integration, and chaos tests passing.
