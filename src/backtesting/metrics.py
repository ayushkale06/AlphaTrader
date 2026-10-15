"""Backtesting performance and risk metrics calculation with bootstrap uncertainty.

Metrics computed:
- Total Return (%)
- CAGR (%) - Compounded Annual Growth Rate
- Annualized Volatility (%)
- Sharpe Ratio (using risk-free rate, annualized with standard trading days)
- Sortino Ratio (using downside deviation)
- Maximum Drawdown (MDD, %) and Drawdown Duration (days)
- Calmar Ratio (CAGR / |Max Drawdown|)
- Win Rate (%), Profit Factor, Average Trade Return (%)
- Total Trades, Long Trades, Short Trades
- Stationary Bootstrap 95% Confidence Intervals for Sharpe and CAGR (Politis & Romano, 1994)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class ConfidenceInterval:
    lower_95: float
    point_estimate: float
    upper_95: float

    def to_dict(self) -> dict[str, float]:
        return {
            "lower_95": round(self.lower_95, 4),
            "point_estimate": round(self.point_estimate, 4),
            "upper_95": round(self.upper_95, 4),
        }


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    cagr_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    calmar_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    gross_profit: float
    gross_loss: float
    total_fees_paid: float
    cagr_ci_95: ConfidenceInterval
    sharpe_ci_95: ConfidenceInterval

    def to_dict(self) -> dict[str, object]:
        return {
            "total_return_pct": round(self.total_return_pct, 4),
            "cagr_pct": round(self.cagr_pct, 4),
            "annualized_volatility_pct": round(self.annualized_volatility_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "calmar_ratio": round(self.calmar_ratio, 4),
            "win_rate_pct": round(self.win_rate_pct, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "gross_profit": round(self.gross_profit, 2),
            "gross_loss": round(self.gross_loss, 2),
            "total_fees_paid": round(self.total_fees_paid, 2),
            "cagr_ci_95": self.cagr_ci_95.to_dict(),
            "sharpe_ci_95": self.sharpe_ci_95.to_dict(),
        }


def compute_stationary_bootstrap_ci(
    returns: npt.NDArray[np.float64],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    n_bootstrap: int = 1000,
    expected_block_len: int = 10,
    seed: int = 42,
) -> ConfidenceInterval:
    """Stationary bootstrap (Politis & Romano, 1994) for dependent financial time series."""
    n = len(returns)
    if n < 5:
        pt = float(stat_fn(returns)) if n > 0 else 0.0
        return ConfidenceInterval(lower_95=pt, point_estimate=pt, upper_95=pt)

    rng = np.random.default_rng(seed)
    p = 1.0 / max(expected_block_len, 1)

    boot_stats = np.empty(n_bootstrap, dtype=float)
    point_est = float(stat_fn(returns))

    for b in range(n_bootstrap):
        indices = np.empty(n, dtype=int)
        curr_idx = int(rng.integers(0, n))
        for i in range(n):
            curr_idx = int(rng.integers(0, n)) if rng.random() < p else int((curr_idx + 1) % n)
            indices[i] = curr_idx

        sample_returns = returns[indices]
        boot_stats[b] = stat_fn(sample_returns)

    # Remove non-finite values if any
    valid_stats = boot_stats[np.isfinite(boot_stats)]
    if len(valid_stats) == 0:
        return ConfidenceInterval(lower_95=point_est, point_estimate=point_est, upper_95=point_est)

    lower = float(np.percentile(valid_stats, 2.5))
    upper = float(np.percentile(valid_stats, 97.5))
    return ConfidenceInterval(lower_95=lower, point_estimate=point_est, upper_95=upper)


def calculate_backtest_metrics(
    equity_curve: Sequence[Decimal | float],
    trade_pnls: Sequence[Decimal | float],
    trade_fees: Sequence[Decimal | float],
    annualization_factor: int = 252,
    risk_free_rate: float = 0.065,  # 6.5% Indian 10y benchmark proxy
) -> BacktestMetrics:
    """Calculate comprehensive performance metrics from equity curve and completed trade logs."""
    eq = np.array([float(x) for x in equity_curve], dtype=float)
    if len(eq) < 2:
        zero_ci = ConfidenceInterval(0.0, 0.0, 0.0)
        return BacktestMetrics(
            total_return_pct=0.0,
            cagr_pct=0.0,
            annualized_volatility_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_duration_bars=0,
            calmar_ratio=0.0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            gross_profit=0.0,
            gross_loss=0.0,
            total_fees_paid=0.0,
            cagr_ci_95=zero_ci,
            sharpe_ci_95=zero_ci,
        )

    initial_capital = eq[0]
    final_equity = eq[-1]
    n_bars = len(eq) - 1

    # Bar-by-bar arithmetic returns
    returns = np.diff(eq) / eq[:-1]

    # Total Return & CAGR
    total_return = (final_equity - initial_capital) / initial_capital
    total_return_pct = total_return * 100.0

    years = n_bars / float(annualization_factor)
    if years > 0 and final_equity > 0 and initial_capital > 0:
        cagr = (final_equity / initial_capital) ** (1.0 / years) - 1.0
        cagr_pct = cagr * 100.0
    else:
        cagr = 0.0
        cagr_pct = 0.0

    # Annualized Volatility
    ann_factor_sqrt = math.sqrt(annualization_factor)
    std_ret = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    ann_vol_pct = (std_ret * ann_factor_sqrt) * 100.0

    # Sharpe Ratio
    rf_per_bar = risk_free_rate / annualization_factor
    excess_returns = returns - rf_per_bar
    sharpe = (
        (float(np.mean(excess_returns)) / std_ret) * ann_factor_sqrt if std_ret > 1e-12 else 0.0
    )

    # Sortino Ratio
    downside_returns = returns[returns < rf_per_bar] - rf_per_bar
    if len(downside_returns) > 0:
        downside_std = math.sqrt(float(np.mean(downside_returns**2)))
        sortino = (
            ((float(np.mean(returns)) - rf_per_bar) / downside_std) * ann_factor_sqrt
            if downside_std > 1e-12
            else 0.0
        )
    else:
        sortino = 0.0

    # Maximum Drawdown and Duration
    running_max = np.maximum.accumulate(eq)
    drawdowns = (eq - running_max) / running_max
    max_dd = float(np.min(drawdowns))
    max_dd_pct = abs(max_dd) * 100.0

    # Max Drawdown Duration
    max_dd_duration = 0
    curr_duration = 0
    for dd in drawdowns:
        if dd < 0:
            curr_duration += 1
            if curr_duration > max_dd_duration:
                max_dd_duration = curr_duration
        else:
            curr_duration = 0

    # Calmar Ratio
    calmar = (cagr / abs(max_dd)) if abs(max_dd) > 1e-6 else 0.0

    # Trade statistics
    pnls = [float(p) for p in trade_pnls]
    fees = [float(f) for f in trade_fees]
    total_trades = len(pnls)
    winning = [p for p in pnls if p > 0]
    losing = [p for p in pnls if p < 0]

    gross_profit = sum(winning)
    gross_loss = abs(sum(losing))
    total_fees = sum(fees)

    win_rate_pct = (len(winning) / total_trades * 100.0) if total_trades > 0 else 0.0
    profit_factor = (
        (gross_profit / gross_loss) if gross_loss > 1e-12 else (999.0 if gross_profit > 0 else 0.0)
    )

    # Bootstrap Confidence Intervals
    def bootstrap_sharpe(r: npt.NDArray[np.float64]) -> float:
        s = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        if s < 1e-12:
            return 0.0
        ex = r - rf_per_bar
        return (float(np.mean(ex)) / s) * ann_factor_sqrt

    def bootstrap_cagr(r: npt.NDArray[np.float64]) -> float:
        compounded = float(np.prod(1.0 + r))
        if compounded <= 0 or years <= 0:
            return 0.0
        return float(((compounded ** (1.0 / years)) - 1.0) * 100.0)

    sharpe_ci = compute_stationary_bootstrap_ci(returns, bootstrap_sharpe)
    cagr_ci = compute_stationary_bootstrap_ci(returns, bootstrap_cagr)

    return BacktestMetrics(
        total_return_pct=total_return_pct,
        cagr_pct=cagr_pct,
        annualized_volatility_pct=ann_vol_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=max_dd_duration,
        calmar_ratio=calmar,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        total_trades=total_trades,
        winning_trades=len(winning),
        losing_trades=len(losing),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        total_fees_paid=total_fees,
        cagr_ci_95=cagr_ci,
        sharpe_ci_95=sharpe_ci,
    )
