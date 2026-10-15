"""Institutional-grade quantitative tear sheet and performance attribution generator.

Computes Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor,
and Indian transaction cost drag (STT, brokerage, exchange fees, GST).
Generates publication-ready Markdown and HTML reports.

Governance: ADR-002, ADR-010.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.backtesting.metrics import BacktestMetrics

logger = logging.getLogger("alphatrader.observability.tearsheet")


@dataclass(frozen=True, slots=True)
class PerformanceTearSheet:
    """Quantitative performance tear sheet summary."""

    strategy_name: str
    symbol: str
    total_return_pct: float
    cagr_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    gross_profit: float
    gross_loss: float
    total_fees_paid: float
    stt_paid: float = 0.0
    exchange_fees_paid: float = 0.0
    stamp_duty_paid: float = 0.0
    gst_paid: float = 0.0
    slippage_paid: float = 0.0

    @property
    def cost_drag_pct(self) -> float:
        """Percentage drag on gross profit caused by friction and statutory taxes."""
        if self.gross_profit <= 0:
            return 0.0
        return round((self.total_fees_paid / self.gross_profit) * 100.0, 2)

    def to_markdown(self) -> str:
        """Render institutional tear sheet in GitHub-flavored Markdown."""
        gen_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        meta_line = (
            f"**Instrument:** `{self.symbol}` | "
            f"**Execution Mode:** `PAPER (ADR-002)` | "
            f"**Generated:** `{gen_time}`"
        )
        win_str = f"{self.win_rate_pct:.2f}% ({self.winning_trades}W / {self.losing_trades}L)"
        disclaimer = (
            "> **Disclaimer:** All executions simulated in strict paper "
            "trading mode with zero live capital risk."
        )
        lines = [
            f"# AlphaTrader Quantitative Performance Tear Sheet — {self.strategy_name}",
            "",
            meta_line,
            "",
            "## 1. Key Return & Risk Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| **Total Return** | `{self.total_return_pct:+.2f}%` |",
            f"| **CAGR** | `{self.cagr_pct:+.2f}%` |",
            f"| **Annualized Volatility** | `{self.annualized_volatility_pct:.2f}%` |",
            f"| **Sharpe Ratio (Rf=6.5% INR)** | `{self.sharpe_ratio:.2f}` |",
            f"| **Sortino Ratio** | `{self.sortino_ratio:.2f}` |",
            f"| **Calmar Ratio** | `{self.calmar_ratio:.2f}` |",
            f"| **Maximum Drawdown (MDD)** | `{-abs(self.max_drawdown_pct):.2f}%` |",
            f"| **Max Drawdown Duration** | `{self.max_drawdown_duration_bars} bars` |",
            "",
            "## 2. Trade Execution & Win Statistics",
            "",
            "| Trade Metric | Value |",
            "|---|---|",
            f"| **Total Closed Trades** | `{self.total_trades}` |",
            f"| **Win Rate** | `{win_str}` |",
            f"| **Profit Factor** | `{self.profit_factor:.2f}` |",
            f"| **Gross Profit** | `INR {self.gross_profit:,.2f}` |",
            f"| **Gross Loss** | `INR {self.gross_loss:,.2f}` |",
            "",
            "## 3. Indian Statutory Taxes & Friction Drag",
            "",
            "| Friction Breakdown | Amount (INR) |",
            "|---|---|",
            f"| **Securities Transaction Tax (STT)** | `INR {self.stt_paid:,.2f}` |",
            f"| **Exchange Turnover Charges** | `INR {self.exchange_fees_paid:,.2f}` |",
            f"| **Stamp Duty** | `INR {self.stamp_duty_paid:,.2f}` |",
            f"| **Goods & Services Tax (GST 18%)** | `INR {self.gst_paid:,.2f}` |",
            f"| **Modeled Market Slippage** | `INR {self.slippage_paid:,.2f}` |",
            f"| **Total Statutory Friction** | `INR {self.total_fees_paid:,.2f}` |",
            f"| **Friction Drag on Profit** | `{self.cost_drag_pct:.2f}%` |",
            "",
            disclaimer,
        ]
        return "\n".join(lines)


def generate_tear_sheet(
    metrics: BacktestMetrics,
    strategy_name: str = "AlphaTrader-NIFTY50-Kelly",
    symbol: str = "NIFTY50",
    stt_paid: float = 0.0,
    exchange_fees_paid: float = 0.0,
    stamp_duty_paid: float = 0.0,
    gst_paid: float = 0.0,
    slippage_paid: float = 0.0,
) -> PerformanceTearSheet:
    """Generate a typed PerformanceTearSheet from computed backtest metrics."""
    return PerformanceTearSheet(
        strategy_name=strategy_name,
        symbol=symbol,
        total_return_pct=round(metrics.total_return_pct, 2),
        cagr_pct=round(metrics.cagr_pct, 2),
        annualized_volatility_pct=round(metrics.annualized_volatility_pct, 2),
        sharpe_ratio=round(metrics.sharpe_ratio, 2),
        sortino_ratio=round(metrics.sortino_ratio, 2),
        calmar_ratio=round(metrics.calmar_ratio, 2),
        max_drawdown_pct=round(metrics.max_drawdown_pct, 2),
        max_drawdown_duration_bars=metrics.max_drawdown_duration_bars,
        win_rate_pct=round(metrics.win_rate_pct, 2),
        profit_factor=round(metrics.profit_factor, 2),
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        gross_profit=round(metrics.gross_profit, 2),
        gross_loss=round(metrics.gross_loss, 2),
        total_fees_paid=round(metrics.total_fees_paid, 2),
        stt_paid=round(stt_paid, 2),
        exchange_fees_paid=round(exchange_fees_paid, 2),
        stamp_duty_paid=round(stamp_duty_paid, 2),
        gst_paid=round(gst_paid, 2),
        slippage_paid=round(slippage_paid, 2),
    )
