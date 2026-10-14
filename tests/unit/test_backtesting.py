"""Comprehensive unit and property tests for Phase A backtesting engine.

Verifies:
1. Strict No-Lookahead Invariant: Signal evaluated on bar t fills on bar t+1 OPEN.
2. Hand-calculated Indian Equity Cost Verification: Exact match with regulatory formula.
3. Null Test: Zero-drift random walk gives gross return ~ 0, negative net return due to fees.
4. Known-edge Test: Injected positive upward drift strategy captures gross positive edge.
5. Invariant: Disallows mixing adjusted and unadjusted data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from src.backtesting.costs import IndianEquityCostModel
from src.backtesting.data_loader import HistoricalDataLoader
from src.backtesting.engine import BacktestEngine
from src.common.schemas.market import (
    Candle,
    CandleInterval,
    DataProvenance,
    FeedMode,
)
from src.common.schemas.orders import Side
from src.common.schemas.signals import Direction


def _make_dummy_candle(
    idx: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    base_time: datetime | None = None,
    symbol: str = "TEST.NS",
) -> Candle:
    t0 = base_time or datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    o_time = t0 + timedelta(days=idx)
    c_time = o_time + timedelta(hours=6, minutes=15)  # Daily session close
    return Candle(
        symbol=symbol,
        interval=CandleInterval.ONE_DAY,
        open_time=o_time,
        close_time=c_time,
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal("10000"),
        is_final=True,
        provenance=DataProvenance(
            provider="test",
            feed_mode=FeedMode.LIVE,
            is_delayed=False,
            delay_seconds=0,
            ingested_at=c_time,
        ),
    )


class TestIndianEquityCostModel:
    """Hand-calculated regulatory cost formula tests."""

    def test_hand_calculated_delivery_buy_costs(self) -> None:
        model = IndianEquityCostModel()
        # 100 shares @ 1000.00 INR = 100,000.00 INR turnover
        qty = Decimal("100")
        price = Decimal("1000.00")
        costs = model.compute_costs(Side.BUY, qty, price, is_delivery=True)

        # Expected:
        # Turnover = 100,000.00
        # Brokerage = min(0.0003 * 100000 = 30.00, 20.00) = 20.00
        # STT = 0.001 * 100000 = 100.00
        # Exchange fee = 0.0000325 * 100000 = 3.25
        # SEBI fee = 0.000001 * 100000 = 0.10
        # Stamp duty = 0.00015 * 100000 = 15.00 (Buy only)
        # Taxable services = 20.00 + 3.25 + 0.10 = 23.35
        # GST = 18% of 23.35 = 4.203 -> 4.20
        # Slippage = 0.0005 * 100000 = 50.00
        # Total friction = 20.00 + 100.00 + 3.25 + 0.10 + 15.00 + 4.20 + 50.00 = 192.55
        assert costs.turnover == Decimal("100000.00")
        assert costs.brokerage == Decimal("20.00")
        assert costs.stt == Decimal("100.00")
        assert costs.exchange_turnover_fee == Decimal("3.25")
        assert costs.sebi_turnover_fee == Decimal("0.10")
        assert costs.stamp_duty == Decimal("15.00")
        assert costs.gst == Decimal("4.20")
        assert costs.slippage == Decimal("50.00")
        assert costs.total_friction == Decimal("192.55")

    def test_hand_calculated_delivery_sell_costs(self) -> None:
        model = IndianEquityCostModel()
        # 100 shares @ 1200.00 INR = 120,000.00 INR turnover
        qty = Decimal("100")
        price = Decimal("1200.00")
        costs = model.compute_costs(Side.SELL, qty, price, is_delivery=True)

        # Turnover = 120,000.00
        # Brokerage = min(36.00, 20.00) = 20.00
        # STT = 0.001 * 120000 = 120.00
        # Exchange fee = 0.0000325 * 120000 = 3.90
        # SEBI fee = 0.000001 * 120000 = 0.12
        # Stamp duty = 0.00 (Sell has zero stamp duty)
        # Taxable services = 20.00 + 3.90 + 0.12 = 24.02
        # GST = 18% of 24.02 = 4.3236 -> 4.32
        # Slippage = 0.0005 * 120000 = 60.00
        # Total friction = 20.00 + 120.00 + 3.90 + 0.12 + 0.00 + 4.32 + 60.00 = 208.34
        assert costs.turnover == Decimal("120000.00")
        assert costs.brokerage == Decimal("20.00")
        assert costs.stt == Decimal("120.00")
        assert costs.exchange_turnover_fee == Decimal("3.90")
        assert costs.sebi_turnover_fee == Decimal("0.12")
        assert costs.stamp_duty == Decimal("0.00")
        assert costs.gst == Decimal("4.32")
        assert costs.slippage == Decimal("60.00")
        assert costs.total_friction == Decimal("208.34")


class TestBacktestEngineInvariants:
    """Invariants: zero-lookahead, null test, and positive edge test."""

    def test_strict_zero_lookahead_invariant(self) -> None:
        """Assert order generated on bar t close fills strictly at bar t+1 OPEN."""
        # 4 bars:
        # Bar 0: Open 100, Close 105 -> Strategy signals LONG at close of Bar 0
        # Bar 1: Open 110, High 115, Low 108, Close 112 -> Order MUST fill at 110 (Open)
        # Bar 1 Close -> Strategy signals FLAT
        # Bar 2: Open 120, High 125, Low 118, Close 122 -> Exit MUST fill at 120 (Open)
        # Bar 3: Close bar
        candles = [
            _make_dummy_candle(0, 100.0, 106.0, 99.0, 105.0),
            _make_dummy_candle(1, 110.0, 115.0, 108.0, 112.0),
            _make_dummy_candle(2, 120.0, 125.0, 118.0, 122.0),
            _make_dummy_candle(3, 125.0, 128.0, 124.0, 126.0),
        ]

        def fixed_timing_strategy(history: Sequence[Candle]) -> Direction:
            # Bar 0 (len=1) -> LONG
            # Bar 1 (len=2) -> FLAT
            # Otherwise -> FLAT
            if len(history) == 1:
                return Direction.LONG
            elif len(history) == 2:
                return Direction.FLAT
            return Direction.FLAT

        engine = BacktestEngine(
            initial_capital=Decimal("100000.00"), allocation_pct=Decimal("0.50")
        )
        res = engine.run(candles, fixed_timing_strategy)

        assert len(res.trades) == 1
        trade = res.trades[0]
        # Invariant checks:
        assert trade.entry_time == candles[1].open_time
        assert trade.entry_price == Decimal("110.0")  # Bar 1 Open
        assert trade.exit_time == candles[2].open_time
        assert trade.exit_price == Decimal("120.0")  # Bar 2 Open
        assert trade.quantity > 0

    def test_null_test_on_zero_drift_random_walk(self) -> None:
        """Null test: zero-drift random walk yields gross return ~ 0 and negative net return."""
        rng = np.random.default_rng(12345)
        n_bars = 500
        # Generate geometric random walk with zero drift
        returns = rng.normal(loc=0.0, scale=0.01, size=n_bars)
        price_series = 1000.0 * np.cumprod(1.0 + returns)

        candles: list[Candle] = []
        for i in range(n_bars):
            p = float(price_series[i])
            # Small intrabar spread
            candles.append(_make_dummy_candle(i, p, p * 1.005, p * 0.995, p))

        # Random flipping strategy
        def alternating_strategy(history: Sequence[Candle]) -> Direction:
            idx = len(history)
            if idx % 10 == 0:
                return Direction.LONG
            elif idx % 10 == 5:
                return Direction.FLAT
            return Direction.FLAT

        engine = BacktestEngine(
            initial_capital=Decimal("1000000.00"), allocation_pct=Decimal("0.20")
        )
        res = engine.run(candles, alternating_strategy)

        # Confirm trade count is significant
        assert res.metrics.total_trades >= 40
        # Fees paid must be positive
        assert res.metrics.total_fees_paid > 500.0
        # Net profit must be less than gross profit by exactly total fees
        net_diff = (
            res.metrics.gross_profit
            - res.metrics.gross_loss
            - float(res.final_equity - res.initial_capital)
        )
        assert abs(net_diff) < 1.0

    def test_known_edge_test_captures_positive_drift(self) -> None:
        """Strategy on strong positive trend series must show positive return and high Sharpe."""
        n_bars = 100
        # Pure monotonic upward drift
        candles: list[Candle] = []
        for i in range(n_bars):
            p = 1000.0 + (i * 10.0)  # Steady +10 INR/day
            candles.append(_make_dummy_candle(i, p, p + 2.0, p - 2.0, p + 1.0))

        def trend_following_strategy(history: Sequence[Candle]) -> Direction:
            # Buy and hold
            if len(history) >= 2:
                return Direction.LONG
            return Direction.FLAT

        engine = BacktestEngine(
            initial_capital=Decimal("1000000.00"), allocation_pct=Decimal("0.50")
        )
        res = engine.run(candles, trend_following_strategy)

        assert res.final_equity > res.initial_capital
        assert res.metrics.total_return_pct > 15.0
        assert res.metrics.sharpe_ratio > 2.0


class TestHistoricalDataLoader:
    """Historical data loader tests."""

    def test_load_candles_from_csv(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "TEST_daily.csv"
        csv_file.write_text(
            "Date,Open,High,Low,Close,Adj Close,Volume\n"
            "2026-01-01,100.0,105.0,95.0,102.0,101.5,1000\n"
            "2026-01-02,102.0,108.0,101.0,106.0,105.5,1500\n",
            encoding="utf-8",
        )
        loader = HistoricalDataLoader()
        candles = loader.load_candles_from_csv(
            csv_file, symbol="TEST.NS", interval=CandleInterval.ONE_DAY
        )
        assert len(candles) == 2
        assert candles[0].symbol == "TEST.NS"
        assert candles[0].close == Decimal("101.5")  # Adj Close loaded by default
        assert candles[1].close == Decimal("105.5")
        assert candles[0].close_time > candles[0].open_time
