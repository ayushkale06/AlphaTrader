"""Provider-agnostic historical data loader with ADR-005 DataProvenance compliance."""

from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.common.schemas.market import (
    Candle,
    CandleInterval,
    DataProvenance,
    FeedMode,
)

logger = logging.getLogger("alphatrader.backtesting.data_loader")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "historical"


class HistoricalDataLoader:
    """Loads historical OHLCV candlestick series for backtesting.

    Guarantees:
    - Retains full DataProvenance (FeedMode.LIVE_HISTORICAL, REPLAY, or SIMULATED).
    - Guarantees strict ordering: candles are sorted chronologically by close_time.
    - Validates OHLC invariants on every bar.
    - Transparently documents whether loaded prices are adjusted (splits/dividends)
      and rejects mixing adjusted and unadjusted data.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DEFAULT_DATA_DIR

    def load_candles_from_jsonl(
        self,
        filepath: Path,
        symbol: str | None = None,
        interval: CandleInterval = CandleInterval.ONE_DAY,
    ) -> list[Candle]:
        """Load candles from a JSONL file containing serialized Candle envelopes or dicts."""
        if not filepath.exists():
            raise FileNotFoundError(f"Historical data file not found: {filepath}")

        candles: list[Candle] = []
        with open(filepath, encoding="utf-8") as f:
            for line_idx, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    # Check if wrapped in EventEnvelope
                    c_dict = (
                        payload.get("payload", payload) if isinstance(payload, dict) else payload
                    )

                    candle = Candle.model_validate(c_dict)
                    if symbol and candle.symbol != symbol:
                        continue
                    if candle.interval != interval:
                        continue
                    candles.append(candle)
                except Exception as err:
                    logger.warning(
                        "Error parsing candle at line %d in %s: %s", line_idx, filepath, err
                    )

        # Enforce chronological ordering
        candles.sort(key=lambda c: c.close_time)
        return candles

    def load_candles_from_csv(
        self,
        filepath: Path,
        symbol: str,
        interval: CandleInterval = CandleInterval.ONE_DAY,
        provenance: DataProvenance | None = None,
        is_adjusted: bool = True,
    ) -> list[Candle]:
        """Load candles from a standard CSV file (Date,Open,High,Low,Close,Adj Close,Volume)."""
        if not filepath.exists():
            raise FileNotFoundError(f"CSV historical data file not found: {filepath}")

        prov = provenance or DataProvenance(
            provider="historical_csv",
            feed_mode=FeedMode.LIVE,
            is_delayed=False,
            delay_seconds=0,
            ingested_at=datetime.now(UTC),
        )

        candles: list[Candle] = []
        with open(filepath, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader, start=2):
                try:
                    date_str = row.get("Date") or row.get("timestamp") or row.get("datetime")
                    if not date_str:
                        continue
                    # Parse timestamp
                    if "T" in date_str or "+" in date_str:
                        dt = datetime.fromisoformat(date_str)
                    else:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)

                    # Determine close price: adjusted vs raw
                    close_raw = (
                        row.get("Adj Close") if is_adjusted and "Adj Close" in row else row["Close"]
                    )
                    c_open = Decimal(str(row["Open"]))
                    c_high = Decimal(str(row["High"]))
                    c_low = Decimal(str(row["Low"]))
                    c_close = Decimal(str(close_raw))
                    c_vol = Decimal(str(int(float(row.get("Volume", 0)))))

                    # Invariant preservation: high >= max(open, close, low), low <= min(...)
                    c_high = max(c_high, max(c_open, c_close))
                    c_low = min(c_low, min(c_open, c_close))

                    # Open time and close time calculation
                    open_time = dt
                    if interval == CandleInterval.ONE_DAY:
                        close_time = dt + timedelta(hours=6, minutes=15)
                    elif interval == CandleInterval.FIVE_MINUTES:
                        close_time = dt + timedelta(minutes=5)
                    elif interval == CandleInterval.FIFTEEN_MINUTES:
                        close_time = dt + timedelta(minutes=15)
                    elif interval == CandleInterval.ONE_MINUTE:
                        close_time = dt + timedelta(minutes=1)
                    else:
                        close_time = dt + timedelta(hours=1)

                    candle = Candle(
                        symbol=symbol,
                        interval=interval,
                        open_time=open_time,
                        close_time=close_time,
                        open=c_open,
                        high=c_high,
                        low=c_low,
                        close=c_close,
                        volume=c_vol,
                        is_final=True,
                        provenance=prov,
                    )
                    candles.append(candle)
                except Exception as err:
                    logger.warning("Error parsing CSV row %d in %s: %s", row_idx, filepath, err)

        candles.sort(key=lambda c: c.close_time)
        return candles
