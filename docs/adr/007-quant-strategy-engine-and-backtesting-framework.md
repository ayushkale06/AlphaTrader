# ADR-007: Quant Strategy Engine and Backtesting Framework

- **Status:** Accepted
- **Date:** 2026-10-14 (Updated 2026-10-15)
- **Deciders:** AlphaTrader Architecture Team, Senior Engineering, Quantitative Lead
- **Technical Context:** Sprint 6 (Days 26–30) — Quant Strategy Engine and Backtesting Framework

---

## 1. Context and Problem Statement

Following the completion of the Technical Analyst MCP Server (Sprint 5) which produces incremental indicator snapshots and rule-based `TechnicalSignal` events, AlphaTrader requires a quantitative strategy generation capability (`QuantSignal`).

To prevent curve-fitting, lookahead bias, and unrealistic paper simulations, we must establish:
1. An honest, provider-agnostic backtesting harness before constructing the live strategy engine.
2. Clear boundaries regarding the use of simulated vs. historical data.
3. Explicit separation of duties: Strategies PROPOSE target allocations; downstream Risk Guardrails (Sprint 7) DISPOSE. No broker execution occurs within this engine.
4. A dedicated background streaming worker consuming `signals.technical` and completed `market.candles` with durable fanout manifests, canonical artifact identity, deterministic signal IDs, and token-fenced state transitions.

---

## 2. Decision Drivers

- **Zero Lookahead Bias:** Any signal calculated at bar $t$ close can only be filled at bar $t+1$ open.
- **Microstructure Realism:** Complete fee modeling for Indian equities (STT, exchange turnover fees, GST, SEBI charges, stamp duty, slippage) using exact `Decimal` arithmetic.
- **Audit Compliance:** Explicit demarcation that synthetic/simulated data is strictly for system plumbing, pipeline tests, and null-hypothesis checks.
- **Statistical Rigor:** Mandatory reporting of bootstrap confidence intervals and out-of-sample performance metrics.
- **Crash Consistency and Idempotency:** Input Kafka offsets committed ONLY after all fan-out outputs reach verified terminal dispositions or whole-input suppression.

---

## 3. Key Architectural Decisions

### 3.1 Simulated Data Boundary Mandate
> **CRITICAL AUDIT INVARIANT**:
> Simulated data (such as data produced by `SimulatedProvider` via seeded random walks) supports **plumbing, pipeline verification, and statistical null tests ONLY**.
> Synthetic data lacks fat tails, volatility clustering (heteroskedasticity), auto-regressive volume profiles, intraday seasonality, liquidity blackouts, and real macroeconomic co-jumps.
> **Simulated data MUST NEVER be used as evidence of strategy profitability, alpha, or positive expectancy.**

### 3.2 Signal and Target Proposals (Reconciled with Schema 1.3.0)
Strategies output `QuantSignal` proposals adhering strictly to:
- `strategy_name`: Non-empty string identifier.
- `strategy_version`: Required semantic version of the strategy logic (e.g. '1.0.0').
- `direction`: `Direction.LONG` or `Direction.FLAT`. (`Direction.SHORT` is strictly forbidden under cash delivery rules; exits must be expressed as `FLAT`).
- `target_weight`: Unit interval $[0, 1]$ representing desired portfolio allocation fraction (`target_weight > 0` for `LONG`, `target_weight == 0` for `FLAT`).
- `expected_net_edge_bps_per_trade`: Projected net edge in basis points. Must remain `None` unless backed by a registered out-of-sample report in `docs/backtests/index.json`.
- `edge_basis`: Scientific validation basis (`OUT_OF_SAMPLE` required when edge is claimed).
- `backtest_report_id`: 64-character SHA-256 hash of the committed report file.
- `source_signal_ids`: Causation lineage linking upstream `TechnicalSignal` event UUIDs.
- `confidence`: Omitted in Schema 1.3.0 per audit feedback until rigorous Bayesian calibration is established.

Downstream pre-trade risk guardrails (Sprint 7) decide whether to approve, scale back, or reject the proposed target. No broker execution or order dispatch occurs within this engine.

### 3.3 Shipped Phase B Deterministic Strategies
1. `ema_trend_following` (v1.0.0): Dual-moving average trend momentum with long-term trend filter (EMA 9 > EMA 21 confirmed by Close > EMA 50).
2. `rsi_bollinger_mean_reversion` (v1.0.0): Bollinger Band lower-band penetration (%B <= 0.15) with oversold RSI (<= 35.0) targeting mean reversion.
3. `volatility_breakout` (v1.0.0): Volatility expansion entry (%B >= 0.90, Bandwidth >= 0.04, ADX >= 25.0) for momentum breakouts.

### 3.4 Streaming Consumer Loop, Manifest Fencing, and Identity
1. **Time-Addressable Anchor Resolution:** Completed candles (`is_final=True`) are persisted in Redis with 72-hour TTL at `quant:candle:{symbol}:{interval}:{close_time}`. `TechnicalSignal.evaluated_at` maps directly to candle close time without floor-quantization heuristics.
2. **Deterministic Canonical Identity:** Signal IDs are derived via UUIDv5 with length-prefixed UTF-8 serialization under dedicated namespace `e7b8a1c0-9d4f-4e8b-b12a-5c9e2b4f6a10`. Strategy artifact hashes cover normalized source code, project-owned dependencies, version, and config hash.
3. **Atomic Lua Manifest State Transitions:** Fanout manifests (`quant:fanout:{event_id}`) track every strategy from `PENDING` to `CONFIRMED_PUBLISHED`, `REPLAY_DEDUPLICATED`, or `INTENTIONALLY_SUPPRESSED` under distributed token fencing (`quant:claim:{event_id}:{strategy}`).
4. **Publish-Before-Commit & Rebalance Fencing:** Kafka source offsets are committed only after all strategy outputs in the manifest reach a verified terminal state and matching dedup markers exist. Partition revocations fence subsequent commits.

---

## 4. Consequences and Distributed Limitations

- **Positive:**
  - Prevents subtle data-snooping and false profit claims during academic audits.
  - Enforces long-only Indian equity cash delivery constraints at the schema and strategy layer.
  - Cryptographically verifies any claimed statistical edge before publication to `signals.quant`.
  - Token fencing prevents split-brain workers from corrupting manifest states.
- **Distributed Boundaries & Operational Limitations:**
  - **At-Least-Once Delivery Guarantee:** Cross-system operations between Kafka and Redis cannot form a single atomic transaction without 2PC. If a worker's Redis lease expires while a Kafka produce is in flight at the broker, the produce may succeed while Redis fencing rejects the state update. Downstream consumers (Risk Guardrail, Order Broker) must enforce idempotency via the deterministic UUIDv5 signal ID.
  - **Redis Data Loss / Disaster Recovery Ambiguity:** If Redis is wiped or loses keys within the 72-hour window, replay cannot distinguish first-seen processing from lost state and falls back to duplicate publication.
  - **Docker Infrastructure Dependency:** Integration tests against live Redpanda and Redis require a running Docker daemon (`com.docker.service`). When Docker is stopped, integration tests cleanly skip while in-memory unit tests provide 100% deterministic state verification.
  - Prohibits shorting in cash equity segments, requiring explicit flat exits.
