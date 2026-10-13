"""Abstract base interface for asynchronous market data feed providers (ADR-005)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any

from src.common.schemas.market import FeedMode, Tick


class MarketDataProvider(ABC):
    """Vendor-agnostic async interface for market data ingestion.

    Guarantees that downstream normalizers, candle aggregators, and Kafka publishers
    never couple directly to a single broker, exchange gateway, or data vendor.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Vendor or generator identifier (e.g. 'simulated_feed', 'yahoo_finance')."""

    @property
    @abstractmethod
    def feed_mode(self) -> FeedMode:
        """Feed execution regime: SIMULATED, REPLAY, or LIVE."""

    @property
    @abstractmethod
    def is_delayed(self) -> bool:
        """True if the quotes provided by this feed are delayed."""

    @property
    @abstractmethod
    def delay_seconds(self) -> int | None:
        """Dissemination delay in seconds (0 for real-time/sim, None if delayed but unknown)."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection or session with the market data source."""

    @abstractmethod
    async def subscribe(self, symbols: Sequence[str]) -> None:
        """Subscribe to a set of NIFTY 50 constituent symbols."""

    @abstractmethod
    def stream_quotes(self) -> AsyncIterator[dict[str, Any] | Tick]:
        """Yield an asynchronous stream of raw quote dicts or pre-formed Ticks."""

    @abstractmethod
    async def close(self) -> None:
        """Gracefully terminate feed connections and release resources."""
