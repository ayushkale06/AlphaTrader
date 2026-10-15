"""Unit tests for institutional quantitative tearsheet generator.

Verifies:
1. PerformanceTearSheet dataclass metric calculations and cost drag.
2. Markdown rendering with ADR-002 disclaimer and Indian statutory tax breakdown.
3. Edge cases (0 gross profit, zero trades).
"""

from __future__ import annotations

import pytest

from src.backtesting.metrics import BacktestMetrics, ConfidenceInterval
from src.backtesting.tearsheet import PerformanceTearSheet, generate_tear_sheet


@pytest.fixture
def sample_metrics() -> BacktestMetrics:
    ci = ConfidenceInterval(point_estimate=12.5, lower_95=10.1, upper_95=14.9)
    return BacktestMetrics(
        total_return_pct=15.42,
        cagr_pct=12.50,
        cagr_ci_95=ci,
        annualized_volatility_pct=16.80,
        sharpe_ratio=1.45,
        sharpe_ci_95=ci,
        sortino_ratio=1.82,
        calmar_ratio=1.20,
        max_drawdown_pct=10.42,
        max_drawdown_duration_bars=45,
        win_rate_pct=58.33,
        profit_factor=1.75,
        total_trades=24,
        winning_trades=14,
        losing_trades=10,
        gross_profit=150000.0,
        gross_loss=85714.28,
        total_fees_paid=4500.0,
    )


def test_generate_tear_sheet(sample_metrics: BacktestMetrics) -> None:
    sheet = generate_tear_sheet(
        metrics=sample_metrics,
        strategy_name="AlphaKelly-NIFTY50",
        symbol="NIFTY50",
        stt_paid=2500.0,
        exchange_fees_paid=200.0,
        stamp_duty_paid=300.0,
        gst_paid=500.0,
        slippage_paid=1000.0,
    )

    assert sheet.strategy_name == "AlphaKelly-NIFTY50"
    assert sheet.symbol == "NIFTY50"
    assert sheet.total_return_pct == 15.42
    assert sheet.cagr_pct == 12.50
    assert sheet.sharpe_ratio == 1.45
    assert sheet.sortino_ratio == 1.82
    assert sheet.calmar_ratio == 1.20
    assert sheet.max_drawdown_pct == 10.42
    assert sheet.total_trades == 24
    assert sheet.win_rate_pct == 58.33
    assert sheet.total_fees_paid == 4500.0
    assert sheet.stt_paid == 2500.0
    assert sheet.exchange_fees_paid == 200.0
    assert sheet.stamp_duty_paid == 300.0
    assert sheet.gst_paid == 500.0
    assert sheet.slippage_paid == 1000.0


def test_cost_drag_percentage(sample_metrics: BacktestMetrics) -> None:
    sheet = generate_tear_sheet(
        metrics=sample_metrics,
        strategy_name="AlphaKelly-NIFTY50",
        symbol="NIFTY50",
    )
    # total_fees_paid (4500) / gross_profit (150000) * 100 = 3.0%
    assert sheet.cost_drag_pct == 3.0


def test_cost_drag_zero_profit(sample_metrics: BacktestMetrics) -> None:
    sheet = PerformanceTearSheet(
        strategy_name="ZeroProfit",
        symbol="TCS.NS",
        total_return_pct=0.0,
        cagr_pct=0.0,
        annualized_volatility_pct=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_duration_bars=0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        gross_profit=0.0,
        gross_loss=0.0,
        total_fees_paid=150.0,
    )
    assert sheet.cost_drag_pct == 0.0


def test_to_markdown_format(sample_metrics: BacktestMetrics) -> None:
    sheet = generate_tear_sheet(
        metrics=sample_metrics,
        strategy_name="AlphaKelly-NIFTY50",
        symbol="NIFTY50",
        stt_paid=2500.0,
    )
    md = sheet.to_markdown()

    assert "# AlphaTrader Quantitative Performance Tear Sheet — AlphaKelly-NIFTY50" in md
    assert "**Instrument:** `NIFTY50`" in md
    assert "**Execution Mode:** `PAPER (ADR-002)`" in md
    assert "Securities Transaction Tax (STT)" in md
    assert "INR 2,500.00" in md
    assert "1. Key Return & Risk Summary" in md
    assert "2. Trade Execution & Win Statistics" in md
    assert "3. Indian Statutory Taxes & Friction Drag" in md
