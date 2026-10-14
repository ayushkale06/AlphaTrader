# ADR-007: Quant Strategy Engine and Backtesting Framework

- **Status:** Proposed
- **Date:** 2026-10-14
- **Deciders:** AlphaTrader Architecture Team, Senior Engineering, Quantitative Lead
- **Technical Context:** Sprint 6 (Days 26–30) — Quant Strategy Engine and Backtesting Framework

---

## 1. Context and Problem Statement

Following the completion of the Technical Analyst MCP Server (Sprint 5) which produces incremental indicator snapshots and rule-based `TechnicalSignal` events, AlphaTrader requires a quantitative strategy generation capability (`QuantSignal`).

To prevent curve-fitting, lookahead bias, and unrealistic paper simulations, we must establish:
1. An honest, provider-agnostic backtesting harness before constructing the live strategy engine.
2. Clear boundaries regarding the use of simulated vs. historical data.
3. Explicit separation of duties: Strategies PROPOSE target allocations; downstream Risk Guardrails (Sprint 7) DISPOSE. No broker execution occurs within this engine.

---

## 2. Decision Drivers

- **Zero Lookahead Bias:** Any signal calculated at bar $t$ close can only be filled at bar $t+1$ open.
- **Microstructure Realism:** Complete fee modeling for Indian equities (STT, exchange turnover fees, GST, SEBI charges, stamp duty, slippage) using exact `Decimal` arithmetic.
- **Audit Compliance:** Explicit demarcation that synthetic/simulated data is strictly for system plumbing, pipeline tests, and null-hypothesis checks.
- **Statistical Rigor:** Mandatory reporting of bootstrap confidence intervals and out-of-sample performance metrics.

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

### 3.4 State Fingerprint and Restore Validation
Indicator states persisted across sessions must carry an explicit metadata envelope:
- `provider`
- `feed_mode`
- `config_version`
- `last_close_time`

On cold-start recovery, states are validated against incoming feeds. A gap exceeding 50 intervals or a change in provider/feed_mode forces a clean state re-warm. Legitimate overnight or weekend gaps with matching fingerprints preserve indicator state.

---

## 4. Consequences

- **Positive:**
  - Prevents subtle data-snooping and false profit claims during professor audits.
  - Enforces long-only Indian equity cash delivery constraints at the schema and strategy layer.
  - Cryptographically verifies any claimed statistical edge before publication to `signals.quant`.
  - Guarantees seamless transition from backtester to live paper execution via identical contract schemas.
- **Negative:**
  - Requires maintaining separate historical datasets under `data/historical/` with documented adjustments and gap records.
  - Prohibits shorting in cash equity segments, requiring explicit flat exits.
