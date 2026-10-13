"""Signal schemas for technical analysis indicators and quantitative strategy proposals."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    DecimalNumber,
    NonEmptyStr,
    NseSymbol,
    UnitIntervalDecimal,
    UtcDatetime,
)
from src.common.schemas.market import CandleInterval


class Direction(StrEnum):
    """Directional bias of a technical or quantitative trading signal."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class MarketRegime(StrEnum):
    """Classified market volatility and trend regime for an NSE instrument."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    MEAN_REVERTING = "MEAN_REVERTING"
    HIGH_VOL_CHOP = "HIGH_VOL_CHOP"


class TechnicalSignal(AlphaTraderBaseModel):
    """Output of MCP Server 2 (Technical Analyst) and the Analyst Agent."""

    signal_id: UUID = Field(default_factory=uuid.uuid4)
    symbol: NseSymbol
    evaluated_at: UtcDatetime
    interval: CandleInterval = CandleInterval.FIVE_MINUTES
    direction: Direction
    confidence: UnitIntervalDecimal = Field(
        description="Deterministic signal confidence score bounded in [0, 1].",
    )
    regime: MarketRegime = MarketRegime.TRENDING_UP
    indicators: dict[str, DecimalNumber] = Field(
        description="Named indicator values (e.g. ema_9, ema_21, rsi_14, atr_14, vwap).",
    )

    @field_validator("indicators")
    @classmethod
    def validate_non_empty_indicators(
        cls, value: dict[str, DecimalNumber]
    ) -> dict[str, DecimalNumber]:
        """Require at least one computed indicator value."""
        if not value:
            msg = "TechnicalSignal.indicators cannot be empty."
            raise ValueError(msg)
        for key in value:
            if not key or not key.strip():
                msg = "Indicator names in TechnicalSignal.indicators must be non-empty strings."
                raise ValueError(msg)
        return value


class QuantSignal(AlphaTraderBaseModel):
    """Output of MCP Server 3 (Quant Strategy Engine) and the Quant Agent."""

    signal_id: UUID = Field(default_factory=uuid.uuid4)
    symbol: NseSymbol
    evaluated_at: UtcDatetime
    strategy_name: NonEmptyStr
    direction: Direction
    target_weight: UnitIntervalDecimal = Field(
        description="Recommended portfolio weight in [0, 1] (e.g. fractional-Kelly output).",
    )
    expected_edge: DecimalNumber = Field(
        description="Projected expected edge in basis points or return units (Decimal).",
    )
    confidence: UnitIntervalDecimal = Field(
        default=Decimal("0.50"),
        description="Model confidence in [0, 1].",
    )
    parameters: dict[str, DecimalNumber | str | int] = Field(
        default_factory=dict,
        description="Strategy hyperparameters used for this evaluation.",
    )

    @model_validator(mode="after")
    def validate_flat_direction_weight(self) -> Self:
        """Ensure FLAT signals always carry a zero target_weight."""
        if self.direction == Direction.FLAT and self.target_weight != Decimal("0"):
            msg = (
                f"QuantSignal with direction=FLAT must have target_weight=0, "
                f"got {self.target_weight}."
            )
            raise ValueError(msg)
        return self
