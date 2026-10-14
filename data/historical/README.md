# Historical Market Data Sample Repository

- **Ingestion Timestamp:** 2026-10-14 06:19:45 UTC
- **Data Source:** Yahoo Finance Chart API (NSE Equity End-of-Day and Intraday)
- **Constituents:** RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK
- **Frequencies Captured:**
  - Daily (2-year history, ~497 bars per symbol)
  - 5-Minute (5-day rolling history, ~330-375 bars per symbol)

## Methodological Disclaimers & Invariants

### 1. Survivorship Bias Disclosure
> **CRITICAL:** These historical datasets represent the top constituents of the NIFTY 50 as of the collection date (October 2026). Backtesting on current index constituents across past years introduces documented **survivorship bias** (delisted, bankrupt, or demoted companies are excluded). Results generated using these series cannot be cited as representative of live index performance.

### 2. Price Adjustment Treatment
- The CSV files contain both raw `Close` and dividend/split `Adj Close`.
- The backtesting framework strictly forbids mixing unadjusted and adjusted prices in the same simulation run.
- Daily backtests must specify whether adjusted or raw closing prices were selected in the provenance header.

### 3. File Inventory

| Symbol | Resolution | Filename | Row Count |
| :--- | :--- | :--- | :--- |
| RELIANCE.NS | daily_2y | `RELIANCE_daily_2y.csv` | 497 |
| RELIANCE.NS | 5m_5d | `RELIANCE_5m_5d.csv` | 326 |
| TCS.NS | daily_2y | `TCS_daily_2y.csv` | 497 |
| TCS.NS | 5m_5d | `TCS_5m_5d.csv` | 325 |
| INFY.NS | daily_2y | `INFY_daily_2y.csv` | 497 |
| INFY.NS | 5m_5d | `INFY_5m_5d.csv` | 325 |
| HDFCBANK.NS | daily_2y | `HDFCBANK_daily_2y.csv` | 497 |
| HDFCBANK.NS | 5m_5d | `HDFCBANK_5m_5d.csv` | 325 |
| ICICIBANK.NS | daily_2y | `ICICIBANK_daily_2y.csv` | 497 |
| ICICIBANK.NS | 5m_5d | `ICICIBANK_5m_5d.csv` | 324 |
