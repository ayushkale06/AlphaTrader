"""Trading Floor Multi-Agent Stateful Orchestrator.

Implements directed state-graph workflow:
1. Floor Circuit Breaker Check (Redis alphatrader:floor_halted)
2. AnalystAgent (Indicators & Volatility Regime Evaluation)
3. QuantAgent (Factor Alpha & Fractional Kelly Sizing)
4. RiskAgent (Deterministic Pre-Trade Limit Evaluation)
5. ExecutionAgent (Paper Broker Matching, Taxes & Execution)
6. Checkpoint & Error Handling (State Persistence & Reconciliation)

Governance: ADR-001, ADR-002, ADR-004, ADR-008, ADR-011, ADR-012.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.agents.analyst.agent import AnalystAgent
from src.agents.base import AnalystRequest, ExecutionRequest, QuantRequest, RiskRequest
from src.agents.execution.agent import ExecutionAgent
from src.agents.mcp_client import MCPClientSession
from src.agents.quant.agent import QuantAgent
from src.agents.risk.agent import RiskAgent
from src.common.config import get_settings
from src.common.schemas.orders import OrderStatus
from src.common.schemas.signals import Direction
from src.orchestrator.checkpoint import OrchestratorCheckpointer
from src.orchestrator.state import OrchestratorStage, TradingFloorState

logger = logging.getLogger("alphatrader.orchestrator.graph")


class TradingFloorOrchestrator:
    """Stateful directed multi-agent graph orchestrator."""

    def __init__(
        self,
        mcp_client: MCPClientSession | None = None,
        redis_client: Any = None,
        checkpointer: OrchestratorCheckpointer | None = None,
        analyst_agent: AnalystAgent | None = None,
        quant_agent: QuantAgent | None = None,
        risk_agent: RiskAgent | None = None,
        execution_agent: ExecutionAgent | None = None,
    ) -> None:
        settings = get_settings()
        # Invariant: Must run strictly in PAPER mode
        if settings.trading_mode != "PAPER":
            msg = (
                "TradingFloorOrchestrator requires TRADING_MODE='PAPER', "
                f"got '{settings.trading_mode}'"
            )
            raise ValueError(msg)

        self.mcp_client = mcp_client
        self.redis = redis_client
        self.checkpointer = checkpointer or OrchestratorCheckpointer(redis_client=redis_client)

        self.analyst = analyst_agent or AnalystAgent(mcp_client=mcp_client)
        self.quant = quant_agent or QuantAgent(mcp_client=mcp_client)
        self.risk = risk_agent or RiskAgent(mcp_client=mcp_client)
        self.execution = execution_agent or ExecutionAgent(mcp_client=mcp_client)

    def is_floor_halted(self) -> bool:
        """Check real-time floor circuit breaker latch in Redis."""
        if self.redis is None:
            return False
        try:
            val = self.redis.get("alphatrader:floor_halted")
            if val is None:
                return False
            val_str = val.decode("utf-8") if isinstance(val, bytes) else str(val)
            return val_str.lower() in ("true", "1", "halted")
        except Exception as e:
            logger.warning("Error checking floor circuit-breaker status: %s", e)
            return False

    def run(self, state: TradingFloorState) -> TradingFloorState:
        """Execute full stateful graph traversal over the trading floor."""
        logger.info(
            "Initiating trading floor traversal for %s (correlation_id=%s)",
            state.symbol,
            state.correlation_id,
        )

        # ---------------------------------------------------------------------
        # Step 1: Pre-flight Floor Circuit Breaker Check
        # ---------------------------------------------------------------------
        if self.is_floor_halted() or state.floor_halted:
            logger.warning("Trading floor is HALTED. Aborting traversal for %s", state.symbol)
            state.floor_halted = True
            state.stage = OrchestratorStage.FLOOR_HALTED
            state.rejection_reasons.append("Trading floor circuit breaker active.")
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        # ---------------------------------------------------------------------
        # Step 2: Analyst Agent Node (Indicators & Regime)
        # ---------------------------------------------------------------------
        state.stage = OrchestratorStage.ANALYST_EVALUATION
        analyst_req = AnalystRequest(
            symbol=state.symbol,
            interval=state.interval,
            correlation_id=state.correlation_id,
            trigger_event_id=state.trigger_event_id,
            current_market_price=state.current_market_price,
        )
        analyst_res = self.analyst.process(analyst_req)

        if not analyst_res.success or analyst_res.signal is None:
            state.errors.append(f"AnalystAgent failed: {analyst_res.error_code}")
            state.stage = OrchestratorStage.FAILED
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        state.analyst_signal = analyst_res.signal
        state.causation_id = analyst_res.signal.signal_id

        # Conditional Edge: If FLAT / neutral, conclude with no trade
        if analyst_res.signal.direction == Direction.FLAT:
            logger.info("Market regime evaluated to FLAT. No trade action required.")
            state.stage = OrchestratorStage.COMPLETED
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        # ---------------------------------------------------------------------
        # Step 3: Quant Agent Node (Alpha Factors & Fractional Kelly Sizing)
        # ---------------------------------------------------------------------
        state.stage = OrchestratorStage.QUANT_SIZING
        quant_req = QuantRequest(
            analyst_signal=analyst_res.signal,
            portfolio_equity=Decimal("10000000.00"),
            correlation_id=state.correlation_id,
            trigger_event_id=state.causation_id,
        )
        quant_res = self.quant.process(quant_req)

        if not quant_res.success:
            state.errors.append(f"QuantAgent failed: {quant_res.error_code}")
            state.stage = OrchestratorStage.FAILED
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        # Conditional Edge: If no order proposed (e.g. edge <= 0 or zero sizing)
        if quant_res.order_proposal is None:
            logger.info("Quant agent determined zero sizing or no statistical edge.")
            state.stage = OrchestratorStage.COMPLETED
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        state.quant_order = quant_res.order_proposal
        state.causation_id = quant_res.order_proposal.order_request_id

        # ---------------------------------------------------------------------
        # Step 4: Risk Agent Node (Deterministic Guardrails & Limits)
        # ---------------------------------------------------------------------
        state.stage = OrchestratorStage.RISK_AUDIT
        risk_req = RiskRequest(
            order_proposal=state.quant_order,
            correlation_id=state.correlation_id,
            trigger_event_id=state.causation_id,
        )
        risk_res = self.risk.process(risk_req)

        state.risk_decision = risk_res.risk_decision

        # Conditional Edge: If rejected by Risk Guardrail
        if not risk_res.approved or risk_res.approval_token is None:
            logger.warning(
                "Order proposal %s rejected by Risk Guardrail: %s",
                state.quant_order.order_request_id,
                risk_res.rejection_reasons,
            )
            state.stage = OrchestratorStage.REJECTED
            state.rejection_reasons.extend(risk_res.rejection_reasons)
            state.updated_at = datetime.now(UTC)
            self.checkpointer.save(state)
            return state

        state.approval_token = risk_res.approval_token
        if risk_res.risk_decision is not None:
            state.causation_id = risk_res.risk_decision.decision_id

        # ---------------------------------------------------------------------
        # Step 5: Execution Agent Node (Paper Broker Matching & Taxes)
        # ---------------------------------------------------------------------
        state.stage = OrchestratorStage.PAPER_EXECUTION
        exec_req = ExecutionRequest(
            order_proposal=state.quant_order,
            approval_token=state.approval_token,
            current_market_price=state.current_market_price,
            correlation_id=state.correlation_id,
            trigger_event_id=state.causation_id,
        )
        exec_res = self.execution.process(exec_req)

        state.execution_status = exec_res.status
        state.order_id = exec_res.order_id
        state.fill_id = exec_res.fill_id
        state.fill = exec_res.fill

        if exec_res.success and exec_res.status == OrderStatus.FILLED:
            logger.info(
                "Execution successfully filled order %s (fill_id=%s)",
                state.order_id,
                state.fill_id,
            )
            state.stage = OrchestratorStage.COMPLETED
        else:
            logger.warning(
                "Execution failed or rejected for order %s: %s",
                state.order_id,
                exec_res.error_code,
            )
            state.stage = OrchestratorStage.REJECTED
            if exec_res.error_code:
                state.rejection_reasons.append(exec_res.error_code)

        state.updated_at = datetime.now(UTC)
        self.checkpointer.save(state)
        return state
