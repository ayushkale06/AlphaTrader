"""Deterministic position sizer and analytical proposal engine.

Combines:
1. Volatility-targeted ATR stop-loss and take-profit calculation.
2. Fractional-Kelly sizing only when explicit win rate and payoff ratio are available.
3. Long-only cash segment portfolio constraints (target_weight in [0, max_position_weight]).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.common.schemas.signals import Direction


@dataclass(frozen=True)
class SizingResult:
    """Output of analytical position sizing."""

    direction: Direction
    target_weight: Decimal
    stop_loss_price: Decimal | None
    take_profit_price: Decimal | None
    risk_amount_per_share: Decimal | None
    kelly_fraction: Decimal | None
    method: str
    reasons: list[str]


class PositionSizer:
    """Computes risk-controlled position sizing and brackets without broker interaction."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.target_risk_per_trade = Decimal(str(cfg.get("target_risk_per_trade_pct", "0.01")))
        self.atr_sl_mult = Decimal(str(cfg.get("atr_stop_loss_multiplier", "2.0")))
        self.atr_tp_mult = Decimal(str(cfg.get("atr_take_profit_multiplier", "3.0")))
        self.max_position_weight = Decimal(str(cfg.get("max_position_weight", "0.20")))
        self.kelly_cap = Decimal(str(cfg.get("fractional_kelly_cap", "0.25")))

    def calculate_atr_brackets(
        self,
        close_price: Decimal,
        atr: Decimal | None,
        direction: Direction,
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        """Calculate stop-loss and take-profit prices from ATR."""
        if atr is None or atr <= Decimal("0") or close_price <= Decimal("0"):
            return None, None, None

        if direction == Direction.LONG:
            risk_per_share = (atr * self.atr_sl_mult).quantize(Decimal("0.05"))
            stop_loss = max(Decimal("0.05"), close_price - risk_per_share).quantize(Decimal("0.05"))
            take_profit = (close_price + (atr * self.atr_tp_mult)).quantize(Decimal("0.05"))
            return stop_loss, take_profit, risk_per_share

        return None, None, None

    def calculate_fractional_kelly(
        self,
        win_rate: Decimal | None,
        payoff_ratio: Decimal | None,
    ) -> Decimal | None:
        """Calculate fractional Kelly criterion f* = (p * b - q) / b.

        Returns None if inputs are missing, invalid, or produce non-positive edge.
        """
        if win_rate is None or payoff_ratio is None:
            return None
        if not (Decimal("0") < win_rate < Decimal("1")):
            return None
        if payoff_ratio <= Decimal("0"):
            return None

        p = win_rate
        q = Decimal("1") - p
        b = payoff_ratio

        # Full Kelly
        full_kelly = (p * b - q) / b
        if full_kelly <= Decimal("0"):
            return Decimal("0")

        # Fractional Kelly capped by conservative threshold
        frac_kelly = (full_kelly * self.kelly_cap).quantize(Decimal("0.0001"))
        return min(frac_kelly, self.max_position_weight)

    def size_order(
        self,
        close_price: Decimal,
        atr: Decimal | None,
        direction: Direction,
        base_weight: Decimal = Decimal("0.10"),
        win_rate: Decimal | None = None,
        payoff_ratio: Decimal | None = None,
        portfolio_equity: Decimal | None = None,
    ) -> SizingResult:
        """Perform deterministic position sizing proposal.

        Never places an order; returns structured sizing recommendations.
        """
        reasons: list[str] = []

        if direction == Direction.FLAT:
            return SizingResult(
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                stop_loss_price=None,
                take_profit_price=None,
                risk_amount_per_share=None,
                kelly_fraction=None,
                method="EXIT_OR_NEUTRAL",
                reasons=["Direction is FLAT: target_weight = 0"],
            )

        if direction == Direction.SHORT:
            # Enforce cash delivery constraint
            return SizingResult(
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                stop_loss_price=None,
                take_profit_price=None,
                risk_amount_per_share=None,
                kelly_fraction=None,
                method="CASH_DELIVERY_SHORT_PROHIBITED",
                reasons=["SHORT is prohibited for cash delivery; forced to FLAT"],
            )

        # 1. ATR Brackets
        sl_price, tp_price, risk_per_share = self.calculate_atr_brackets(
            close_price, atr, direction
        )
        if sl_price is None:
            reasons.append("ATR unavailable or zero: brackets not established")

        # 2. Sizing determination
        method = "FIXED_VOLATILITY_BOUNDED"
        kelly_frac = self.calculate_fractional_kelly(win_rate, payoff_ratio)

        if kelly_frac is not None and kelly_frac > Decimal("0"):
            method = "FRACTIONAL_KELLY_VOL_TARGET"
            target_w = min(kelly_frac, self.max_position_weight)
            reasons.append(f"Sized via Fractional-Kelly ({target_w:.4f})")
        else:
            if kelly_frac == Decimal("0"):
                reasons.append("Non-positive Kelly expectation; fallback to base weight capped")
            target_w = min(base_weight, self.max_position_weight)
            reasons.append(f"Sized via Base Allocation ({target_w:.4f})")

        return SizingResult(
            direction=Direction.LONG,
            target_weight=target_w.quantize(Decimal("0.0001")),
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            risk_amount_per_share=risk_per_share,
            kelly_fraction=kelly_frac,
            method=method,
            reasons=reasons,
        )
