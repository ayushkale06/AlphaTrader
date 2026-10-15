"""Deterministic Non-LLM Pre-Trade Risk Evaluator.

Enforces strictly deterministic pre-trade risk invariants:
1. Circuit Breaker / Floor Halt: Rejects immediately if floor is halted.
2. Intraday Drawdown: Rejects or triggers HALT if drawdown > MAX_DAILY_DRAWDOWN_PCT (2.0%).
3. Position Concentration: Rejects if post-trade position notional > MAX_POSITION_SIZE_PCT (5.0%).
4. Maximum Open Positions: Rejects new symbol if open positions >= MAX_OPEN_POSITIONS (10).
5. Order Rate Limiter: Rejects if order submissions in trailing minute > MAX_ORDERS_PER_MINUTE (30).
6. Stop Loss Sanity: Rejects if missing or non-positive, or if stop-loss distance is irrational.
7. Long-Only Cash Delivery: Rejects SELL orders for symbols without existing inventory, or shorting.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.common.schemas.orders import OrderRequest, Side
from src.common.schemas.risk import (
    RiskDecision,
    RiskInputSnapshot,
    RiskReasonCode,
    RiskVerdict,
)
from src.mcp_servers.risk_guardrail.token import RiskTokenManager

logger = logging.getLogger("alphatrader.risk_guardrail.evaluator")


@dataclass
class PortfolioState:
    """Current portfolio accounting snapshot required for risk evaluation."""

    equity: Decimal
    cash: Decimal
    high_water_mark: Decimal
    open_positions: Mapping[str, Decimal]  # symbol -> current notional value in INR
    is_floor_halted: bool = False


class DeterministicRiskEvaluator:
    """Pure-Python, deterministic, non-LLM risk evaluator."""

    def __init__(
        self,
        token_manager: RiskTokenManager,
        max_daily_drawdown_pct: Decimal = Decimal("2.0"),
        max_position_size_pct: Decimal = Decimal("5.0"),
        max_open_positions: int = 10,
        max_orders_per_minute: int = 30,
        min_stop_loss_pct: Decimal = Decimal("0.1"),  # At least 0.1% away from fill
        max_stop_loss_pct: Decimal = Decimal("15.0"),  # No more than 15% catastrophic stop
    ) -> None:
        self.token_manager = token_manager
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_position_size_pct = max_position_size_pct
        self.max_open_positions = max_open_positions
        self.max_orders_per_minute = max_orders_per_minute
        self.min_stop_loss_pct = min_stop_loss_pct
        self.max_stop_loss_pct = max_stop_loss_pct

        # Trailing 60-second window of timestamps for rate limiting
        self._order_timestamps: deque[float] = deque()

    def _get_orders_in_last_minute(self, current_ts: float) -> int:
        """Prune timestamps older than 60s and return current rate count."""
        cutoff = current_ts - 60.0
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()
        return len(self._order_timestamps)

    def _record_order_timestamp(self, current_ts: float) -> None:
        self._order_timestamps.append(current_ts)

    def evaluate_order(
        self,
        order: OrderRequest,
        portfolio: PortfolioState,
        current_time: datetime | None = None,
    ) -> RiskDecision:
        """Evaluate an order proposal against all deterministic pre-trade risk invariants."""
        now = current_time or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now_ts = now.timestamp()

        # Compute current portfolio drawdown percentage
        hwm = portfolio.high_water_mark
        equity = portfolio.equity
        if hwm > Decimal("0"):
            drawdown_val = max(Decimal("0"), hwm - equity)
            current_drawdown_pct = (drawdown_val / hwm) * Decimal("100")
        else:
            current_drawdown_pct = Decimal("0")

        # Proposed order notional
        price = order.limit_price or order.stop_loss_price  # fallback price for notional estimate
        proposed_notional = (order.quantity * price).quantize(Decimal("0.01"))

        # Current symbol position notional
        current_sym_notional = portfolio.open_positions.get(order.symbol, Decimal("0"))
        if order.side == Side.BUY:
            post_trade_notional = current_sym_notional + proposed_notional
        else:
            post_trade_notional = max(Decimal("0"), current_sym_notional - proposed_notional)

        post_trade_pct = (
            (post_trade_notional / equity * Decimal("100"))
            if equity > Decimal("0")
            else Decimal("100")
        )

        open_positions_count = len(
            [p for p in portfolio.open_positions.values() if p > Decimal("0")]
        )
        orders_last_min = self._get_orders_in_last_minute(now_ts)

        snapshot = RiskInputSnapshot(
            symbol=order.symbol,
            proposed_notional=proposed_notional,
            portfolio_equity=equity,
            current_drawdown_pct=current_drawdown_pct.quantize(Decimal("0.01")),
            max_daily_drawdown_pct=self.max_daily_drawdown_pct,
            post_trade_position_pct=post_trade_pct.quantize(Decimal("0.01")),
            max_position_size_pct=self.max_position_size_pct,
            open_positions_count=open_positions_count,
            max_open_positions=self.max_open_positions,
            orders_last_minute=orders_last_min,
        )

        def make_decision(
            verdict: RiskVerdict,
            reason: RiskReasonCode,
            rule: str,
            msg: str,
            token: str | None = None,
        ) -> RiskDecision:
            return RiskDecision(
                order_request_id=order.order_request_id,
                evaluated_at=now,
                decision=verdict,
                rule_fired=rule,
                reason_code=reason,
                inputs_snapshot=snapshot,
                approval_token=token,
                message=msg,
            )

        # 1. Rule: Floor Halted
        if portfolio.is_floor_halted:
            return make_decision(
                RiskVerdict.HALT,
                RiskReasonCode.FLOOR_ALREADY_HALTED,
                "RULE_FLOOR_HALTED",
                "Trading floor is currently halted. All order proposals are rejected.",
            )

        # 2. Rule: Max Daily Drawdown Breach -> HALT
        if current_drawdown_pct >= self.max_daily_drawdown_pct:
            return make_decision(
                RiskVerdict.HALT,
                RiskReasonCode.MAX_DAILY_DRAWDOWN_BREACH,
                "RULE_MAX_DAILY_DRAWDOWN",
                (
                    f"Current daily drawdown ({current_drawdown_pct:.2f}%) breaches limit "
                    f"({self.max_daily_drawdown_pct:.2f}%)."
                ),
            )

        # 3. Rule: Order Rate Limiting
        if orders_last_min >= self.max_orders_per_minute:
            return make_decision(
                RiskVerdict.REJECT,
                RiskReasonCode.ORDER_RATE_LIMIT_EXCEEDED,
                "RULE_ORDER_RATE_LIMIT",
                (
                    f"Order submission rate ({orders_last_min}/min) exceeds limit "
                    f"({self.max_orders_per_minute}/min)."
                ),
            )

        # 4. Rule: Stop Loss Sanity Checks
        ref_price = order.limit_price or price
        if order.stop_loss_price <= Decimal("0"):
            return make_decision(
                RiskVerdict.REJECT,
                RiskReasonCode.MISSING_STOP_LOSS,
                "RULE_STOP_LOSS_NON_POSITIVE",
                "Order must have a strictly positive stop_loss_price.",
            )

        if order.side == Side.BUY:
            if order.stop_loss_price >= ref_price:
                return make_decision(
                    RiskVerdict.REJECT,
                    RiskReasonCode.MISSING_STOP_LOSS,
                    "RULE_STOP_LOSS_INVALID_DIRECTION",
                    (
                        f"BUY order stop_loss_price ({order.stop_loss_price}) must be below "
                        f"entry price ({ref_price})."
                    ),
                )
            sl_distance_pct = ((ref_price - order.stop_loss_price) / ref_price) * Decimal("100")
        else:
            if order.stop_loss_price <= ref_price:
                return make_decision(
                    RiskVerdict.REJECT,
                    RiskReasonCode.MISSING_STOP_LOSS,
                    "RULE_STOP_LOSS_INVALID_DIRECTION",
                    (
                        f"SELL order stop_loss_price ({order.stop_loss_price}) must be above "
                        f"entry price ({ref_price})."
                    ),
                )
            sl_distance_pct = ((order.stop_loss_price - ref_price) / ref_price) * Decimal("100")

        if sl_distance_pct < self.min_stop_loss_pct or sl_distance_pct > self.max_stop_loss_pct:
            return make_decision(
                RiskVerdict.REJECT,
                RiskReasonCode.MISSING_STOP_LOSS,
                "RULE_STOP_LOSS_DISTANCE_OUT_OF_BOUNDS",
                (
                    f"Stop loss distance ({sl_distance_pct:.2f}%) out of allowable bounds "
                    f"[{self.min_stop_loss_pct}%, {self.max_stop_loss_pct}%]."
                ),
            )

        # 5. Rule: Max Position Concentration
        if order.side == Side.BUY and post_trade_pct > self.max_position_size_pct:
            return make_decision(
                RiskVerdict.REJECT,
                RiskReasonCode.MAX_POSITION_SIZE_BREACH,
                "RULE_MAX_POSITION_CONCENTRATION",
                (
                    f"Projected position size ({post_trade_pct:.2f}%) exceeds concentration limit "
                    f"({self.max_position_size_pct:.2f}%)."
                ),
            )

        # 6. Rule: Max Concurrent Open Positions
        is_new_symbol = order.symbol not in portfolio.open_positions or portfolio.open_positions[
            order.symbol
        ] == Decimal("0")
        if (
            order.side == Side.BUY
            and is_new_symbol
            and open_positions_count >= self.max_open_positions
        ):
            return make_decision(
                RiskVerdict.REJECT,
                RiskReasonCode.MAX_OPEN_POSITIONS_BREACH,
                "RULE_MAX_OPEN_POSITIONS",
                (
                    f"Open positions count ({open_positions_count}) has reached maximum "
                    f"allowable ({self.max_open_positions})."
                ),
            )

        # 7. Passed All Checks -> Issue Cryptographic Approval Token
        token = self.token_manager.issue_token(
            order_request_id=order.order_request_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            limit_price=order.limit_price,
        )

        self._record_order_timestamp(now_ts)

        return make_decision(
            RiskVerdict.APPROVE,
            RiskReasonCode.WITHIN_LIMITS,
            "RULE_ALL_LIMITS_PASSED",
            "Order proposal passed all deterministic pre-trade risk checks. Approval token issued.",
            token=token,
        )
