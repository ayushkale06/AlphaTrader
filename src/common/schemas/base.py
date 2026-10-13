"""Foundational Pydantic v2 base model, UTC datetime, Decimal, and NSE symbol types.

Enforces three core domain invariants across all AlphaTrader schemas:
1. Immutability and strictness: ``frozen=True`` and ``extra="forbid"``.
2. Timezone safety: naive ``datetime`` objects are rejected; all timestamps normalize to UTC.
3. Financial precision: ``float`` inputs are rejected for prices, money, and quantities in
   favor of exact ``Decimal`` representations.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

_NSE_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9&-]{1,14}(?:\.NS)?$")


def validate_utc_datetime(value: object) -> datetime:
    """Validate that a datetime or ISO-8601 string is timezone-aware and normalize to UTC."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            msg = "Empty datetime string is invalid."
            raise ValueError(msg)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            msg = f"Invalid ISO-8601 datetime string: {value!r}"
            raise ValueError(msg) from exc
        value = parsed

    if not isinstance(value, datetime) or isinstance(value, bool):
        msg = f"Expected timezone-aware datetime or ISO-8601 string, got {type(value).__name__}."
        raise ValueError(msg)

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        msg = (
            "Naive datetime is prohibited; all timestamps must be timezone-aware "
            "and normalized to UTC."
        )
        raise ValueError(msg)

    return value.astimezone(UTC)


def validate_decimal_no_float(value: object) -> Decimal:
    """Convert int, str, or Decimal to a finite Decimal while strictly rejecting float/bool."""
    if isinstance(value, bool):
        msg = "Boolean values are prohibited for Decimal financial fields."
        raise ValueError(msg)
    if isinstance(value, float):
        msg = (
            f"Float value {value!r} is prohibited for financial fields due to IEEE-754 "
            "precision loss; pass Decimal, str, or int."
        )
        raise ValueError(msg)
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            msg = "Empty string cannot be converted to Decimal."
            raise ValueError(msg)
        try:
            dec = Decimal(stripped)
        except InvalidOperation as exc:
            msg = f"Invalid Decimal string: {value!r}"
            raise ValueError(msg) from exc
    else:
        msg = f"Expected Decimal, str, or int for financial field, got {type(value).__name__}."
        raise ValueError(msg)

    if not dec.is_finite():
        msg = f"Non-finite Decimal value {dec!r} (NaN or Infinity) is prohibited."
        raise ValueError(msg)
    return dec


def _serialize_decimal(value: Decimal) -> str:
    """Serialize Decimal deterministically as a plain string without scientific notation."""
    return format(value, "f")


def validate_nse_symbol(value: object) -> str:
    """Validate that a ticker symbol conforms to NSE NIFTY 50 conventions."""
    if not isinstance(value, str):
        msg = f"NSE symbol must be a string, got {type(value).__name__}."
        raise ValueError(msg)
    if value != value.strip() or not value:
        msg = f"Invalid NSE symbol {value!r}: leading/trailing whitespace or empty string."
        raise ValueError(msg)
    if not _NSE_SYMBOL_PATTERN.match(value):
        msg = (
            f"Invalid NSE symbol {value!r}: must be 2-15 uppercase alphanumeric characters "
            "(with optional '&' or '-') and optional '.NS' suffix (e.g. 'RELIANCE.NS', 'TCS')."
        )
        raise ValueError(msg)
    return value


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(validate_utc_datetime),
]

DecimalNumber = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

PositivePrice = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(gt=Decimal("0"), description="Strictly positive price in INR (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

NonNegativePrice = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(ge=Decimal("0"), description="Non-negative price in INR (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

NonNegativeMoney = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(ge=Decimal("0"), description="Non-negative monetary amount in INR (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

SignedMoney = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(description="Signed monetary amount in INR, e.g. PnL (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

PositiveQuantity = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(gt=Decimal("0"), description="Strictly positive order/fill quantity (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

NonNegativeQuantity = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(ge=Decimal("0"), description="Non-negative quantity or volume (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

SignedQuantity = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(description="Signed position quantity (Decimal)."),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

UnitIntervalDecimal = Annotated[
    Decimal,
    BeforeValidator(validate_decimal_no_float),
    Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Bounded Decimal ratio in [0, 1].",
    ),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]

NseSymbol = Annotated[
    str,
    BeforeValidator(validate_nse_symbol),
    Field(description="Canonical NSE ticker symbol (e.g. 'RELIANCE.NS' or 'RELIANCE')."),
]

NonEmptyStr = Annotated[
    str,
    Field(min_length=1, pattern=r"\S"),
]


class AlphaTraderBaseModel(BaseModel):
    """Canonical immutable Pydantic v2 base model for all AlphaTrader schemas."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        validate_default=True,
        str_strip_whitespace=False,
    )
