"""Deterministic multi-factor alpha scoring and conflict resolution engine.

Combines:
1. Trend / Momentum factor (from EMAs, MACD)
2. Mean Reversion factor (from RSI, Bollinger %B)
3. Volatility / Breakout factor (from Bollinger Bandwidth, ADX)

Mathematical Formulation:
Each factor computes a signed score in [-1.0, +1.0]:
- Positive indicates bullish impulse
- Negative indicates bearish pressure
- Zero indicates neutral or warming up

Aggregate Composite Score:
  Composite = (w_trend * S_trend) + (w_reversion * S_reversion) + (w_breakout * S_breakout)
Where sum(weights) = 1.0.

Decision Rule (Long-Only Cash Delivery):
- If Composite >= +0.30: Emit LONG
- Else: Emit FLAT (no shorts permitted in Indian cash equity delivery)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.common.schemas.market import Candle
from src.common.schemas.signals import Direction, TechnicalSignal


@dataclass(frozen=True)
class FactorEvaluationResult:
    """Detailed analytical evaluation of multi-factor signals."""

    symbol: str
    direction: Direction
    composite_score: Decimal
    factor_scores: dict[str, Decimal]
    factor_fired: dict[str, bool]
    contributions: dict[str, str]
    conflict_resolution: str
    reasons: list[str] = field(default_factory=list)


class MultiFactorEvaluator:
    """Evaluates multi-factor alpha models deterministically."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.w_trend = Decimal(str(cfg.get("weight_trend", "0.40")))
        self.w_reversion = Decimal(str(cfg.get("weight_reversion", "0.30")))
        self.w_breakout = Decimal(str(cfg.get("weight_breakout", "0.30")))
        self.long_threshold = Decimal(str(cfg.get("composite_long_threshold", "0.25")))

    def evaluate_factors(
        self,
        candle: Candle,
        indicators: dict[str, Decimal],
        technical_signal: TechnicalSignal | None = None,
    ) -> FactorEvaluationResult:
        """Deterministically evaluate factors and combine them."""
        factor_scores: dict[str, Decimal] = {}
        factor_fired: dict[str, bool] = {}
        contributions: dict[str, str] = {}
        reasons: list[str] = []

        close = candle.close
        ema_9 = indicators.get("ema_9")
        ema_21 = indicators.get("ema_21")
        ema_50 = indicators.get("ema_50")
        rsi_14 = indicators.get("rsi_14")
        pct_b = indicators.get("bb_percent_b")
        bandwidth = indicators.get("bb_bandwidth")
        adx = indicators.get("adx")

        # 1. Trend Factor
        if ema_9 is not None and ema_21 is not None and ema_50 is not None:
            if ema_9 > ema_21 and close > ema_50:
                s_trend = Decimal("1.0")
                contributions["trend"] = "Strong Bullish Momentum (EMA 9 > 21 and Close > 50)"
                factor_fired["trend"] = True
            elif ema_9 < ema_21 and close < ema_50:
                s_trend = Decimal("-1.0")
                contributions["trend"] = "Strong Bearish Momentum (EMA 9 < 21 and Close < 50)"
                factor_fired["trend"] = True
            else:
                s_trend = Decimal("0.0")
                contributions["trend"] = "Mixed Trend Signals"
                factor_fired["trend"] = False
        else:
            s_trend = Decimal("0.0")
            contributions["trend"] = "Trend Factor Warming Up"
            factor_fired["trend"] = False
        factor_scores["trend"] = s_trend

        # 2. Mean Reversion Factor
        if rsi_14 is not None and pct_b is not None:
            if pct_b <= Decimal("0.20") and rsi_14 <= Decimal("35"):
                s_rev = Decimal("1.0")
                contributions["mean_reversion"] = (
                    "Oversold Reversion Potential (%B <= 0.20, RSI <= 35)"
                )
                factor_fired["mean_reversion"] = True
            elif pct_b >= Decimal("0.80") and rsi_14 >= Decimal("65"):
                s_rev = Decimal("-1.0")
                contributions["mean_reversion"] = "Overbought Exhaustion (%B >= 0.80, RSI >= 65)"
                factor_fired["mean_reversion"] = True
            else:
                s_rev = Decimal("0.0")
                contributions["mean_reversion"] = "Neutral Mean Reversion Range"
                factor_fired["mean_reversion"] = False
        else:
            s_rev = Decimal("0.0")
            contributions["mean_reversion"] = "Mean Reversion Factor Warming Up"
            factor_fired["mean_reversion"] = False
        factor_scores["mean_reversion"] = s_rev

        # 3. Volatility / Breakout Factor
        if pct_b is not None and bandwidth is not None and adx is not None:
            if pct_b >= Decimal("0.85") and bandwidth >= Decimal("0.04") and adx >= Decimal("25"):
                s_break = Decimal("1.0")
                contributions["breakout"] = (
                    "Volatility Expansion Breakout (High %B, Bandwidth, ADX)"
                )
                factor_fired["breakout"] = True
            elif pct_b <= Decimal("0.15") and bandwidth >= Decimal("0.04") and adx >= Decimal("25"):
                s_break = Decimal("-1.0")
                contributions["breakout"] = "Downside Volatility Breakdown"
                factor_fired["breakout"] = True
            else:
                s_break = Decimal("0.0")
                contributions["breakout"] = "No Volatility Breakout Triggered"
                factor_fired["breakout"] = False
        else:
            s_break = Decimal("0.0")
            contributions["breakout"] = "Volatility Breakout Factor Warming Up"
            factor_fired["breakout"] = False
        factor_scores["breakout"] = s_break

        # Compute Composite
        composite = (
            (self.w_trend * s_trend) + (self.w_reversion * s_rev) + (self.w_breakout * s_break)
        ).quantize(Decimal("0.0001"))

        # Conflict resolution explanation
        if s_trend > Decimal("0") and s_rev < Decimal("0"):
            conflict_res = (
                "Trend Bullish conflicted with Mean-Reversion Overbought; weighted netting applied."
            )
            reasons.append(conflict_res)
        elif s_trend < Decimal("0") and s_rev > Decimal("0"):
            conflict_res = (
                "Trend Bearish conflicted with Mean-Reversion Oversold; weighted netting applied."
            )
            reasons.append(conflict_res)
        else:
            conflict_res = "No significant factor conflict detected."

        if composite >= self.long_threshold:
            direction = Direction.LONG
            reasons.append(
                f"Composite score ({composite:.4f}) >= threshold ({self.long_threshold}) -> LONG"
            )
        else:
            direction = Direction.FLAT
            reasons.append(
                f"Composite score ({composite:.4f}) < threshold ({self.long_threshold}) -> FLAT"
            )

        return FactorEvaluationResult(
            symbol=candle.symbol,
            direction=direction,
            composite_score=composite,
            factor_scores=factor_scores,
            factor_fired=factor_fired,
            contributions=contributions,
            conflict_resolution=conflict_res,
            reasons=reasons,
        )
