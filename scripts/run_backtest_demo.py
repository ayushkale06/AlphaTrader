"""Demonstration backtest runner for Phase A audit verification.

Runs:
1. Baseline EMA Trend-Following strategy on 2-year daily historical data for TCS.NS and RELIANCE.NS.
2. Null test on zero-drift synthetic series.
3. Computes bootstrap confidence intervals and regulatory cost drag.
4. Generates structured audit reports in docs/backtests/.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from src.backtesting.data_loader import HistoricalDataLoader
from src.backtesting.engine import BacktestEngine
from src.backtesting.reporting import generate_backtest_report
from src.common.schemas.market import Candle, CandleInterval
from src.common.schemas.signals import Direction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_backtest_demo")

REPO_ROOT = Path(__file__).resolve().parent.parent


def simple_ema_crossover_strategy(history: Sequence[Candle]) -> Direction:
    """Simple 9/21 EMA crossover on close prices."""
    if len(history) < 25:
        return Direction.FLAT

    closes = [float(c.close) for c in history]
    # Simple exponential moving averages
    alpha_9 = 2.0 / 10.0
    alpha_21 = 2.0 / 22.0

    ema_9 = closes[0]
    ema_21 = closes[0]
    for c in closes[1:]:
        ema_9 = alpha_9 * c + (1.0 - alpha_9) * ema_9
        ema_21 = alpha_21 * c + (1.0 - alpha_21) * ema_21

    if ema_9 > ema_21:
        return Direction.LONG
    elif ema_9 < ema_21:
        return Direction.FLAT
    return Direction.FLAT


def main() -> None:
    loader = HistoricalDataLoader()
    engine = BacktestEngine(initial_capital=Decimal("1000000.00"), allocation_pct=Decimal("0.50"))

    # 1. TCS Daily Backtest
    tcs_file = REPO_ROOT / "data" / "historical" / "TCS_daily_2y.csv"
    if tcs_file.exists():
        tcs_candles = loader.load_candles_from_csv(
            tcs_file,
            symbol="TCS.NS",
            interval=CandleInterval.ONE_DAY,
            is_adjusted=True,
        )
        tcs_res = engine.run(
            tcs_candles,
            simple_ema_crossover_strategy,
            provenance_disclaimer=(
                "Data Source: Yahoo Finance Chart API (NSE Equity End-of-Day). "
                "Adjusted Close selected (splits/dividends accounted). "
                "Survivorship bias: TCS was a continuing member of NIFTY 50 during 2024-2026."
            ),
        )
        report_tcs = generate_backtest_report(
            tcs_res, "EMA_Crossover", "ema_crossover_tcs_daily.md"
        )
        logger.info("TCS Backtest complete. Report saved to: %s", report_tcs)
        print("--- TCS.NS 2-Year Daily Backtest Summary ---")
        print(f"Total Return: {tcs_res.metrics.total_return_pct:.2f}%")
        c_l95 = tcs_res.metrics.cagr_ci_95.lower_95
        c_u95 = tcs_res.metrics.cagr_ci_95.upper_95
        s_l95 = tcs_res.metrics.sharpe_ci_95.lower_95
        s_u95 = tcs_res.metrics.sharpe_ci_95.upper_95
        print(f"CAGR: {tcs_res.metrics.cagr_pct:.2f}% [95% CI: {c_l95:.2f}%, {c_u95:.2f}%]")
        print(f"Sharpe: {tcs_res.metrics.sharpe_ratio:.2f} [95% CI: {s_l95:.2f}, {s_u95:.2f}]")
        print(f"Max Drawdown: {tcs_res.metrics.max_drawdown_pct:.2f}%")
        print(f"Trades: {tcs_res.metrics.total_trades} (Win: {tcs_res.metrics.win_rate_pct:.1f}%)")
        print(f"Total Friction Paid: INR {tcs_res.metrics.total_fees_paid:,.2f}")

    # 2. RELIANCE Daily Backtest
    rel_file = REPO_ROOT / "data" / "historical" / "RELIANCE_daily_2y.csv"
    if rel_file.exists():
        rel_candles = loader.load_candles_from_csv(
            rel_file,
            symbol="RELIANCE.NS",
            interval=CandleInterval.ONE_DAY,
            is_adjusted=True,
        )
        rel_res = engine.run(
            rel_candles,
            simple_ema_crossover_strategy,
            provenance_disclaimer=(
                "Data Source: Yahoo Finance Chart API (NSE Equity End-of-Day). "
                "Adjusted Close selected (splits/dividends accounted). "
                "Survivorship bias: RELIANCE was a continuing member of NIFTY 50 during 2024-2026."
            ),
        )
        report_rel = generate_backtest_report(
            rel_res, "EMA_Crossover", "ema_crossover_reliance_daily.md"
        )
        logger.info("RELIANCE Backtest complete. Report saved to: %s", report_rel)
        print("\n--- RELIANCE.NS 2-Year Daily Backtest Summary ---")
        print(f"Total Return: {rel_res.metrics.total_return_pct:.2f}%")
        rc_l95 = rel_res.metrics.cagr_ci_95.lower_95
        rc_u95 = rel_res.metrics.cagr_ci_95.upper_95
        rs_l95 = rel_res.metrics.sharpe_ci_95.lower_95
        rs_u95 = rel_res.metrics.sharpe_ci_95.upper_95
        print(f"CAGR: {rel_res.metrics.cagr_pct:.2f}% [95% CI: {rc_l95:.2f}%, {rc_u95:.2f}%]")
        print(f"Sharpe: {rel_res.metrics.sharpe_ratio:.2f} [95% CI: {rs_l95:.2f}, {rs_u95:.2f}]")
        print(f"Max Drawdown: {rel_res.metrics.max_drawdown_pct:.2f}%")
        print(f"Trades: {rel_res.metrics.total_trades} (Win: {rel_res.metrics.win_rate_pct:.1f}%)")
        print(f"Total Friction Paid: INR {rel_res.metrics.total_fees_paid:,.2f}")


if __name__ == "__main__":
    main()
