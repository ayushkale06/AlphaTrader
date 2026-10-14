"""Indian equity transaction cost model with Decimal arithmetic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from src.common.schemas.orders import Side

DEFAULT_COSTS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "indian_equity_costs.json"
)


@dataclass(frozen=True)
class TradeCostBreakdown:
    """Exact breakdown of regulatory and broker transaction costs in INR."""

    turnover: Decimal
    brokerage: Decimal
    stt: Decimal
    exchange_turnover_fee: Decimal
    sebi_turnover_fee: Decimal
    stamp_duty: Decimal
    gst: Decimal
    slippage: Decimal
    total_friction: Decimal


class IndianEquityCostModel:
    """Computes statutory, regulatory, and exchange transaction friction for Indian equities.

    Features:
    - Brokerage: min(rate * turnover, max_per_order)
    - STT (Securities Transaction Tax): delivery vs intraday rates
    - Exchange Turnover Charges: standard NSE cash equity rate
    - SEBI Turnover Charges: INR 10 per crore (0.000001)
    - Stamp Duty: levied on BUY orders only
    - GST: 18% levied on (brokerage + exchange turnover fee + sebi turnover fee)
    - Slippage: dynamic half-spread execution cost
    All arithmetic uses Decimal with exact precision.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or DEFAULT_COSTS_PATH
        self.config = self._load_config()

        self.brokerage_rate = Decimal(str(self.config.get("brokerage_rate_pct", "0.0003")))
        self.brokerage_max = Decimal(str(self.config.get("brokerage_max_per_order", "20.00")))
        self.stt_delivery_rate = Decimal(str(self.config.get("stt_delivery_rate_pct", "0.001")))
        self.stt_intraday_sell_rate = Decimal(
            str(self.config.get("stt_intraday_sell_rate_pct", "0.00025"))
        )
        self.exchange_fee_rate = Decimal(
            str(self.config.get("exchange_turnover_fee_pct", "0.0000325"))
        )
        self.sebi_fee_rate = Decimal(str(self.config.get("sebi_turnover_fee_pct", "0.000001")))
        self.stamp_duty_rate = Decimal(str(self.config.get("stamp_duty_buy_rate_pct", "0.00015")))
        self.gst_rate = Decimal(str(self.config.get("gst_rate_pct", "0.18")))
        self.slippage_half_spread = Decimal(
            str(self.config.get("default_slippage_half_spread_pct", "0.0005"))
        )

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, encoding="utf-8") as f:
            data: Any = json.load(f)
            return dict(data) if isinstance(data, dict) else {}

    def compute_costs(
        self,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        is_delivery: bool = True,
    ) -> TradeCostBreakdown:
        """Calculate exact friction for an executed order."""
        turnover = (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Brokerage: e.g. min(0.03% of turnover, 20 INR)
        raw_brokerage = turnover * self.brokerage_rate
        brokerage = min(raw_brokerage, self.brokerage_max).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # STT (Securities Transaction Tax)
        if is_delivery:
            # Delivery: 0.1% on both Buy and Sell
            stt = (turnover * self.stt_delivery_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            # Intraday: 0.025% on Sell only
            stt = (
                (turnover * self.stt_intraday_sell_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if side == Side.SELL
                else Decimal("0.00")
            )

        # Exchange Turnover Charges
        exchange_fee = (turnover * self.exchange_fee_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # SEBI Turnover Charges
        sebi_fee = (turnover * self.sebi_fee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Stamp Duty: BUY side only (0.015%)
        stamp_duty = (
            (turnover * self.stamp_duty_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if side == Side.BUY
            else Decimal("0.00")
        )

        # GST: 18% on (brokerage + exchange fee + sebi fee)
        taxable_services = brokerage + exchange_fee + sebi_fee
        gst = (taxable_services * self.gst_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Bid-ask slippage impact
        slippage = (turnover * self.slippage_half_spread).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        total_friction = (
            brokerage + stt + exchange_fee + sebi_fee + stamp_duty + gst + slippage
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return TradeCostBreakdown(
            turnover=turnover,
            brokerage=brokerage,
            stt=stt,
            exchange_turnover_fee=exchange_fee,
            sebi_turnover_fee=sebi_fee,
            stamp_duty=stamp_duty,
            gst=gst,
            slippage=slippage,
            total_friction=total_friction,
        )
