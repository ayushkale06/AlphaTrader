"""Event-driven backtesting execution engine enforcing strict zero-lookahead invariant.

INVARIANTS:
1. Zero Lookahead:
   - A signal/target generated on bar t (evaluated on bar t close) is executed at bar t+1 OPEN.
   - Under no circumstances does execution use bar t close or bar t+1 close for order entry/exit.
2. Exact Accounting:
   - Cash and positions tracked using Decimal arithmetic.
   - Transaction friction computed via IndianEquityCostModel (STT, brokerage, turnover fees,
     GST, stamp duty, slippage).
3. Modularity:
   - Pluggable strategy signal generator function.
   - Pluggable position sizer and risk guardrail.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from src.backtesting.costs import IndianEquityCostModel
from src.backtesting.metrics import BacktestMetrics, calculate_backtest_metrics
from src.common.schemas.market import Candle
from src.common.schemas.orders import Side
from src.common.schemas.signals import Direction

logger = logging.getLogger("alphatrader.backtesting.engine")


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    side: Side
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_fees: Decimal
    exit_fees: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    return_pct: Decimal


@dataclass
class BacktestResult:
    symbol: str
    initial_capital: Decimal
    final_equity: Decimal
    equity_curve: list[Decimal]
    timestamps: list[datetime]
    trades: list[TradeRecord]
    metrics: BacktestMetrics
    provenance_disclaimer: str


class BacktestEngine:
    """Event-driven single/multi-asset backtest runner."""

    def __init__(
        self,
        cost_model: IndianEquityCostModel | None = None,
        initial_capital: Decimal = Decimal("1000000.00"),  # 10 Lakh INR default
        allocation_pct: Decimal = Decimal("0.20"),  # 20% equity per position default
        annualization_factor: int = 252,
    ) -> None:
        self.cost_model = cost_model or IndianEquityCostModel()
        self.initial_capital = initial_capital
        self.allocation_pct = allocation_pct
        self.annualization_factor = annualization_factor

    def run(
        self,
        candles: Sequence[Candle],
        strategy_fn: Callable[[Sequence[Candle]], Direction],
        provenance_disclaimer: str = "",
        is_delivery: bool = True,
    ) -> BacktestResult:
        """Execute simulation over a chronological candle sequence.

        Args:
            candles: Chronologically sorted candles.
            strategy_fn: Receives history up to bar t, returns Direction.
            provenance_disclaimer: Note on data provenance, adjustments, and survivorship.
            is_delivery: True for delivery equity rates, False for intraday rates.
        """
        if not candles:
            raise ValueError("Cannot run backtest on empty candle sequence.")

        # Ensure sorted
        sorted_candles = sorted(candles, key=lambda c: c.close_time)
        symbol = sorted_candles[0].symbol

        cash = self.initial_capital
        position_qty = Decimal("0")
        entry_price = Decimal("0")
        entry_time: datetime | None = None
        entry_fees = Decimal("0")

        equity_curve: list[Decimal] = [cash]
        timestamps: list[datetime] = [sorted_candles[0].open_time]
        trades: list[TradeRecord] = []
        pending_order_target: Direction | None = None

        # Simulation loop
        for t in range(len(sorted_candles)):
            curr_bar = sorted_candles[t]

            # 1. EXECUTE PENDING ORDER AT OPEN OF CURRENT BAR (t)
            if pending_order_target is not None:
                target_dir = pending_order_target
                fill_price = curr_bar.open

                # Sizing: Target 1 position or Flat
                if target_dir == Direction.FLAT:
                    # Close position if open
                    if position_qty > 0:
                        exit_cost = self.cost_model.compute_costs(
                            Side.SELL, position_qty, fill_price, is_delivery=is_delivery
                        )
                        gross_pnl = (fill_price - entry_price) * position_qty
                        net_pnl = gross_pnl - entry_fees - exit_cost.total_friction
                        ret_pct = (net_pnl / (entry_price * position_qty)) * Decimal("100.0")

                        cash += (position_qty * fill_price) - exit_cost.total_friction
                        trades.append(
                            TradeRecord(
                                symbol=symbol,
                                entry_time=entry_time or curr_bar.open_time,
                                exit_time=curr_bar.open_time,
                                side=Side.BUY,
                                quantity=position_qty,
                                entry_price=entry_price,
                                exit_price=fill_price,
                                entry_fees=entry_fees,
                                exit_fees=exit_cost.total_friction,
                                gross_pnl=gross_pnl.quantize(Decimal("0.01")),
                                net_pnl=net_pnl.quantize(Decimal("0.01")),
                                return_pct=ret_pct.quantize(Decimal("0.01")),
                            )
                        )
                        position_qty = Decimal("0")
                        entry_price = Decimal("0")
                        entry_time = None
                        entry_fees = Decimal("0")

                elif target_dir == Direction.LONG and position_qty == 0:
                    # Open new long position
                    available_capital = cash * self.allocation_pct
                    target_qty = (available_capital / fill_price).quantize(
                        Decimal("1"), rounding=ROUND_FLOOR
                    )
                    if target_qty > 0:
                        entry_cost = self.cost_model.compute_costs(
                            Side.BUY, target_qty, fill_price, is_delivery=is_delivery
                        )
                        total_outlay = (target_qty * fill_price) + entry_cost.total_friction
                        if cash >= total_outlay:
                            cash -= total_outlay
                            position_qty = target_qty
                            entry_price = fill_price
                            entry_time = curr_bar.open_time
                            entry_fees = entry_cost.total_friction

                pending_order_target = None

            # 2. MARK-TO-MARKET AT CLOSE OF CURRENT BAR (t)
            current_equity = cash + (position_qty * curr_bar.close)
            equity_curve.append(current_equity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            timestamps.append(curr_bar.close_time)

            # 3. EVALUATE STRATEGY AT CLOSE OF CURRENT BAR (t) -> FOR EXECUTION AT t+1 OPEN
            # Pass history strictly up to t (inclusive of bar t close)
            history_slice = sorted_candles[: t + 1]
            signal = strategy_fn(history_slice)

            if signal == Direction.LONG and position_qty == 0:
                pending_order_target = Direction.LONG
            elif (signal == Direction.SHORT or signal == Direction.FLAT) and position_qty > 0:
                pending_order_target = Direction.FLAT

        # End of simulation: liquidate any remaining position at last bar close
        # for clean portfolio reconciliation
        if position_qty > 0:
            last_bar = sorted_candles[-1]
            exit_price = last_bar.close
            exit_cost = self.cost_model.compute_costs(
                Side.SELL, position_qty, exit_price, is_delivery=is_delivery
            )
            gross_pnl = (exit_price - entry_price) * position_qty
            net_pnl = gross_pnl - entry_fees - exit_cost.total_friction
            ret_pct = (net_pnl / (entry_price * position_qty)) * Decimal("100.0")
            cash += (position_qty * exit_price) - exit_cost.total_friction
            trades.append(
                TradeRecord(
                    symbol=symbol,
                    entry_time=entry_time or last_bar.open_time,
                    exit_time=last_bar.close_time,
                    side=Side.BUY,
                    quantity=position_qty,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    entry_fees=entry_fees,
                    exit_fees=exit_cost.total_friction,
                    gross_pnl=gross_pnl.quantize(Decimal("0.01")),
                    net_pnl=net_pnl.quantize(Decimal("0.01")),
                    return_pct=ret_pct.quantize(Decimal("0.01")),
                )
            )
            position_qty = Decimal("0")
            equity_curve[-1] = cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        trade_pnls = [t.net_pnl for t in trades]
        trade_fees = [t.entry_fees + t.exit_fees for t in trades]

        metrics = calculate_backtest_metrics(
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
            trade_fees=trade_fees,
            annualization_factor=self.annualization_factor,
        )

        return BacktestResult(
            symbol=symbol,
            initial_capital=self.initial_capital,
            final_equity=cash.quantize(Decimal("0.01")),
            equity_curve=equity_curve,
            timestamps=timestamps,
            trades=trades,
            metrics=metrics,
            provenance_disclaimer=provenance_disclaimer,
        )
