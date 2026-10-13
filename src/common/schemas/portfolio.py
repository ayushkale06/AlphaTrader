"""Portfolio ledger schemas: Position and PortfolioSnapshot."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    NonNegativeMoney,
    NseSymbol,
    PositivePrice,
    SignedMoney,
    SignedQuantity,
    UtcDatetime,
)


class Position(AlphaTraderBaseModel):
    """Open or flat paper position in a single NSE NIFTY 50 instrument."""

    symbol: NseSymbol
    quantity: SignedQuantity
    average_entry_price: PositivePrice
    mark_price: PositivePrice
    market_value: NonNegativeMoney
    unrealized_pnl: SignedMoney
    realized_pnl: SignedMoney = Decimal("0")
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def validate_market_value_consistency(self) -> Self:
        """Ensure market_value equals abs(quantity) * mark_price."""
        expected_market_value = abs(self.quantity) * self.mark_price
        if self.market_value != expected_market_value:
            msg = (
                f"Position market_value ({self.market_value}) must equal "
                f"abs(quantity) * mark_price ({expected_market_value})."
            )
            raise ValueError(msg)
        return self


class PortfolioSnapshot(AlphaTraderBaseModel):
    """Point-in-time snapshot of paper portfolio balances, PnL, exposure, and drawdown."""

    snapshot_id: UUID = Field(default_factory=uuid.uuid4)
    as_of: UtcDatetime
    cash: NonNegativeMoney
    equity: PositivePrice
    exposure: NonNegativeMoney
    realized_pnl: SignedMoney
    unrealized_pnl: SignedMoney
    drawdown: NonNegativeMoney = Field(
        le=Decimal("100"),
        description="Intraday drawdown percentage from daily high_water_mark in [0, 100].",
    )
    high_water_mark: PositivePrice
    positions: list[Position] = Field(default_factory=list)
    trading_mode: Literal["PAPER"] = "PAPER"

    @model_validator(mode="after")
    def validate_portfolio_invariants(self) -> Self:
        """Enforce high_water_mark >= equity and no duplicate symbols in positions."""
        if self.high_water_mark < self.equity:
            msg = (
                f"PortfolioSnapshot high_water_mark ({self.high_water_mark}) cannot be less "
                f"than current equity ({self.equity})."
            )
            raise ValueError(msg)

        seen_symbols: set[str] = set()
        for pos in self.positions:
            if pos.symbol in seen_symbols:
                msg = f"Duplicate symbol {pos.symbol!r} in PortfolioSnapshot.positions."
                raise ValueError(msg)
            seen_symbols.add(pos.symbol)
        return self
