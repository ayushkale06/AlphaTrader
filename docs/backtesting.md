# AlphaTrader Backtesting Framework Architecture & Methodology

> **AUDIT MANDATE (PROFESSOR & SYSTEM AUDITORS)**:
> Simulated data (such as data produced by `SimulatedProvider` via seeded random walks or geometric Brownian motion) supports **plumbing, pipeline verification, and statistical null tests ONLY**. Synthetic data lacks fat tails, volatility clustering (heteroskedasticity), auto-regressive volume profiles, intraday seasonality, liquidity blackouts, and real macroeconomic co-jumps. **Simulated data MUST NEVER be used as evidence of strategy profitability, alpha, or positive expectancy.**

---

## 1. Backtesting Philosophy: Honest Accounting First

AlphaTrader's backtester is designed around an unwavering principle: **Strategies propose; risk and execution dispose**. The backtesting engine enforces strict realism, eliminating the classical sources of backtest over-optimism:
1. **Zero Lookahead Guarantee**:
   - Indicator updates and signal evaluations occur strictly using bar $t$ closing information (`is_final=True`).
   - Order generation occurs after bar $t$ evaluation.
   - Order execution fills at the **Open price of bar $t+1$** (or subsequent available execution tick). Bar $t$ close is NEVER used as the fill price for an order evaluated on bar $t$.
2. **Deterministic Indian Equity Cost Model**:
   - Every fill incurs brokerage, Securities Transaction Tax (STT), Exchange Turnover Charges, SEBI turnover fees, Stamp Duty, GST (18% on brokerage + turnover charges), and dynamic bid-ask half-spread slippage.
   - All monetary arithmetic is conducted using Python `Decimal` (no binary `float` rounding errors).
3. **Multi-Regime and Null-Hypothesis Testing**:
   - Every candidate strategy is subjected to a **Null Test** on a zero-drift geometric random walk. The backtester must confirm that gross returns are statistically indistinguishable from zero, and net returns are negative due to friction.
   - A **Known-Edge Test** verifies that an artificial synthetic series with an injected deterministic drift is captured properly by the engine.

---

## 2. Data Provenance & Source Hierarchy

As defined in ADR-005, historical datasets ingested by `HistoricalDataLoader` strictly preserve the `DataProvenance` contract:
- `LIVE-historical`: Bars queried directly from primary exchange feeds (or historical archives of live vendor API queries).
- `REPLAY`: Recorded tick feeds played back through the aggregator.
- `SIMULATED`: Deterministic synthetic walks (reserved for plumbing, test benchmarks, and null-hypothesis testing).

---

## 3. Statistical Verification & Uncertainty Quantification

Backtest summaries do not simply present a single Sharpe ratio. The engine computes:
- Out-of-sample performance across split intervals.
- Stationary bootstrap 95% confidence intervals for Sharpe, Sortino, and Maximum Drawdown.
- Number of trades and turnover metrics.
- Worst-case drawdown duration and recovery time.
