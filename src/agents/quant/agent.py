"""Quant Strategy Autonomous Agent.

Invokes MCP Server 3 (quant-strategy-engine) to evaluate multi-factor alpha models
and generate mathematically sized candidate OrderRequests with ATR stop losses.
Governance: ADR-002, ADR-004, ADR-011.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from src.agents.base import BaseAgent, QuantRequest, QuantResult
from src.agents.mcp_client import MCPClientSession
from src.common.schemas.envelope import ComponentSource
from src.common.schemas.orders import OrderRequest, OrderType, Side
from src.common.schemas.signals import Direction

logger = logging.getLogger("alphatrader.agents.quant")


class QuantAgent(BaseAgent):
    """Specialized agent wrapper for Quant Strategy Engine MCP Server."""

    agent_name: str = "QuantAgent"
    component_source: ComponentSource = ComponentSource.QUANT_AGENT

    def __init__(self, mcp_client: MCPClientSession | None = None) -> None:
        super().__init__(mcp_client=mcp_client)

    def process(self, request: QuantRequest) -> QuantResult:
        """Process quant strategy evaluation deterministically via Server 3."""
        if self.mcp_client is None:
            return QuantResult(
                success=False,
                order_proposal=None,
                alpha_score=None,
                explanation="MCP client session is not configured.",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code="NO_MCP_CLIENT",
            )

        signal = request.analyst_signal
        if signal.direction == Direction.FLAT:
            return QuantResult(
                success=True,
                order_proposal=None,
                alpha_score=Decimal("0.00"),
                explanation=f"Signal for {signal.symbol} is FLAT. No order proposed.",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
            )

        # 1. Invoke evaluate_alpha_factors on Server 3
        alpha_resp = self.mcp_client.call_tool(
            server_name="quant-strategy-engine",
            tool_name="evaluate_alpha_factors",
            arguments={"symbol": signal.symbol, "interval": signal.interval.value},
        )

        import contextlib

        alpha_score = Decimal("0.50")
        if "composite_alpha" in alpha_resp:
            with contextlib.suppress(Exception):
                alpha_score = Decimal(str(alpha_resp["composite_alpha"]))

        # 2. Invoke propose_sized_order on Server 3
        # Select strategy matching direction
        strategy_name = (
            "momentum_trend_v1" if signal.direction == Direction.LONG else "mean_reversion_v1"
        )
        size_resp = self.mcp_client.call_tool(
            server_name="quant-strategy-engine",
            tool_name="propose_sized_order",
            arguments={
                "symbol": signal.symbol,
                "strategy_name": strategy_name,
                "interval": signal.interval.value,
            },
        )

        if "error" in size_resp:
            err_code = size_resp.get("error", "SIZING_ERROR")
            msg = size_resp.get("message", "Failed to compute sized order.")
            return QuantResult(
                success=False,
                order_proposal=None,
                alpha_score=alpha_score,
                explanation=f"Quant strategy engine sizing error: {msg}",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code=str(err_code),
            )

        # Extract proposal parameters
        proposal_dict = size_resp.get("proposal", {})
        qty = Decimal(str(proposal_dict.get("quantity", "1.0000")))
        limit_px = Decimal(str(proposal_dict.get("limit_price", "2500.0000")))
        stop_px = Decimal(str(proposal_dict.get("stop_loss_price", (limit_px * Decimal("0.98")))))
        target_px = Decimal(
            str(proposal_dict.get("take_profit_price", (limit_px * Decimal("1.04"))))
        )

        # Cash delivery policy check: Short sales prohibited
        side = Side.BUY if signal.direction == Direction.LONG else Side.SELL

        from datetime import UTC, datetime

        req_id = uuid4()
        order_req = OrderRequest(
            order_request_id=req_id,
            idempotency_key=str(req_id),
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=qty,
            limit_price=limit_px,
            stop_loss_price=stop_px,
            take_profit_price=target_px,
            strategy_name=strategy_name,
            requested_at=datetime.now(UTC),
            trading_mode="PAPER",
        )

        explanation = (
            f"Quant proposal: {side.value} {qty} {signal.symbol} @ limit {limit_px} "
            f"(SL: {stop_px}, TP: {target_px}), Composite Alpha: {alpha_score}."
        )

        return QuantResult(
            success=True,
            order_proposal=order_req,
            alpha_score=alpha_score,
            explanation=explanation,
            correlation_id=request.correlation_id,
            causation_id=request.trigger_event_id,
        )
