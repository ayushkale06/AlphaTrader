# Backtest Report: EMA_Crossover (RELIANCE.NS)

- **Generated At:** 2026-10-14 08:53:55 UTC
- **Target Asset:** RELIANCE.NS
- **Starting Capital:** â‚¹1,000,000.00
- **Position Sizing Rule:** Fixed-fractional (50.0% portfolio equity per trade)
- **Final Equity:** â‚¹863,660.40
- **Total Return:** -13.63%
- **CAGR:** -7.16% (95% CI: [-15.67%, 2.21%])
- **Sharpe Ratio:** -2.20 (95% CI: [-4.00, -0.62], rf=6.5%, N=252)
- **Sortino Ratio:** -2.58
- **Max Drawdown:** 13.63% (461 bars)
- **Calmar Ratio:** -0.53
- **Stationary Bootstrap Parameter:** Politis & Romano (1994), expected block length L=10 bars, 1000 resamples
- **Trial Counter:** runs_evaluated_count = 4

## Performance vs. Benchmark Baselines

- **Buy-and-Hold Baseline:** Return -13.37%, CAGR -7.02%, Sharpe -0.56
- **Seeded Random-Entry Baseline (N=200 seeds):** Mean Return -5.91%, Mean Sharpe -0.07

## Methodology & Disclaimers

> [!IMPORTANT]
> **Survivorship Bias Disclaimer:**
> Data Source: Yahoo Finance Chart API (NSE Equity End-of-Day). Adjusted Close selected (splits/dividends accounted). Survivorship bias: RELIANCE was a continuing member of NIFTY 50 during 2024-2026.

## Cost Model & Itemized Statutory Breakdown

- **Model:** Indian Equity Cash Segment (NSE Delivery)
- **Brokerage:** Min(0.03%, â‚¹20/order)
- **STT:** 0.1% on delivery buys and sells
- **Exchange Fee:** 0.00325% turnover
- **SEBI Fee:** â‚¹10 / crore (0.0001%)
- **Stamp Duty:** 0.015% on buy turnover
- **GST:** 18% on (brokerage + exchange + sebi charges)
- **Slippage Model:** 5 bps half-spread execution drag

### Statutory Friction Paid Across Simulation
- **Brokerage:** â‚¹480.00
- **STT (Securities Transaction Tax):** â‚¹10,990.95
- **Exchange Turnover Charges:** â‚¹357.19
- **SEBI Turnover Charges:** â‚¹10.98
- **Stamp Duty:** â‚¹833.18
- **GST (18% on fees):** â‚¹152.67
- **Execution Half-Spread Slippage:** â‚¹5,495.48
- **Total Friction Paid:** â‚¹18,320.45

## Trade Statistics

- **Total Completed Trades:** 12
- **Winning Trades:** 2
- **Losing Trades:** 10
- **Win Rate:** 16.7%
- **Profit Factor:** 0.30
- **Gross Profit:** â‚¹59,244.36
- **Gross Loss:** â‚¹195,583.96

## Trade Log (Sample of Completed Roundtrips)

| Entry | Exit | Side | Qty | Entry Px | Exit Px | Net P&L | Return |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2024-12-05 | 2024-12-12 | BUY | 380 | â‚¹1314.3500 | â‚¹1270.0000 | â‚¹-18487.01 | -3.70% |
| 2025-01-17 | 2025-01-29 | BUY | 371 | â‚¹1322.2500 | â‚¹1236.0000 | â‚¹-33580.71 | -6.85% |
| 2025-02-03 | 2025-02-04 | BUY | 378 | â‚¹1251.0000 | â‚¹1248.0500 | â‚¹-2687.52 | -0.57% |
| 2025-02-05 | 2025-02-13 | BUY | 368 | â‚¹1282.6500 | â‚¹1217.0500 | â‚¹-25675.01 | -5.44% |
| 2025-03-13 | 2025-04-08 | BUY | 364 | â‚¹1260.0500 | â‚¹1172.0000 | â‚¹-33529.10 | -7.31% |
| 2025-04-22 | 2025-07-22 | BUY | 340 | â‚¹1300.0000 | â‚¹1427.4000 | â‚¹41774.85 | 9.45% |
| 2025-09-17 | 2025-09-26 | BUY | 329 | â‚¹1407.0000 | â‚¹1372.0000 | â‚¹-13039.20 | -2.82% |
| 2025-10-20 | 2026-01-08 | BUY | 317 | â‚¹1440.0000 | â‚¹1500.0000 | â‚¹17469.51 | 3.83% |
| 2026-04-30 | 2026-05-18 | BUY | 330 | â‚¹1409.0000 | â‚¹1334.0000 | â‚¹-26260.52 | -5.65% |
| 2026-07-21 | 2026-07-23 | BUY | 342 | â‚¹1321.0000 | â‚¹1283.0000 | â‚¹-14482.02 | -3.21% |
| 2026-08-07 | 2026-08-28 | BUY | 337 | â‚¹1320.0000 | â‚¹1284.9000 | â‚¹-13294.10 | -2.99% |
| 2026-09-07 | 2026-09-09 | BUY | 331 | â‚¹1323.1000 | â‚¹1283.5000 | â‚¹-14548.77 | -3.32% |
