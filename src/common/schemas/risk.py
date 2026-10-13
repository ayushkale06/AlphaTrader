"""Deterministic risk guardrail schemas: RiskDecision, RiskInputSnapshot, and HaltEvent."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    NonEmptyStr,
    NonNegativeMoney,
    NseSymbol,
    PositivePrice,
    UtcDatetime,
)


class RiskVerdict(StrEnum):
    """Deterministic action taken by the non-LLM Risk Guardrail MCP server."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    HALT = "HALT"


class RiskReasonCode(StrEnum):
    """Canonical machine-readable reason codes for risk guardrail evaluations."""

    WITHIN_LIMITS = "WITHIN_LIMITS"
    MAX_DAILY_DRAWDOWN_BREACH = "MAX_DAILY_DRAWDOWN_BREACH"
    MAX_POSITION_SIZE_BREACH = "MAX_POSITION_SIZE_BREACH"
    MAX_OPEN_POSITIONS_BREACH = "MAX_OPEN_POSITIONS_BREACH"
    ORDER_RATE_LIMIT_EXCEEDED = "ORDER_RATE_LIMIT_EXCEEDED"
    MARKET_CLOSED = "MARKET_CLOSED"
    FLOOR_ALREADY_HALTED = "FLOOR_ALREADY_HALTED"
    MISSING_STOP_LOSS = "MISSING_STOP_LOSS"
    MANUAL_KILL_SWITCH = "MANUAL_KILL_SWITCH"


class RiskInputSnapshot(AlphaTraderBaseModel):
    """Immutable snapshot of portfolio and order metrics at the instant of risk evaluation."""

    symbol: NseSymbol
    proposed_notional: NonNegativeMoney
    portfolio_equity: PositivePrice
    current_drawdown_pct: NonNegativeMoney
    max_daily_drawdown_pct: PositivePrice
    post_trade_position_pct: NonNegativeMoney
    max_position_size_pct: PositivePrice
    open_positions_count: int = Field(ge=0)
    max_open_positions: int = Field(ge=1)
    orders_last_minute: int = Field(default=0, ge=0)


class RiskDecision(AlphaTraderBaseModel):
    """Audit record emitted by MCP Server 4 (Deterministic Risk Guardrail) for an order proposal."""

    decision_id: UUID = Field(default_factory=uuid.uuid4)
    order_request_id: UUID
    evaluated_at: UtcDatetime
    decision: RiskVerdict
    rule_fired: NonEmptyStr = Field(
        description="Identifier of the deterministic rule that determined the verdict.",
    )
    reason_code: RiskReasonCode
    inputs_snapshot: RiskInputSnapshot
    approval_token: str | None = Field(
        default=None,
        description="Signed deterministic risk token required by the Paper Execution Broker.",
    )
    message: NonEmptyStr

    @model_validator(mode="after")
    def validate_verdict_invariants(self) -> Self:
        """Enforce consistency between decision, reason_code, and approval_token."""
        if self.decision == RiskVerdict.APPROVE:
            if self.reason_code != RiskReasonCode.WITHIN_LIMITS:
                msg = (
                    f"RiskDecision with decision=APPROVE must have "
                    f"reason_code=WITHIN_LIMITS, got {self.reason_code.value}."
                )
                raise ValueError(msg)
            if not self.approval_token or not self.approval_token.strip():
                msg = "RiskDecision with decision=APPROVE must include a non-empty approval_token."
                raise ValueError(msg)
        else:
            if self.reason_code == RiskReasonCode.WITHIN_LIMITS:
                msg = (
                    f"RiskDecision with decision={self.decision.value} cannot use "
                    "reason_code=WITHIN_LIMITS."
                )
                raise ValueError(msg)
            if self.approval_token is not None:
                msg = (
                    f"RiskDecision with decision={self.decision.value} must not issue "
                    "an approval_token."
                )
                raise ValueError(msg)
        return self


class HaltEvent(AlphaTraderBaseModel):
    """Critical circuit-breaker event halting the entire AlphaTrader paper trading floor."""

    halt_id: UUID = Field(default_factory=uuid.uuid4)
    triggered_at: UtcDatetime
    rule_fired: NonEmptyStr
    reason_code: RiskReasonCode
    current_drawdown_pct: NonNegativeMoney
    max_daily_drawdown_pct: PositivePrice
    portfolio_equity: NonNegativeMoney
    high_water_mark: PositivePrice
    details: NonEmptyStr

    @model_validator(mode="after")
    def validate_halt_reason(self) -> Self:
        """A floor halt can never carry reason_code=WITHIN_LIMITS."""
        if self.reason_code == RiskReasonCode.WITHIN_LIMITS:
            msg = "HaltEvent cannot have reason_code=WITHIN_LIMITS."
            raise ValueError(msg)
        return self
