"""Property-based tests using Hypothesis for EventEnvelope round-trips and Decimal precision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from src.common.schemas import (
    ComponentSource,
    EventEnvelope,
    Tick,
    validate_decimal_no_float,
)

NSE_SYMBOLS_STRATEGY = st.sampled_from(
    [
        "RELIANCE.NS",
        "TCS.NS",
        "INFY.NS",
        "HDFCBANK.NS",
        "ICICIBANK.NS",
        "SBIN.NS",
        "M&M.NS",
        "BAJAJ-AUTO.NS",
        "LT.NS",
        "NIFTY50",
    ]
)

POSITIVE_DECIMAL_STRATEGY = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000.00"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

NON_NEGATIVE_DECIMAL_STRATEGY = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("100000000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

TZ_OFFSETS_STRATEGY = st.integers(min_value=-720, max_value=840).map(
    lambda mins: timezone(timedelta(minutes=mins))
)

AWARE_DATETIME_STRATEGY = st.builds(
    lambda dt, tz: dt.replace(tzinfo=tz),
    st.datetimes(
        min_value=datetime(2020, 1, 1, 0, 0, 0),
        max_value=datetime(2035, 12, 31, 23, 59, 59),
    ),
    TZ_OFFSETS_STRATEGY,
)


@settings(max_examples=50, deadline=None)
@given(
    event_id=st.uuids(version=4),
    causation_id=st.one_of(st.none(), st.uuids(version=4)),
    correlation_id=st.from_regex(r"^cid-[a-z0-9]{4,16}$", fullmatch=True),
    produced_at=AWARE_DATETIME_STRATEGY,
    source=st.sampled_from(list(ComponentSource)),
    symbol=NSE_SYMBOLS_STRATEGY,
    tick_ts=AWARE_DATETIME_STRATEGY,
    last_price=POSITIVE_DECIMAL_STRATEGY,
    volume=NON_NEGATIVE_DECIMAL_STRATEGY,
    seq_no=st.integers(min_value=1, max_value=10_000_000),
)
def test_hypothesis_envelope_deterministic_round_trip(
    event_id: UUID,
    causation_id: UUID | None,
    correlation_id: str,
    produced_at: datetime,
    source: ComponentSource,
    symbol: str,
    tick_ts: datetime,
    last_price: Decimal,
    volume: Decimal,
    seq_no: int,
) -> None:
    """Any valid EventEnvelope[Tick] must round-trip deterministically via JSON bytes."""
    tick = Tick(
        symbol=symbol,
        timestamp=tick_ts,
        last_price=last_price,
        volume=volume,
        sequence_number=seq_no,
    )
    envelope = EventEnvelope[Tick](
        event_id=event_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        produced_at=produced_at,
        source=source,
        payload=tick,
    )

    first_bytes = envelope.to_json_bytes()
    restored = EventEnvelope[Tick].from_json_bytes(first_bytes)
    second_bytes = restored.to_json_bytes()

    assert restored == envelope
    assert first_bytes == second_bytes
    assert restored.produced_at.tzinfo == UTC
    assert restored.payload.timestamp.tzinfo == UTC
    assert restored.payload.last_price == last_price
    assert restored.payload.volume == volume


@settings(max_examples=50, deadline=None)
@given(
    dec_val=st.decimals(
        min_value=Decimal("-999999999.999999"),
        max_value=Decimal("999999999.999999"),
        places=6,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_hypothesis_decimal_precision_preserved_from_str_and_decimal(
    dec_val: Decimal,
) -> None:
    """Arbitrary finite Decimals and their string representations preserve exact precision."""
    from_dec = validate_decimal_no_float(dec_val)
    from_str = validate_decimal_no_float(format(dec_val, "f"))
    assert from_dec == dec_val
    assert from_str == dec_val


@settings(max_examples=30, deadline=None)
@given(float_val=st.floats(allow_nan=True, allow_infinity=True))
def test_hypothesis_all_floats_rejected_by_decimal_validator(float_val: float) -> None:
    """Every possible IEEE-754 float (finite, NaN, or infinite) must be rejected."""
    with pytest.raises((ValueError, ValidationError)):
        validate_decimal_no_float(float_val)
