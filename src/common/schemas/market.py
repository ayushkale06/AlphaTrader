"""Market data schemas for NSE NIFTY 50 ticks, OHLCV candles, and exchange session status."""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    NonEmptyStr,
    NonNegativeQuantity,
    NseSymbol,
    PositivePrice,
    UtcDatetime,
    validate_utc_datetime,
)

NSE_TIMEZONE = ZoneInfo("Asia/Kolkata")
NSE_PRE_OPEN_START = time(9, 0)
NSE_MARKET_OPEN = time(9, 15)
NSE_MARKET_CLOSE = time(15, 30)
NSE_POST_CLOSE_END = time(16, 0)


class FeedMode(StrEnum):
    """Canonical market data feed modes across configuration and payload contracts."""

    SIMULATED = "SIMULATED"
    REPLAY = "REPLAY"
    LIVE = "LIVE"


class DataProvenance(AlphaTraderBaseModel):
    """Explicit vendor lineage, feed regime, and dissemination delay metadata (ADR-005)."""

    provider: NonEmptyStr = Field(
        description="Vendor/source name (e.g. 'simulated_feed', 'replay_feed', 'yahoo_finance').",
    )
    feed_mode: FeedMode = Field(
        description="Origin regime: SIMULATED (walk), REPLAY (history), or LIVE (market feed).",
    )
    is_delayed: bool = Field(
        default=False,
        description="True if the market quote is subject to an exchange dissemination delay.",
    )
    delay_seconds: int | None = Field(
        default=0,
        ge=0,
        description="Delay in seconds (0 for real-time/sim, None if delayed but unknown).",
    )
    ingested_at: UtcDatetime | None = Field(
        default=None,
        description="UTC timestamp when the quote was received at the ingestion boundary.",
    )

    @model_validator(mode="after")
    def validate_delay_invariants(self) -> Self:
        """Validate delay invariants per Sprint 4 specifications.

        Invariants:
        1. SIMULATED can never be delayed (is_delayed=False, delay_seconds=0).
        2. is_delayed=False requires delay_seconds in (0, None).
        3. delay_seconds > 0 requires is_delayed=True.
        4. delay_seconds=None is only allowed when is_delayed=True (unknown positive delay).
        """
        if self.feed_mode == FeedMode.SIMULATED and self.is_delayed:
            raise ValueError("SIMULATED market data cannot be delayed.")
        if not self.is_delayed:
            if self.delay_seconds not in (0, None):
                msg = (
                    f"is_delayed=False requires delay_seconds in (0, None), "
                    f"got {self.delay_seconds}."
                )
                raise ValueError(msg)
        else:
            # is_delayed is True
            if self.delay_seconds == 0:
                raise ValueError("is_delayed=True requires delay_seconds > 0 or None.")
        if self.delay_seconds is not None and self.delay_seconds > 0 and not self.is_delayed:
            raise ValueError("delay_seconds > 0 requires is_delayed=True.")
        if self.delay_seconds is None and not self.is_delayed:
            raise ValueError("delay_seconds=None is only allowed when is_delayed=True.")
        return self


class CandleInterval(StrEnum):
    """Supported OHLCV bar aggregation intervals."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


class MarketSessionPhase(StrEnum):
    """NSE equity market session phases in Asia/Kolkata (IST) hours."""

    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    POST_CLOSE = "POST_CLOSE"
    CLOSED = "CLOSED"


class Tick(AlphaTraderBaseModel):
    """Normalized real-time or replayed NSE NIFTY 50 quote/trade tick."""

    symbol: NseSymbol
    timestamp: UtcDatetime
    last_price: PositivePrice
    volume: NonNegativeQuantity
    bid_price: PositivePrice | None = None
    ask_price: PositivePrice | None = None
    sequence_number: int = Field(
        default=1,
        ge=1,
        description="Monotonically increasing feed sequence number.",
    )
    provenance: DataProvenance | None = Field(
        default=None,
        description="Data provenance and delay metadata. Required for schema >= 1.1.0.",
    )

    @model_validator(mode="after")
    def validate_bid_ask_spread(self) -> Self:
        """Ensure ask_price is never strictly less than bid_price when both are present."""
        if (
            self.bid_price is not None
            and self.ask_price is not None
            and self.ask_price < self.bid_price
        ):
            msg = (
                f"Inverted bid/ask quote for {self.symbol}: "
                f"ask_price ({self.ask_price}) < bid_price ({self.bid_price})."
            )
            raise ValueError(msg)
        return self


class Candle(AlphaTraderBaseModel):
    """Normalized OHLCV candlestick bar for an NSE NIFTY 50 symbol."""

    symbol: NseSymbol
    interval: CandleInterval
    open_time: UtcDatetime
    close_time: UtcDatetime
    open: PositivePrice
    high: PositivePrice
    low: PositivePrice
    close: PositivePrice
    volume: NonNegativeQuantity
    vwap: PositivePrice | None = None
    trade_count: int = Field(default=0, ge=0)
    provenance: DataProvenance | None = Field(
        default=None,
        description="Data provenance and delay metadata. Required for schema >= 1.1.0.",
    )

    @model_validator(mode="after")
    def validate_ohlc_and_time_invariants(self) -> Self:
        """Enforce close_time > open_time and high/low envelope bounds."""
        if self.close_time <= self.open_time:
            msg = (
                f"Invalid candle window for {self.symbol}: "
                f"close_time ({self.close_time}) must be > open_time ({self.open_time})."
            )
            raise ValueError(msg)

        highest_endpoint = max(self.open, self.close, self.low)
        lowest_endpoint = min(self.open, self.close, self.high)

        if self.high < highest_endpoint:
            msg = (
                f"Invalid OHLC bar for {self.symbol}: "
                f"high ({self.high}) must be >= max(open, close, low) ({highest_endpoint})."
            )
            raise ValueError(msg)
        if self.low > lowest_endpoint:
            msg = (
                f"Invalid OHLC bar for {self.symbol}: "
                f"low ({self.low}) must be <= min(open, close, high) ({lowest_endpoint})."
            )
            raise ValueError(msg)
        if self.vwap is not None and not (self.low <= self.vwap <= self.high):
            msg = (
                f"Invalid VWAP for {self.symbol}: "
                f"vwap ({self.vwap}) must lie within [low ({self.low}), high ({self.high})]."
            )
            raise ValueError(msg)
        return self


def classify_nse_session(timestamp: datetime) -> MarketSessionPhase:
    """Classify a UTC-aware timestamp into the corresponding NSE session phase in IST."""
    utc_dt = validate_utc_datetime(timestamp)
    ist_dt = utc_dt.astimezone(NSE_TIMEZONE)
    if ist_dt.weekday() >= 5:
        return MarketSessionPhase.CLOSED

    ist_time = ist_dt.timetz().replace(tzinfo=None)
    if NSE_PRE_OPEN_START <= ist_time < NSE_MARKET_OPEN:
        return MarketSessionPhase.PRE_OPEN
    if NSE_MARKET_OPEN <= ist_time < NSE_MARKET_CLOSE:
        return MarketSessionPhase.OPEN
    if NSE_MARKET_CLOSE <= ist_time < NSE_POST_CLOSE_END:
        return MarketSessionPhase.POST_CLOSE
    return MarketSessionPhase.CLOSED


class MarketStatus(AlphaTraderBaseModel):
    """NSE market session status evaluated in Asia/Kolkata exchange time."""

    exchange: Literal["NSE"] = "NSE"
    timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    as_of: UtcDatetime
    session: MarketSessionPhase
    is_open: bool
    reason: str | None = None

    @model_validator(mode="after")
    def validate_session_consistency(self) -> Self:
        """Ensure is_open matches whether the session is OPEN."""
        expected_open = self.session == MarketSessionPhase.OPEN
        if self.is_open != expected_open:
            msg = (
                f"Inconsistent MarketStatus: session={self.session.value} "
                f"requires is_open={expected_open}, got {self.is_open}."
            )
            raise ValueError(msg)
        return self

    @classmethod
    def from_timestamp(cls, timestamp: datetime, reason: str | None = None) -> Self:
        """Construct a validated MarketStatus from any timezone-aware timestamp."""
        utc_dt = validate_utc_datetime(timestamp)
        session = classify_nse_session(utc_dt)
        return cls(
            as_of=utc_dt,
            session=session,
            is_open=(session == MarketSessionPhase.OPEN),
            reason=reason,
        )
