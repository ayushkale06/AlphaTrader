"""Indicator value results and base protocol for incremental technical indicators.

All indicators accept Decimal-safe Candle instances and yield Decimal outputs
at the public boundary. Internal calculations use double-precision floats where
documented for numerical performance, and values are rounded back to Decimal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.common.schemas.market import Candle


@dataclass(frozen=True, slots=True)
class IndicatorValue:
    """Base class for indicator results."""

    is_ready: bool


@dataclass(frozen=True, slots=True)
class SingleValue(IndicatorValue):
    """Single numeric indicator result."""

    value: Decimal | None


@dataclass(frozen=True, slots=True)
class BollingerBandsValue(IndicatorValue):
    """Bollinger Bands result."""

    upper: Decimal | None
    middle: Decimal | None
    lower: Decimal | None
    bandwidth: Decimal | None
    percent_b: Decimal | None


@dataclass(frozen=True, slots=True)
class MACDValue(IndicatorValue):
    """MACD indicator result."""

    macd_line: Decimal | None
    signal_line: Decimal | None
    histogram: Decimal | None


@dataclass(frozen=True, slots=True)
class ADXValue(IndicatorValue):
    """ADX indicator result with directional indices."""

    adx: Decimal | None
    plus_di: Decimal | None
    minus_di: Decimal | None


@dataclass(frozen=True, slots=True)
class StochasticValue(IndicatorValue):
    """Stochastic Oscillator result (%K and %D)."""

    k: Decimal | None
    d: Decimal | None


class Indicator(ABC):
    """Abstract base class for streaming O(1) indicators."""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Return True if sufficient warmup bars have been observed."""

    @abstractmethod
    def update(self, candle: Candle) -> IndicatorValue:
        """Incrementally update indicator state with a new final candle."""

    @abstractmethod
    def reset(self) -> None:
        """Reset indicator state."""

    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Serialize internal state for checkpointing."""

    @abstractmethod
    def restore(self, state: dict[str, Any]) -> None:
        """Restore internal state from checkpoint."""
