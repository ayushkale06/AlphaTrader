# Backtest Report: EMA_Crossover (TCS.NS)

- **Generated At:** 2026-10-14 08:53:55 UTC
- **Target Asset:** TCS.NS
- **Starting Capital:** â‚¹1,000,000.00
- **Position Sizing Rule:** Fixed-fractional (50.0% portfolio equity per trade)
- **Final Equity:** â‚¹890,473.78
- **Total Return:** -10.95%
- **CAGR:** -5.71% (95% CI: [-13.92%, 3.95%])
- **Sharpe Ratio:** -1.06 (95% CI: [-1.92, -0.17], rf=6.5%, N=252)
- **Sortino Ratio:** -1.32
- **Max Drawdown:** 12.33% (469 bars)
- **Calmar Ratio:** -0.46
- **Stationary Bootstrap Parameter:** Politis & Romano (1994), expected block length L=10 bars, 1000 resamples
- **Trial Counter:** runs_evaluated_count = 3

## Performance vs. Benchmark Baselines

- **Buy-and-Hold Baseline:** Return -39.62%, CAGR -22.57%, Sharpe -1.13
- **Seeded Random-Entry Baseline (N=200 seeds):** Mean Return -27.94%, Mean Sharpe -0.31

## Methodology & Disclaimers

> [!IMPORTANT]
> **Survivorship Bias Disclaimer:**
> Data Source: Yahoo Finance Chart API (NSE Equity End-of-Day). Adjusted Close selected (splits/dividends accounted). Survivorship bias: TCS was a continuing member of NIFTY 50 during 2024-2026.

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
- **Brokerage:** â‚¹600.00
- **STT (Securities Transaction Tax):** â‚¹14,174.82
- **Exchange Turnover Charges:** â‚¹460.68
- **SEBI Turnover Charges:** â‚¹14.16
- **Stamp Duty:** â‚¹1,069.55
- **GST (18% on fees):** â‚¹193.48
- **Execution Half-Spread Slippage:** â‚¹7,087.40
- **Total Friction Paid:** â‚¹23,600.09

## Trade Statistics

- **Total Completed Trades:** 15
- **Winning Trades:** 5
- **Losing Trades:** 10
- **Win Rate:** 33.3%
- **Profit Factor:** 0.31
- **Gross Profit:** â‚¹50,145.59
- **Gross Loss:** â‚¹159,671.82

## Trade Log (Sample of Completed Roundtrips)

| Entry | Exit | Side | Qty | Entry Px | Exit Px | Net P&L | Return |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2024-11-19 | 2024-11-22 | BUY | 124 | â‚¹4019.8999 | â‚¹4071.0000 | â‚¹4669.88 | 0.94% |
| 2024-11-25 | 2024-12-24 | BUY | 117 | â‚¹4280.0000 | â‚¹4158.2998 | â‚¹-15881.17 | -3.17% |
| 2025-01-27 | 2025-01-28 | BUY | 120 | â‚¹4102.0000 | â‚¹4070.8000 | â‚¹-5374.93 | -1.09% |
| 2025-05-07 | 2025-06-03 | BUY | 143 | â‚¹3420.6001 | â‚¹3446.0000 | â‚¹1999.91 | 0.41% |
| 2025-06-17 | 2025-06-24 | BUY | 140 | â‚¹3510.0000 | â‚¹3434.6001 | â‚¹-12173.69 | -2.48% |
| 2025-09-03 | 2025-09-08 | BUY | 155 | â‚¹3129.8999 | â‚¹3050.0000 | â‚¹-13979.15 | -2.88% |
| 2025-09-15 | 2025-09-25 | BUY | 153 | â‚¹3134.0000 | â‚¹3022.0000 | â‚¹-18705.16 | -3.90% |
| 2025-10-24 | 2025-11-10 | BUY | 152 | â‚¹3080.0000 | â‚¹2993.0000 | â‚¹-14762.56 | -3.15% |
| 2025-11-11 | 2026-01-16 | BUY | 152 | â‚¹3045.0000 | â‚¹3219.3999 | â‚¹24926.24 | 5.39% |
| 2026-01-19 | 2026-01-20 | BUY | 149 | â‚¹3188.0000 | â‚¹3151.6001 | â‚¹-6996.29 | -1.47% |
| 2026-02-04 | 2026-02-05 | BUY | 151 | â‚¹3120.0000 | â‚¹2990.0000 | â‚¹-21168.25 | -4.49% |
| 2026-04-13 | 2026-04-15 | BUY | 185 | â‚¹2489.8999 | â‚¹2501.8999 | â‚¹681.98 | 0.15% |
| 2026-04-16 | 2026-04-27 | BUY | 179 | â‚¹2572.0000 | â‚¹2396.8999 | â‚¹-32828.49 | -7.13% |
| 2026-07-16 | 2026-08-24 | BUY | 201 | â‚¹2206.0000 | â‚¹2302.3999 | â‚¹17867.58 | 4.03% |
| 2026-09-01 | 2026-09-08 | BUY | 192 | â‚¹2355.0000 | â‚¹2270.0000 | â‚¹-17802.13 | -3.94% |
