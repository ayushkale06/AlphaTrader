"""Realistic paper matching engine with spread-crossing and volume slippage.

Simulates execution for cash equity orders (BUY and SELL):
- Validates order proposals against current market prices and order types (MARKET vs LIMIT).
- Half-spread crossing: BUY fills at (LTP + half_spread), SELL fills at (LTP - half_spread).
- Volume/impact slippage: additional slippage basis points proportional to order size.
- Exact price bounds for LIMIT orders:
  * BUY LIMIT: executable only if effective fill price <= limit_price.
  * SELL LIMIT: executable only if effective fill price >= limit_price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from src.common.schemas.base import UtcDatetime
from src.common.schemas.orders import Fill, OrderType, Side
from src.mcp_servers.paper_execution_broker.costs import StatutoryCostEngine


@dataclass(frozen=True)
class MatchResult:
    """Result of paper order matching simulation."""

    is_filled: bool
    fill: Fill | None = None
    rejection_reason: str | None = None


class PaperMatchingEngine:
    """Deterministic simulated matching engine for paper execution."""

    def __init__(
        self,
        cost_engine: StatutoryCostEngine | None = None,
        default_half_spread_pct: Decimal = Decimal("0.0005"),  # 5 bps
        slippage_slope_bps_per_1000_shares: Decimal = Decimal("0.5"),  # 0.5 bps per 1000 shares
    ) -> None:
        self.cost_engine = cost_engine or StatutoryCostEngine()
        self.default_half_spread_pct = default_half_spread_pct
        self.slippage_slope = slippage_slope_bps_per_1000_shares

    def match_order(
        self,
        order_id: UUID,
        order_request_id: UUID,
        symbol: str,
        side: Side,
        quantity: Decimal,
        order_type: OrderType,
        current_market_price: Decimal,
        limit_price: Decimal | None,
        filled_at: UtcDatetime,
        is_delivery: bool = True,
    ) -> MatchResult:
        """Attempt to match and fill a paper order."""
        if quantity <= Decimal("0"):
            return MatchResult(is_filled=False, rejection_reason="Quantity must be positive.")
        if current_market_price <= Decimal("0"):
            return MatchResult(
                is_filled=False, rejection_reason="Current market price must be positive."
            )

        # 1. Compute half-spread price shift
        half_spread_cost = current_market_price * self.default_half_spread_pct

        # 2. Compute volume-dependent slippage
        # slippage_bps = base (0) + (quantity / 1000) * slippage_slope
        additional_slippage_bps = ((quantity / Decimal("1000")) * self.slippage_slope).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        slippage_price_shift = current_market_price * (additional_slippage_bps / Decimal("10000"))

        total_shift = half_spread_cost + slippage_price_shift

        # 3. Compute effective execution price
        if side == Side.BUY:
            raw_fill_price = current_market_price + total_shift
        else:
            raw_fill_price = current_market_price - total_shift

        fill_price = raw_fill_price.quantize(Decimal("0.05"), rounding=ROUND_HALF_UP)
        if fill_price <= Decimal("0"):
            fill_price = Decimal("0.05")

        # 4. Check Limit Order price constraint
        if order_type == OrderType.LIMIT:
            if limit_price is None or limit_price <= Decimal("0"):
                return MatchResult(
                    is_filled=False,
                    rejection_reason="LIMIT order requires a positive limit_price.",
                )
            if side == Side.BUY and fill_price > limit_price:
                return MatchResult(
                    is_filled=False,
                    rejection_reason=(
                        f"BUY LIMIT price breach: simulated fill price {fill_price} "
                        f"exceeds limit_price {limit_price}."
                    ),
                )
            if side == Side.SELL and fill_price < limit_price:
                return MatchResult(
                    is_filled=False,
                    rejection_reason=(
                        f"SELL LIMIT price breach: simulated fill price {fill_price} "
                        f"is below limit_price {limit_price}."
                    ),
                )

        # 5. Compute statutory fees and taxes
        cost_breakdown = self.cost_engine.calculate_statutory_costs(
            side=side,
            quantity=quantity,
            price=fill_price,
            is_delivery=is_delivery,
        )

        # Compute effective slippage in bps
        price_diff = abs(fill_price - current_market_price)
        total_slippage_bps = ((price_diff / current_market_price) * Decimal("10000")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # 6. Construct Fill schema model
        fill = Fill(
            fill_id=uuid4(),
            order_id=order_id,
            order_request_id=order_request_id,
            symbol=symbol,
            side=side,
            fill_price=fill_price,
            fill_quantity=quantity,
            slippage_bps=total_slippage_bps,
            stt_tax=cost_breakdown.stt_tax,
            exchange_fees=cost_breakdown.exchange_fees,
            gst_tax=cost_breakdown.gst_tax,
            stamp_duty=cost_breakdown.stamp_duty,
            total_fees=cost_breakdown.total_fees,
            filled_at=filled_at,
            trading_mode="PAPER",
        )

        return MatchResult(is_filled=True, fill=fill)
