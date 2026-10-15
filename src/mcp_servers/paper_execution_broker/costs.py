"""Exact Indian statutory transaction tax and fee calculations for paper execution.

Evaluates:
- STT (Securities Transaction Tax):
    * Delivery: 0.10% (0.001) on BUY and SELL.
    * Intraday: 0.025% (0.00025) on SELL only.
- Exchange Turnover Fees: 0.00325% (0.0000325) on total turnover.
- SEBI Regulatory Fees: INR 10 per crore (0.000001) on total turnover.
- Stamp Duty: 0.015% (0.00015) on BUY side only.
- GST: 18% (0.18) on (Exchange Turnover Fees + SEBI Fees).
- Total Fees: stt_tax + exchange_fees + gst_tax + stamp_duty.

Strictly non-float Decimal arithmetic with ROUND_HALF_UP quantization to 2 decimal places.
Guarantees Fill.total_fees invariant compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from src.common.schemas.orders import Side


@dataclass(frozen=True)
class PaperExecutionCostBreakdown:
    """Breakdown of statutory costs matching Fill schema requirements."""

    turnover: Decimal
    stt_tax: Decimal
    exchange_fees: Decimal
    gst_tax: Decimal
    stamp_duty: Decimal
    total_fees: Decimal


class StatutoryCostEngine:
    """Deterministic statutory tax and exchange charges engine for Indian equities."""

    # Rates
    STT_DELIVERY_RATE = Decimal("0.001")
    STT_INTRADAY_SELL_RATE = Decimal("0.00025")
    EXCHANGE_TURNOVER_FEE_RATE = Decimal("0.0000325")
    SEBI_TURNOVER_FEE_RATE = Decimal("0.000001")
    STAMP_DUTY_BUY_RATE = Decimal("0.00015")
    GST_RATE = Decimal("0.18")

    def calculate_statutory_costs(
        self,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        is_delivery: bool = True,
    ) -> PaperExecutionCostBreakdown:
        """Compute exact statutory tax breakdown for an execution fill.

        All intermediate and terminal values are quantized to 2 decimal places (ROUND_HALF_UP).
        """
        turnover = (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 1. STT
        if is_delivery:
            stt = (turnover * self.STT_DELIVERY_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            stt = (
                (turnover * self.STT_INTRADAY_SELL_RATE).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if side == Side.SELL
                else Decimal("0.00")
            )

        # 2. Exchange & SEBI Turnover Charges
        exchange_base = (turnover * self.EXCHANGE_TURNOVER_FEE_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        sebi_base = (turnover * self.SEBI_TURNOVER_FEE_RATE).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        exchange_fees = (exchange_base + sebi_base).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # 3. Stamp Duty (BUY side only)
        stamp_duty = (
            (turnover * self.STAMP_DUTY_BUY_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if side == Side.BUY
            else Decimal("0.00")
        )

        # 4. GST: 18% on regulatory & exchange turnover fees
        gst_tax = (exchange_fees * self.GST_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 5. Total Fees: sum of the four components matching Fill model invariant
        total_fees = (stt + exchange_fees + gst_tax + stamp_duty).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        return PaperExecutionCostBreakdown(
            turnover=turnover,
            stt_tax=stt,
            exchange_fees=exchange_fees,
            gst_tax=gst_tax,
            stamp_duty=stamp_duty,
            total_fees=total_fees,
        )
