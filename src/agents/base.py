"""Base abstractions, contracts, and execution models for autonomous trading agents.

Governance: ADR-002 (Paper Only), ADR-004 (Event-Driven System of Record), ADR-011.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.common.schemas.envelope import ComponentSource
from src.common.schemas.market import CandleInterval
from src.common.schemas.orders import Fill, OrderRequest, OrderStatus
from src.common.schemas.risk import RiskDecision
from src.common.schemas.signals import MarketRegime, TechnicalSignal

logger = logging.getLogger("alphatrader.agents")


# -----------------------------------------------------------------------------
# Agent Request & Result Models
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalystRequest:
    """Input payload to AnalystAgent."""

    symbol: str
    interval: CandleInterval
    correlation_id: str
    trigger_event_id: UUID
    current_market_price: Decimal | None = None


@dataclass(frozen=True)
class AnalystResult:
    """Output result from AnalystAgent."""

    success: bool
    signal: TechnicalSignal | None
    regime: MarketRegime | None
    explanation: str
    correlation_id: str
    causation_id: UUID
    error_code: str | None = None


@dataclass(frozen=True)
class QuantRequest:
    """Input payload to QuantAgent."""

    analyst_signal: TechnicalSignal
    portfolio_equity: Decimal
    correlation_id: str
    trigger_event_id: UUID


@dataclass(frozen=True)
class QuantResult:
    """Output result from QuantAgent."""

    success: bool
    order_proposal: OrderRequest | None
    alpha_score: Decimal | None
    explanation: str
    correlation_id: str
    causation_id: UUID
    error_code: str | None = None


@dataclass(frozen=True)
class RiskRequest:
    """Input payload to RiskAgent."""

    order_proposal: OrderRequest
    correlation_id: str
    trigger_event_id: UUID


@dataclass(frozen=True)
class RiskResult:
    """Output result from RiskAgent."""

    success: bool
    approved: bool
    risk_decision: RiskDecision | None
    approval_token: str | None
    rejection_reasons: list[str]
    correlation_id: str
    causation_id: UUID
    error_code: str | None = None


@dataclass(frozen=True)
class ExecutionRequest:
    """Input payload to ExecutionAgent."""

    order_proposal: OrderRequest
    approval_token: str
    current_market_price: Decimal
    correlation_id: str
    trigger_event_id: UUID


@dataclass(frozen=True)
class ExecutionResult:
    """Output result from ExecutionAgent."""

    success: bool
    status: OrderStatus
    fill: Fill | None
    order_id: UUID | None
    fill_id: UUID | None
    statutory_fees: dict[str, Decimal] | None
    correlation_id: str
    causation_id: UUID
    error_code: str | None = None


# -----------------------------------------------------------------------------
# Base Agent Abstract Class
# -----------------------------------------------------------------------------
class BaseAgent(ABC):
    """Abstract base class for all AlphaTrader autonomous agents."""

    agent_name: str = "BaseAgent"
    component_source: ComponentSource = ComponentSource.SYSTEM

    def __init__(self, mcp_client: Any | None = None) -> None:
        self.mcp_client = mcp_client
        self.logger = logging.getLogger(f"alphatrader.agents.{self.agent_name.lower()}")

    @abstractmethod
    def process(self, request: Any) -> Any:
        """Process incoming request deterministically with strict provenance."""
