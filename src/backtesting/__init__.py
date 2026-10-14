"""Backtesting engine package for AlphaTrader."""

from src.backtesting.costs import IndianEquityCostModel, TradeCostBreakdown
from src.backtesting.data_loader import HistoricalDataLoader
from src.backtesting.engine import BacktestEngine, BacktestResult, TradeRecord
from src.backtesting.metrics import BacktestMetrics, calculate_backtest_metrics
from src.backtesting.reporting import generate_backtest_report

__all__ = [
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "HistoricalDataLoader",
    "IndianEquityCostModel",
    "TradeCostBreakdown",
    "TradeRecord",
    "calculate_backtest_metrics",
    "generate_backtest_report",
]
