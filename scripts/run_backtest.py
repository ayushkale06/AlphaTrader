"""Backtesting Replay Runner with simulated clock and tearsheet output.

Enforces:
1. Zero-Lookahead execution (ADR-002, ADR-010).
2. Exact Indian regulatory friction accounting (STT, GST, Exchange charges, Stamp Duty).
3. Production of institutional quantitative tear sheets and reports.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from src.backtesting.data_loader import HistoricalDataLoader
from src.backtesting.engine import BacktestEngine, BacktestResult
from src.backtesting.tearsheet import PerformanceTearSheet, generate_tear_sheet
from src.common.schemas.market import Candle, CandleInterval
from src.common.schemas.signals import Direction

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("alphatrader.backtesting.runner")

REPO_ROOT = Path(__file__).resolve().parent.parent


def trend_following_strategy(history: Sequence[Candle]) -> Direction:
    """Robust Dual-EMA trend-following strategy with minimum warmup period."""
    if len(history) < 30:
        return Direction.FLAT

    closes = [float(c.close) for c in history]
    alpha_fast = 2.0 / (12.0 + 1.0)
    alpha_slow = 2.0 / (26.0 + 1.0)

    ema_fast = closes[0]
    ema_slow = closes[0]
    for c in closes[1:]:
        ema_fast = alpha_fast * c + (1.0 - alpha_fast) * ema_fast
        ema_slow = alpha_slow * c + (1.0 - alpha_slow) * ema_slow

    if ema_fast > ema_slow * 1.002:
        return Direction.LONG
    elif ema_fast < ema_slow * 0.998:
        return Direction.FLAT
    return Direction.FLAT


def run_historical_replay(
    csv_path: Path,
    symbol: str,
    initial_capital: Decimal = Decimal("1000000.00"),
    allocation_pct: Decimal = Decimal("0.25"),
    output_tearsheet_path: Path | None = None,
) -> tuple[BacktestResult, PerformanceTearSheet]:
    """Execute historical replay backtest and generate institutional performance tear sheet."""
    logger.info("Loading historical data for %s from %s", symbol, csv_path)
    loader = HistoricalDataLoader()
    candles = loader.load_candles_from_csv(
        csv_path,
        symbol=symbol,
        interval=CandleInterval.ONE_DAY,
        is_adjusted=True,
    )
    if not candles:
        raise ValueError(f"No valid candles loaded from {csv_path}")

    logger.info("Loaded %d chronological bars. Running BacktestEngine...", len(candles))
    engine = BacktestEngine(
        initial_capital=initial_capital,
        allocation_pct=allocation_pct,
    )
    result = engine.run(
        candles=candles,
        strategy_fn=trend_following_strategy,
        provenance_disclaimer=f"Historical CSV Replay for {symbol} (Zero-Lookahead compliant)",
    )

    # Compute breakdown sums
    stt_paid = float(sum(t.stt_paid for t in result.trades))
    exchange_fees_paid = float(sum(t.exchange_fees_paid for t in result.trades))
    stamp_duty_paid = float(sum(t.stamp_duty_paid for t in result.trades))
    gst_paid = float(sum(t.gst_paid for t in result.trades))
    slippage_paid = float(sum(t.slippage_paid for t in result.trades))

    tearsheet = generate_tear_sheet(
        metrics=result.metrics,
        strategy_name="TrendFollowing-DualEMA",
        symbol=symbol,
        stt_paid=stt_paid,
        exchange_fees_paid=exchange_fees_paid,
        stamp_duty_paid=stamp_duty_paid,
        gst_paid=gst_paid,
        slippage_paid=slippage_paid,
    )

    if output_tearsheet_path:
        output_tearsheet_path.parent.mkdir(parents=True, exist_ok=True)
        output_tearsheet_path.write_text(tearsheet.to_markdown(), encoding="utf-8")
        logger.info("Tear sheet successfully written to %s", output_tearsheet_path)

    return result, tearsheet


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaTrader Historical Backtest Replay Runner")
    parser.add_argument(
        "--symbol",
        default="TCS.NS",
        help="Target symbol for replay (default: TCS.NS)",
    )
    parser.add_argument(
        "--data-file",
        default=str(REPO_ROOT / "data" / "historical" / "TCS_daily_2y.csv"),
        help="Path to historical OHLCV CSV file",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "docs" / "backtests" / "tearsheet_tcs.md"),
        help="Output markdown path for performance tear sheet",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1000000.0,
        help="Initial capital in INR (default: 1,000,000)",
    )
    parser.add_argument(
        "--allocation",
        type=float,
        default=0.25,
        help="Portfolio allocation per trade (default: 0.25)",
    )

    args = parser.parse_args()
    data_path = Path(args.data_file)
    output_path = Path(args.output) if args.output else None

    if not data_path.exists():
        logger.error("Data file does not exist: %s", data_path)
        sys.exit(1)

    _result, sheet = run_historical_replay(
        csv_path=data_path,
        symbol=args.symbol,
        initial_capital=Decimal(str(args.capital)),
        allocation_pct=Decimal(str(args.allocation)),
        output_tearsheet_path=output_path,
    )

    print("\n" + "=" * 60)
    print(f"  BACKTEST REPLAY FINISHED: {args.symbol}")
    print("=" * 60)
    print(f"Total Return:   {sheet.total_return_pct:+.2f}%")
    print(f"CAGR:           {sheet.cagr_pct:+.2f}%")
    print(f"Sharpe Ratio:   {sheet.sharpe_ratio:.2f}")
    print(f"Max Drawdown:   {sheet.max_drawdown_pct:.2f}%")
    print(f"Trades Executed: {sheet.total_trades} (Win Rate: {sheet.win_rate_pct:.1f}%)")
    print(f"Total Friction: INR {sheet.total_fees_paid:,.2f} (Drag: {sheet.cost_drag_pct:.2f}%)")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
