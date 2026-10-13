"""Paper order lifecycle schemas, fills, and deterministic status state machine."""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    NonEmptyStr,
    NonNegativeMoney,
    NonNegativeQuantity,
    NseSymbol,
    PositivePrice,
    PositiveQuantity,
    UtcDatetime,
)


class Side(StrEnum):
    """Order execution side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Supported NSE paper order execution types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"


class OrderStatus(StrEnum):
    """Deterministic state machine states for a paper order."""

    PENDING_RISK = "PENDING_RISK"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class IllegalOrderTransitionError(ValueError):
    """Raised when an order attempts an illegal state machine transition."""


LEGAL_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_RISK: frozenset(
        {OrderStatus.APPROVED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.APPROVED: frozenset(
        {OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
    ),
    OrderStatus.SUBMITTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def validate_order_status_transition(
    current_status: OrderStatus,
    next_status: OrderStatus,
) -> OrderStatus:
    """Validate that transitioning from ``current_status`` to ``next_status`` is legal."""
    allowed = LEGAL_ORDER_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed:
        allowed_names = sorted(s.value for s in allowed)
        msg = (
            f"Illegal order status transition: {current_status.value} -> {next_status.value}. "
            f"Allowed targets from {current_status.value}: {allowed_names}."
        )
        raise IllegalOrderTransitionError(msg)
    return next_status


class OrderRequest(AlphaTraderBaseModel):
    """Candidate paper order request submitted for risk evaluation and execution."""

    order_request_id: UUID = Field(default_factory=uuid.uuid4)
    idempotency_key: NonEmptyStr = Field(
        description="Deterministic client-generated key preventing duplicate order submission.",
    )
    symbol: NseSymbol
    side: Side
    order_type: OrderType
    quantity: PositiveQuantity
    limit_price: PositivePrice | None = None
    stop_loss_price: PositivePrice
    take_profit_price: PositivePrice | None = None
    strategy_name: NonEmptyStr
    requested_at: UtcDatetime
    trading_mode: Literal["PAPER"] = "PAPER"

    @model_validator(mode="after")
    def validate_limit_price_requirement(self) -> Self:
        """Require limit_price when order_type is LIMIT."""
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            msg = "OrderRequest with order_type=LIMIT requires a positive limit_price."
            raise ValueError(msg)
        return self


class Order(AlphaTraderBaseModel):
    """Tracked paper order record with state machine enforcement."""

    order_id: UUID = Field(default_factory=uuid.uuid4)
    order_request_id: UUID
    idempotency_key: NonEmptyStr
    symbol: NseSymbol
    side: Side
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING_RISK
    quantity: PositiveQuantity
    filled_quantity: NonNegativeQuantity = Decimal("0")
    average_fill_price: PositivePrice | None = None
    limit_price: PositivePrice | None = None
    stop_loss_price: PositivePrice
    approval_token: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    trading_mode: Literal["PAPER"] = "PAPER"

    @model_validator(mode="after")
    def validate_order_invariants(self) -> Self:
        """Validate fill quantities, timestamps, and status-dependent fields."""
        if self.updated_at < self.created_at:
            msg = (
                f"Order updated_at ({self.updated_at}) cannot precede "
                f"created_at ({self.created_at})."
            )
            raise ValueError(msg)
        if self.filled_quantity > self.quantity:
            msg = (
                f"Order filled_quantity ({self.filled_quantity}) cannot exceed "
                f"quantity ({self.quantity})."
            )
            raise ValueError(msg)
        if self.status == OrderStatus.FILLED:
            if self.filled_quantity != self.quantity:
                msg = (
                    f"Order with status=FILLED must have filled_quantity ({self.filled_quantity}) "
                    f"== quantity ({self.quantity})."
                )
                raise ValueError(msg)
            if self.average_fill_price is None:
                msg = "Order with status=FILLED requires average_fill_price."
                raise ValueError(msg)
        if self.status == OrderStatus.PARTIALLY_FILLED:
            if not (Decimal("0") < self.filled_quantity < self.quantity):
                msg = (
                    "Order with status=PARTIALLY_FILLED must satisfy "
                    "0 < filled_quantity < quantity."
                )
                raise ValueError(msg)
            if self.average_fill_price is None:
                msg = "Order with status=PARTIALLY_FILLED requires average_fill_price."
                raise ValueError(msg)
        return self


class Fill(AlphaTraderBaseModel):
    """Simulated paper trade execution fill including Indian statutory transaction taxes."""

    fill_id: UUID = Field(default_factory=uuid.uuid4)
    order_id: UUID
    order_request_id: UUID
    symbol: NseSymbol
    side: Side
    fill_price: PositivePrice
    fill_quantity: PositiveQuantity
    slippage_bps: NonNegativeMoney = Decimal("0")
    stt_tax: NonNegativeMoney = Decimal("0")
    exchange_fees: NonNegativeMoney = Decimal("0")
    gst_tax: NonNegativeMoney = Decimal("0")
    stamp_duty: NonNegativeMoney = Decimal("0")
    total_fees: NonNegativeMoney = Decimal("0")
    filled_at: UtcDatetime
    trading_mode: Literal["PAPER"] = "PAPER"

    @model_validator(mode="after")
    def validate_fee_breakdown(self) -> Self:
        """Ensure total_fees equals the sum of component statutory taxes and fees."""
        component_sum = self.stt_tax + self.exchange_fees + self.gst_tax + self.stamp_duty
        if self.total_fees != component_sum:
            msg = (
                f"Fill total_fees ({self.total_fees}) must equal the sum of component fees "
                f"({component_sum})."
            )
            raise ValueError(msg)
        return self
