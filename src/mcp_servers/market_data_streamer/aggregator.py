"""OHLCV candlestick bar aggregation engine from continuous ticks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.common.schemas.market import (
    NSE_TIMEZONE,
    Candle,
    CandleInterval,
    DataProvenance,
    Tick,
)


def get_candle_boundary(
    ts: datetime,
    interval: CandleInterval,
) -> tuple[datetime, datetime]:
    """Calculate bucket open and close timestamps aligned to Asia/Kolkata boundary."""
    utc_ts = ts.astimezone(UTC)
    ist_ts = utc_ts.astimezone(NSE_TIMEZONE)

    if interval == CandleInterval.ONE_MINUTE:
        minutes = 1
    elif interval == CandleInterval.FIVE_MINUTES:
        minutes = 5
    elif interval == CandleInterval.FIFTEEN_MINUTES:
        minutes = 15
    elif interval == CandleInterval.ONE_HOUR:
        minutes = 60
    elif interval == CandleInterval.ONE_DAY:
        bucket_start_ist = ist_ts.replace(hour=9, minute=15, second=0, microsecond=0)
        bucket_end_ist = ist_ts.replace(hour=15, minute=30, second=0, microsecond=0)
        return bucket_start_ist.astimezone(UTC), bucket_end_ist.astimezone(UTC)
    else:
        minutes = 1

    bucket_min = (ist_ts.minute // minutes) * minutes
    bucket_start_ist = ist_ts.replace(minute=bucket_min, second=0, microsecond=0)
    bucket_end_ist = bucket_start_ist + timedelta(minutes=minutes)

    return bucket_start_ist.astimezone(UTC), bucket_end_ist.astimezone(UTC)


class CandleBarBuilder:
    """Aggregates ticks into an active candlestick bar."""

    def __init__(
        self,
        symbol: str,
        interval: CandleInterval,
        open_time: datetime,
        close_time: datetime,
        provenance: DataProvenance | None = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.open_time = open_time
        self.close_time = close_time
        self.provenance = provenance

        self.open: Decimal | None = None
        self.high: Decimal | None = None
        self.low: Decimal | None = None
        self.close: Decimal | None = None
        self.volume: Decimal = Decimal("0")
        self.dollar_volume: Decimal = Decimal("0")
        self.trade_count: int = 0

    def add_tick(self, tick: Tick) -> None:
        """Incorporate a new tick into this bar's OHLCV state."""
        price = tick.last_price
        vol = tick.volume

        if self.open is None:
            self.open = price
            self.high = price
            self.low = price
        else:
            assert self.high is not None
            assert self.low is not None
            if price > self.high:
                self.high = price
            if price < self.low:
                self.low = price

        self.close = price
        self.volume += vol
        self.dollar_volume += price * vol
        self.trade_count += 1
        if tick.provenance is not None:
            self.provenance = tick.provenance

    def build(self) -> Candle | None:
        """Finalize and construct a validated Candle model."""
        if self.open is None or self.high is None or self.low is None or self.close is None:
            return None

        vwap: Decimal | None = None
        if self.volume > 0:
            vwap = (self.dollar_volume / self.volume).quantize(Decimal("0.01"))
            # Enforce vwap in [low, high]
            vwap = max(self.low, min(self.high, vwap))

        return Candle(
            symbol=self.symbol,
            interval=self.interval,
            open_time=self.open_time,
            close_time=self.close_time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            vwap=vwap,
            trade_count=self.trade_count,
            provenance=self.provenance,
        )


class CandleAggregator:
    """Manages active candle buckets across symbols and time intervals.

    Supports:
    - Multiple intervals (e.g. 1m, 5m).
    - Flushes completed candles when a tick crosses bucket boundaries.
    - Preserves OHLCV invariants strictly.
    """

    def __init__(
        self,
        intervals: tuple[CandleInterval, ...] = (
            CandleInterval.ONE_MINUTE,
            CandleInterval.FIVE_MINUTES,
        ),
    ) -> None:
        self._intervals = intervals
        # Mapping: (symbol, interval) -> CandleBarBuilder
        self._active_bars: dict[tuple[str, CandleInterval], CandleBarBuilder] = {}

    def process_tick(self, tick: Tick) -> list[Candle]:
        """Incorporate tick into active bars and return any completed candles."""
        completed_candles: list[Candle] = []

        for interval in self._intervals:
            key = (tick.symbol, interval)
            open_time, close_time = get_candle_boundary(tick.timestamp, interval)

            current_builder = self._active_bars.get(key)
            if current_builder is not None:
                # If tick belongs to a new time bucket, finalize and flush the old one
                if tick.timestamp >= current_builder.close_time:
                    completed = current_builder.build()
                    if completed:
                        completed_candles.append(completed)
                    # Create new builder for current bucket
                    current_builder = CandleBarBuilder(
                        symbol=tick.symbol,
                        interval=interval,
                        open_time=open_time,
                        close_time=close_time,
                        provenance=tick.provenance,
                    )
                    self._active_bars[key] = current_builder
            else:
                current_builder = CandleBarBuilder(
                    symbol=tick.symbol,
                    interval=interval,
                    open_time=open_time,
                    close_time=close_time,
                    provenance=tick.provenance,
                )
                self._active_bars[key] = current_builder

            current_builder.add_tick(tick)

        return completed_candles

    def flush_all(self) -> list[Candle]:
        """Flush and return all currently active in-flight candles (used at shutdown)."""
        completed_candles: list[Candle] = []
        for builder in self._active_bars.values():
            candle = builder.build()
            if candle:
                completed_candles.append(candle)
        self._active_bars.clear()
        return completed_candles
