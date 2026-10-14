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

### 3.2 Signal and Target Proposals
Strategies output `QuantSignal` proposals carrying:
- `strategy_name`: String identifier.
- `direction`: `LONG`, `SHORT`, or `FLAT`.
- `target_weight`: Unit interval $[0, 1]$ representing desired portfolio allocation fraction.
- `expected_edge`: Projected edge in basis points.
- `confidence`: Bounded $[0, 1]$.

Downstream risk guardrails decide whether to approve, scale back, or reject the proposed target.

### 3.3 State Fingerprint and Restore Validation
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
  - Guarantees seamless transition from backtester to live paper execution via identical contract schemas.
- **Negative:**
  - Requires maintaining separate historical datasets under `data/historical/` with documented adjustments and gap records.
