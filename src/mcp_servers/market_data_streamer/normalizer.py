"""Validates and normalizes raw provider quotes into canonical Tick models."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from src.common.schemas.market import DataProvenance, FeedMode, Tick

logger = logging.getLogger("alphatrader.normalizer")


class NormalizationResult:
    """Outcome of a raw quote normalization attempt."""

    def __init__(
        self,
        tick: Tick | None = None,
        is_valid: bool = True,
        error_message: str | None = None,
        error_code: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        self.tick = tick
        self.is_valid = is_valid
        self.error_message = error_message
        self.error_code = error_code
        self.raw_payload = raw_payload or {}


class TickNormalizer:
    """Transforms raw dictionary or provider objects into validated Tick instances.

    Guarantees:
    - Rejects missing fields, invalid symbols, non-positive prices, negative quantities.
    - Tracks per-symbol sequence numbers and detects out-of-order sequence arrivals.
    - Populates DataProvenance if omitted by the raw source.
    """

    def __init__(
        self, default_provider: str = "simulated_feed", feed_mode: FeedMode = FeedMode.SIMULATED
    ) -> None:
        self._default_provider = default_provider
        self._feed_mode = feed_mode
        self._last_sequence: dict[str, int] = {}
        self._rejected_count = 0

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def normalize(self, raw: dict[str, Any] | Tick) -> NormalizationResult:
        """Validate and convert input into a canonical Tick model."""
        if isinstance(raw, Tick):
            return NormalizationResult(tick=raw, is_valid=True)

        try:
            symbol = raw.get("symbol")
            if not symbol or not isinstance(symbol, str):
                self._rejected_count += 1
                return NormalizationResult(
                    is_valid=False,
                    error_message="Missing or invalid ticker symbol",
                    error_code="INVALID_TICK_PAYLOAD",
                    raw_payload=raw,
                )

            # Check price values
            try:
                last_price = Decimal(str(raw["last_price"]))
                if last_price <= Decimal("0"):
                    self._rejected_count += 1
                    return NormalizationResult(
                        is_valid=False,
                        error_message="Last price must be positive",
                        error_code="NON_POSITIVE_PRICE",
                        raw_payload=raw,
                    )
            except Exception as exc:
                self._rejected_count += 1
                return NormalizationResult(
                    is_valid=False,
                    error_message=f"Invalid last_price: {exc}",
                    error_code="NON_POSITIVE_PRICE",
                    raw_payload=raw,
                )

            bid = Decimal(str(raw["bid_price"])) if raw.get("bid_price") is not None else None
            ask = Decimal(str(raw["ask_price"])) if raw.get("ask_price") is not None else None
            if bid is not None and ask is not None and bid > ask:
                self._rejected_count += 1
                return NormalizationResult(
                    is_valid=False,
                    error_message=f"Bid price ({bid}) cannot exceed ask price ({ask})",
                    error_code="INVERTED_BID_ASK",
                    raw_payload=raw,
                )

            # Sequence monotonicity check
            seq = int(raw.get("sequence_number", 1))
            last_seq = self._last_sequence.get(symbol, 0)
            if seq <= last_seq:
                logger.warning(
                    "Out-of-order or duplicate sequence for %s: got %d, last was %d",
                    symbol,
                    seq,
                    last_seq,
                )
                self._rejected_count += 1
                return NormalizationResult(
                    is_valid=False,
                    error_message=f"Sequence number {seq} <= last seen sequence {last_seq}",
                    error_code="SEQUENCE_OUT_OF_ORDER",
                    raw_payload=raw,
                )
            self._last_sequence[symbol] = seq

            # Parse timestamp
            ts_raw = raw.get("timestamp")
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw).astimezone(UTC)
            elif isinstance(ts_raw, datetime):
                ts = ts_raw.astimezone(UTC)
            else:
                ts = datetime.now(UTC)

            # Build or extract provenance
            prov_raw = raw.get("provenance")
            if isinstance(prov_raw, dict):
                provenance = DataProvenance.model_validate(prov_raw)
            else:
                provenance = DataProvenance(
                    provider=self._default_provider,
                    feed_mode=self._feed_mode,
                    is_delayed=False,
                    delay_seconds=0,
                    ingested_at=datetime.now(UTC),
                )

            tick = Tick(
                symbol=symbol,
                timestamp=ts,
                last_price=last_price,
                volume=Decimal(str(raw["volume"])),
                bid_price=bid,
                ask_price=ask,
                sequence_number=seq,
                provenance=provenance,
            )
            return NormalizationResult(tick=tick, is_valid=True, raw_payload=raw)

        except (ValidationError, KeyError, ValueError, TypeError) as exc:
            self._rejected_count += 1
            return NormalizationResult(
                is_valid=False,
                error_message=f"Schema normalization error: {exc}",
                error_code="INVALID_TICK_PAYLOAD",
                raw_payload=raw,
            )
