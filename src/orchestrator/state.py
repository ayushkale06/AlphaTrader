"""Typed state definitions for the Trading Floor Multi-Agent Orchestrator.

Governance: ADR-001, ADR-002, ADR-003, ADR-011, ADR-012.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.common.schemas.market import CandleInterval, Tick
from src.common.schemas.orders import Fill, OrderRequest, OrderStatus
from src.common.schemas.risk import RiskDecision
from src.common.schemas.signals import TechnicalSignal


class OrchestratorStage(StrEnum):
    """Execution stages of the Trading Floor Orchestrator state graph."""

    INITIATED = "INITIATED"
    FLOOR_HALTED = "FLOOR_HALTED"
    ANALYST_EVALUATION = "ANALYST_EVALUATION"
    QUANT_SIZING = "QUANT_SIZING"
    RISK_AUDIT = "RISK_AUDIT"
    PAPER_EXECUTION = "PAPER_EXECUTION"
    RECONCILIATION = "RECONCILIATION"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class TradingFloorState:
    """State carried through the multi-agent trading floor graph.

    Maintains strict event provenance across all hops:
    - correlation_id: Unified session identifier propagated across all nodes.
    - trigger_event_id: Event ID of the inbound market trigger.
    - causation_id: Upstream event ID causing the immediate subsequent hop.
    """

    # Event Lineage & Metadata
    correlation_id: str
    trigger_event_id: UUID
    causation_id: UUID
    symbol: str
    interval: CandleInterval
    current_market_price: Decimal
    tick: Tick | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Graph Traversal Lifecycle
    stage: OrchestratorStage = OrchestratorStage.INITIATED
    trading_mode: str = "PAPER"
    floor_halted: bool = False

    # Node Outputs
    analyst_signal: TechnicalSignal | None = None
    quant_order: OrderRequest | None = None
    risk_decision: RiskDecision | None = None
    approval_token: str | None = None
    execution_status: OrderStatus | None = None
    fill: Fill | None = None
    order_id: UUID | None = None
    fill_id: UUID | None = None

    # Error & Termination Tracking
    rejection_reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reconciliation_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for persistent checkpointing and audit logging."""
        return {
            "correlation_id": self.correlation_id,
            "trigger_event_id": str(self.trigger_event_id),
            "causation_id": str(self.causation_id),
            "symbol": self.symbol,
            "interval": self.interval.value,
            "current_market_price": str(self.current_market_price),
            "stage": self.stage.value,
            "trading_mode": self.trading_mode,
            "floor_halted": self.floor_halted,
            "analyst_signal_id": (
                str(self.analyst_signal.signal_id) if self.analyst_signal else None
            ),
            "quant_order_request_id": (
                str(self.quant_order.order_request_id) if self.quant_order else None
            ),
            "risk_decision_id": (
                str(self.risk_decision.decision_id) if self.risk_decision else None
            ),
            "has_approval_token": bool(self.approval_token),
            "execution_status": (self.execution_status.value if self.execution_status else None),
            "order_id": str(self.order_id) if self.order_id else None,
            "fill_id": str(self.fill_id) if self.fill_id else None,
            "rejection_reasons": self.rejection_reasons,
            "errors": self.errors,
            "reconciliation_attempts": self.reconciliation_attempts,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
