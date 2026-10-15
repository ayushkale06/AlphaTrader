"""Paper Execution Autonomous Agent.

Invokes MCP Server 5 (paper-execution-broker) to submit risk-approved orders
with cryptographic token verification, statutory tax modeling,
and ambiguous-timeout safe reconciliation.
Governance: ADR-002, ADR-004, ADR-009, ADR-011.
"""

from __future__ import annotations

import contextlib
import logging
from decimal import Decimal
from uuid import UUID

from src.agents.base import BaseAgent, ExecutionRequest, ExecutionResult
from src.agents.mcp_client import MCPClientSession
from src.common.schemas.envelope import ComponentSource
from src.common.schemas.orders import Fill, OrderStatus

logger = logging.getLogger("alphatrader.agents.execution")


class ExecutionAgent(BaseAgent):
    """Specialized agent wrapper for Paper Execution Broker MCP Server."""

    agent_name: str = "ExecutionAgent"
    component_source: ComponentSource = ComponentSource.EXECUTION_AGENT

    def __init__(self, mcp_client: MCPClientSession | None = None) -> None:
        super().__init__(mcp_client=mcp_client)
        # Store dispatched order requests to guard against duplicate submissions
        self._dispatched_orders: dict[UUID, ExecutionResult] = {}

    def process(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute risk-approved paper order deterministically via Server 5."""
        if self.mcp_client is None:
            return ExecutionResult(
                success=False,
                status=OrderStatus.REJECTED,
                fill=None,
                order_id=None,
                fill_id=None,
                statutory_fees=None,
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code="NO_MCP_CLIENT",
            )

        req_id = request.order_proposal.order_request_id

        # 1. Idempotency Check: if order_request_id was already processed, return existing
        if req_id in self._dispatched_orders:
            logger.info(
                "Order request %s already dispatched. Returning cached execution result.", req_id
            )
            return self._dispatched_orders[req_id]

        # Prepare order dictionary matching execute_paper_order tool interface
        order_dict = {
            "order_request_id": str(req_id),
            "symbol": request.order_proposal.symbol,
            "side": request.order_proposal.side.value,
            "order_type": request.order_proposal.order_type.value,
            "quantity": str(request.order_proposal.quantity),
            "limit_price": (
                str(request.order_proposal.limit_price)
                if request.order_proposal.limit_price is not None
                else None
            ),
            "stop_loss_price": (
                str(request.order_proposal.stop_loss_price)
                if request.order_proposal.stop_loss_price is not None
                else None
            ),
            "current_market_price": str(request.current_market_price),
            "correlation_id": request.correlation_id,
        }

        # 2. Invoke execute_paper_order on Server 5
        resp = self.mcp_client.call_tool(
            server_name="paper-execution-broker",
            tool_name="execute_paper_order",
            arguments={"order_dict": order_dict, "risk_token": request.approval_token},
        )

        # 3. Handle transport or server errors with ambiguous outcome reconciliation
        if "error" in resp:
            err_code = resp.get("error", "BROKER_ERROR")

            # Check if error is timeout/connection failure: query get_paper_order_status
            if err_code in ("TIMEOUT", "CONNECTION_ERROR", "TOOL_EXECUTION_ERROR"):
                logger.warning(
                    "Ambiguous execution outcome for request %s: attempting reconciliation.",
                    req_id,
                )
                # Attempt reconciliation if server provides order lookup
                recon_resp = self.mcp_client.call_tool(
                    server_name="paper-execution-broker",
                    tool_name="get_paper_order_status",
                    arguments={"order_id": str(req_id)},
                )
                if "order_id" in recon_resp and recon_resp.get("status") == "FILLED":
                    logger.info("Reconciliation confirmed order %s was filled.", req_id)
                    res = ExecutionResult(
                        success=True,
                        status=OrderStatus.FILLED,
                        fill=None,
                        order_id=UUID(str(recon_resp["order_id"])),
                        fill_id=None,
                        statutory_fees=None,
                        correlation_id=request.correlation_id,
                        causation_id=request.trigger_event_id,
                    )
                    self._dispatched_orders[req_id] = res
                    return res

            return ExecutionResult(
                success=False,
                status=OrderStatus.REJECTED,
                fill=None,
                order_id=None,
                fill_id=None,
                statutory_fees=None,
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code=str(err_code),
            )

        # 4. Parse execution result
        is_success = resp.get("success", False)
        status_str = resp.get("status", "REJECTED")
        status = OrderStatus.FILLED if status_str == "FILLED" else OrderStatus.REJECTED

        order_id_val = UUID(str(resp["order_id"])) if "order_id" in resp else None
        fill_id_val = UUID(str(resp["fill_id"])) if "fill_id" in resp else None

        # Parse statutory fees
        fees_dict: dict[str, Decimal] = {}
        if "statutory_fees" in resp and isinstance(resp["statutory_fees"], dict):
            for k, v in resp["statutory_fees"].items():
                with contextlib.suppress(Exception):
                    fees_dict[k] = Decimal(str(v))

        # Construct Fill if filled
        fill: Fill | None = None
        if status == OrderStatus.FILLED and fill_id_val and order_id_val:
            from datetime import datetime

            fill_time_str = resp.get("filled_at")
            fill_time = (
                datetime.fromisoformat(fill_time_str)
                if fill_time_str
                else request.order_proposal.requested_at
            )

            fill = Fill(
                fill_id=fill_id_val,
                order_id=order_id_val,
                order_request_id=req_id,
                symbol=request.order_proposal.symbol,
                side=request.order_proposal.side,
                fill_price=Decimal(str(resp.get("fill_price", request.current_market_price))),
                fill_quantity=Decimal(
                    str(resp.get("fill_quantity", request.order_proposal.quantity))
                ),
                stt_tax=fees_dict.get("stt_tax", Decimal("0.0000")),
                exchange_fees=fees_dict.get("exchange_fees", Decimal("0.0000")),
                stamp_duty=fees_dict.get("stamp_duty", Decimal("0.0000")),
                gst_tax=fees_dict.get("gst_tax", Decimal("0.0000")),
                total_fees=fees_dict.get("total_fees", Decimal("0.0000")),
                slippage_bps=Decimal(str(resp.get("slippage_bps", "0.0000"))),
                filled_at=fill_time,
                trading_mode="PAPER",
            )

        err_msg = resp.get("reason") if not is_success else None

        result = ExecutionResult(
            success=is_success,
            status=status,
            fill=fill,
            order_id=order_id_val,
            fill_id=fill_id_val,
            statutory_fees=fees_dict if fees_dict else None,
            correlation_id=request.correlation_id,
            causation_id=request.trigger_event_id,
            error_code=err_msg,
        )

        self._dispatched_orders[req_id] = result
        return result
