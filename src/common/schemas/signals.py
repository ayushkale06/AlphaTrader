"""Signal schemas for technical analysis indicators and quantitative strategy proposals.

Schema 1.3.0 changes:
- TechnicalSignal:
  * interval and regime carry no default values.
  * regime is MarketRegime | None (None when regime classifier is unready or disabled).
  * config_version (NonEmptyStr) and config_hash (64 hex characters) required.
  * rules_long and rules_short stored explicitly.
  * rules_fired and rules_against properties return [] for FLAT.
  * Model validator enforces directional counts and exact ROUND_HALF_UP (quantized to 0.01) confidence.
  * provenance is strictly required DataProvenance.
- QuantSignal:
  * confidence removed entirely for 1.3.0.
  * interval (CandleInterval) is required.
  * strategy_version is required NonEmptyStr.
  * direction SHORT forbidden for long-only cash delivery.
  * target_weight > 0 for LONG and == 0 for FLAT.
  * expected_net_edge_bps_per_trade is nullable; requires edge_basis=OUT_OF_SAMPLE and backtest_report_id (64 hex).
  * source_signal_ids (list[UUID]) causality tracking.
  * parameters round-trips DecimalNumber | str | int.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Self
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
from src.common.schemas.market import CandleInterval, DataProvenance

HexSha256 = Annotated[
    str,
    Field(
        pattern=r"^[a-f0-9]{64}$",
        description="64-character lowercase hexadecimal SHA-256 digest.",
    ),
]


class Direction(StrEnum):
    """Directional bias of a technical or quantitative trading signal."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class EdgeBasis(StrEnum):
    """Scientific validation basis of projected strategy edge."""

    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"


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
    interval: CandleInterval
    direction: Direction
    confidence: UnitIntervalDecimal = Field(
        description=(
            "Deterministic rule-agreement ratio quantized to 0.01: "
            "count(rules_in_direction) / (count(rules_long) + count(rules_short)). "
            "Equals Decimal('0.50') on tied FLAT signals."
        ),
    )
    regime: MarketRegime | None = Field(
        default=None,
        description=(
            "Classified volatility/trend regime, or None if regime classifier "
            "is unready or disabled."
        ),
    )
    indicators: dict[str, DecimalNumber] = Field(
        description="Named indicator values (e.g. ema_9, ema_21, rsi_14, atr_14, vwap).",
    )
    rules_long: list[str] = Field(
        default_factory=list,
        description="Deterministic rule names that fired for LONG direction.",
    )
    rules_short: list[str] = Field(
        default_factory=list,
        description="Deterministic rule names that fired for SHORT direction.",
    )
    config_version: NonEmptyStr = Field(
        description="Human-readable configuration semantic version string (e.g. '1.0.0').",
    )
    config_hash: HexSha256 = Field(
        description="SHA-256 (64 hex characters) of the active technical rules configuration.",
    )
    provenance: DataProvenance | None = Field(
        default=None,
        description=(
            "Propagated input data provenance holding provider, feed_mode, "
            "is_delayed, delay_seconds, and ingested_at. Required for schema >= 1.2.0."
        ),
    )

    @property
    def rules_fired(self) -> list[str]:
        """Winning direction rules (empty for FLAT)."""
        if self.direction == Direction.LONG:
            return self.rules_long
        if self.direction == Direction.SHORT:
            return self.rules_short
        return []

    @property
    def rules_against(self) -> list[str]:
        """Opposing direction rules (empty for FLAT)."""
        if self.direction == Direction.LONG:
            return self.rules_short
        if self.direction == Direction.SHORT:
            return self.rules_long
        return []

    @field_validator("indicators")
    @classmethod
    def validate_non_empty_indicators(
        cls, value: dict[str, DecimalNumber]
    ) -> dict[str, DecimalNumber]:
        if not value:
            msg = "TechnicalSignal.indicators cannot be empty."
            raise ValueError(msg)
        for key in value:
            if not key or not key.strip():
                msg = "Indicator names in TechnicalSignal.indicators must be non-empty strings."
                raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_directional_rules_and_confidence(self) -> Self:
        n_long = len(self.rules_long)
        n_short = len(self.rules_short)
        total = n_long + n_short

        if total == 0:
            msg = "TechnicalSignal must have at least one rule fired in rules_long or rules_short."
            raise ValueError(msg)

        if self.direction == Direction.LONG:
            if n_long <= n_short:
                msg = (
                    f"Direction.LONG requires len(rules_long) > len(rules_short), "
                    f"got {n_long} vs {n_short}."
                )
                raise ValueError(msg)
            expected_conf = (Decimal(n_long) / Decimal(total)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        elif self.direction == Direction.SHORT:
            if n_short <= n_long:
                msg = (
                    f"Direction.SHORT requires len(rules_short) > len(rules_long), "
                    f"got {n_short} vs {n_long}."
                )
                raise ValueError(msg)
            expected_conf = (Decimal(n_short) / Decimal(total)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:  # FLAT
            if n_long != n_short:
                msg = (
                    f"Direction.FLAT requires a tie (len(rules_long) == len(rules_short)), "
                    f"got {n_long} vs {n_short}."
                )
                raise ValueError(msg)
            expected_conf = Decimal("0.50")

        if self.confidence != expected_conf:
            msg = (
                f"TechnicalSignal confidence mismatch: got {self.confidence}, "
                f"expected {expected_conf} (quantized 0.01 ROUND_HALF_UP) from rule counts "
                f"({n_long} long vs {n_short} short)."
            )
            raise ValueError(msg)

        return self


class QuantSignal(AlphaTraderBaseModel):
    """Output of MCP Server 3 (Quant Strategy Engine) and the Quant Agent."""

    signal_id: UUID = Field(default_factory=uuid.uuid4)
    symbol: NseSymbol
    evaluated_at: UtcDatetime
    interval: CandleInterval
    strategy_name: NonEmptyStr
    strategy_version: NonEmptyStr = Field(
        description="Required semantic version of the strategy logic (e.g. '1.0.0').",
    )
    direction: Direction
    target_weight: UnitIntervalDecimal = Field(
        description="Recommended portfolio target weight in [0, 1] for long-only cash delivery.",
    )
    expected_net_edge_bps_per_trade: DecimalNumber | None = Field(
        default=None,
        description=(
            "Projected net edge in basis points per trade after statutory fees/slippage. "
            "Must be None unless backed by an out-of-sample backtest report."
        ),
    )
    edge_basis: EdgeBasis | None = Field(
        default=None,
        description="Scientific validation basis; MUST be OUT_OF_SAMPLE when edge is claimed.",
    )
    backtest_report_id: HexSha256 | None = Field(
        default=None,
        description="SHA-256 hash (64 hex characters) of the committed out-of-sample backtest report file.",
    )
    source_signal_ids: list[UUID] = Field(
        default_factory=list,
        description="Causation lineage linking upstream signal UUIDs that fed this strategy.",
    )
    config_hash: HexSha256 = Field(
        description="SHA-256 (64 hex characters) of the strategy configuration and hyperparameters.",
    )
    provenance: DataProvenance = Field(
        description=(
            "Propagated input data provenance holding provider, feed_mode, "
            "is_delayed, delay_seconds, and ingested_at."
        ),
    )
    parameters: dict[str, DecimalNumber | str | int] = Field(
        default_factory=dict,
        description="Strategy hyperparameters used for this evaluation.",
    )

    @model_validator(mode="after")
    def validate_quant_signal_invariants(self) -> Self:
        # Long-only Cash Segment Rule: SHORT is strictly forbidden in 1.3.0
        if self.direction == Direction.SHORT:
            msg = (
                "QuantSignal direction=SHORT is prohibited for long-only cash delivery. "
                "Use FLAT with target_weight=0 to exit positions."
            )
            raise ValueError(msg)

        # Target weight gating
        if self.direction == Direction.FLAT and self.target_weight != Decimal("0"):
            msg = f"QuantSignal with direction=FLAT must have target_weight=0, got {self.target_weight}."
            raise ValueError(msg)
        if self.direction == Direction.LONG and self.target_weight <= Decimal("0"):
            msg = f"QuantSignal with direction=LONG requires target_weight > 0, got {self.target_weight}."
            raise ValueError(msg)

        # Edge gating: cannot claim edge without OUT_OF_SAMPLE basis and SHA-256 report hash
        if self.expected_net_edge_bps_per_trade is not None:
            if self.edge_basis != EdgeBasis.OUT_OF_SAMPLE:
                msg = (
                    "QuantSignal with expected_net_edge_bps_per_trade requires "
                    f"edge_basis=EdgeBasis.OUT_OF_SAMPLE, got {self.edge_basis}."
                )
                raise ValueError(msg)
            if not self.backtest_report_id:
                msg = (
                    "QuantSignal claiming expected_net_edge_bps_per_trade requires a valid "
                    "backtest_report_id (SHA-256 hash of committed out-of-sample report)."
                )
                raise ValueError(msg)
        elif self.edge_basis is not None or self.backtest_report_id is not None:
            msg = (
                "QuantSignal cannot specify edge_basis or backtest_report_id when "
                "expected_net_edge_bps_per_trade is None."
            )
            raise ValueError(msg)

        return self
