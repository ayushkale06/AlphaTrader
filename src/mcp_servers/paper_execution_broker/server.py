"""FastMCP Server 5: Paper Execution Broker.

Exposes tools:
1. execute_paper_order:
   - Validates HMAC-SHA256 RiskApprovalToken authenticity, parameters, and expiration.
   - Rejects unapproved or expired tokens 100% of the time.
   - Matches order via PaperMatchingEngine with spread-crossing and slippage.
   - Calculates exact Indian statutory taxes (STT, exchange charges, SEBI fees, stamp duty, GST).
   - Publishes EventEnvelope[Fill] to Kafka topic orders.fills with verified delivery.
   - Stores executed orders in-memory/cache.
2. get_paper_order_status:
   - Returns details of a previously submitted/matched paper order.
3. get_broker_health:
   - Reports broker status, total orders processed, fills executed, and trading mode (PAPER).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer

from src.common.config import get_settings
from src.common.schemas.orders import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Side,
)
from src.mcp_servers.paper_execution_broker.matcher import PaperMatchingEngine
from src.mcp_servers.paper_execution_broker.publisher import PaperExecutionPublisher
from src.mcp_servers.risk_guardrail.token import RiskTokenManager

logger = logging.getLogger("alphatrader.paper_execution_broker.server")

# Instantiate MCPServer
mcp = MCPServer("paper-execution-broker")


class PaperExecutionBroker:
    """Core broker service managing token verification, matching, and Kafka publishing."""

    def __init__(
        self,
        token_manager: RiskTokenManager | None = None,
        matching_engine: PaperMatchingEngine | None = None,
        publisher: PaperExecutionPublisher | None = None,
        secret_key: str | bytes | None = None,
        bootstrap_servers: str | None = None,
    ) -> None:
        settings = get_settings()
        secret_val: Any = secret_key or getattr(
            settings,
            "risk_approval_token_secret",
            "alphatrader-default-risk-token-secret-32-chars",
        )
        resolved_secret: str | bytes = (
            secret_val if isinstance(secret_val, (str, bytes)) else str(secret_val)
        )
        self.token_manager = token_manager or RiskTokenManager(secret_key=resolved_secret)
        self.matching_engine = matching_engine or PaperMatchingEngine()
        self.publisher = publisher or PaperExecutionPublisher(
            bootstrap_servers=bootstrap_servers or settings.kafka_bootstrap_servers
        )
        self.orders: dict[UUID, Order] = {}
        self.orders_by_request_id: dict[UUID, Order] = {}
        self.fills: dict[UUID, Fill] = {}
        self._total_orders_received = 0
        self._total_fills_executed = 0
        self._total_rejections = 0

    def execute_order(
        self,
        order_request_id: UUID | str,
        symbol: str,
        side: Side | str,
        quantity: Decimal | str | int,
        order_type: OrderType | str,
        current_market_price: Decimal | str | float,
        approval_token: str,
        limit_price: Decimal | str | float | None = None,
        stop_loss_price: Decimal | str | float | None = None,
        correlation_id: str | None = None,
        trigger_event_id: UUID | str | None = None,
    ) -> dict[str, Any]:
        """Execute a candidate paper order proposal protected by RiskApprovalToken."""
        self._total_orders_received += 1
        now = datetime.now(UTC)
        req_id = (
            UUID(str(order_request_id))
            if not isinstance(order_request_id, UUID)
            else order_request_id
        )
        trig_id = (
            UUID(str(trigger_event_id))
            if trigger_event_id is not None and not isinstance(trigger_event_id, UUID)
            else trigger_event_id
        )
        side_enum = Side(side) if not isinstance(side, Side) else side
        type_enum = OrderType(order_type) if not isinstance(order_type, OrderType) else order_type
        qty_dec = Decimal(str(quantity))
        ltp_dec = Decimal(str(current_market_price))
        lmt_dec = Decimal(str(limit_price)) if limit_price is not None else None
        sl_dec = Decimal(str(stop_loss_price)) if stop_loss_price is not None else Decimal("1.00")

        order_id = uuid4()

        # Step 1: Verify RiskApprovalToken
        try:
            self.token_manager.verify_token(
                token=approval_token,
                expected_order_request_id=req_id,
                expected_symbol=symbol,
                expected_side=side_enum,
                expected_quantity=qty_dec,
                check_expiration=True,
                current_time=now,
            )
        except Exception as e:
            self._total_rejections += 1
            logger.warning("Paper order rejected due to token verification failure: %s", e)
            rejected_order = Order(
                order_id=order_id,
                order_request_id=req_id,
                idempotency_key=str(req_id),
                symbol=symbol,
                side=side_enum,
                order_type=type_enum,
                quantity=qty_dec,
                filled_quantity=Decimal("0"),
                status=OrderStatus.REJECTED,
                limit_price=lmt_dec,
                stop_loss_price=sl_dec,
                approval_token=approval_token,
                created_at=now,
                updated_at=now,
                trading_mode="PAPER",
            )
            self.orders[order_id] = rejected_order
            self.orders_by_request_id[req_id] = rejected_order
            return {
                "success": False,
                "order_id": str(order_id),
                "status": "REJECTED",
                "reason": f"Risk approval token invalid: {e}",
            }

        # Step 2: Match order through simulated matching engine
        match_res = self.matching_engine.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol=symbol,
            side=side_enum,
            quantity=qty_dec,
            order_type=type_enum,
            current_market_price=ltp_dec,
            limit_price=lmt_dec,
            filled_at=now,
            is_delivery=True,
        )

        if not match_res.is_filled or match_res.fill is None:
            self._total_rejections += 1
            reason = match_res.rejection_reason or "Order matching condition not met"
            cancelled_order = Order(
                order_id=order_id,
                order_request_id=req_id,
                idempotency_key=str(req_id),
                symbol=symbol,
                side=side_enum,
                order_type=type_enum,
                quantity=qty_dec,
                filled_quantity=Decimal("0"),
                status=OrderStatus.CANCELLED,
                limit_price=lmt_dec,
                stop_loss_price=sl_dec,
                approval_token=approval_token,
                created_at=now,
                updated_at=now,
                trading_mode="PAPER",
            )
            self.orders[order_id] = cancelled_order
            self.orders_by_request_id[req_id] = cancelled_order
            return {
                "success": False,
                "order_id": str(order_id),
                "status": "CANCELLED",
                "reason": reason,
            }

        fill = match_res.fill

        # Step 3: Verified Publish to Kafka orders.fills
        self.publisher.publish_fill(
            fill=fill,
            correlation_id=correlation_id or str(req_id),
            causation_id=trig_id or order_id,
        )

        # Step 4: Record Order and Fill in state
        filled_order = Order(
            order_id=order_id,
            order_request_id=req_id,
            idempotency_key=str(req_id),
            symbol=symbol,
            side=side_enum,
            order_type=type_enum,
            quantity=qty_dec,
            filled_quantity=qty_dec,
            average_fill_price=fill.fill_price,
            status=OrderStatus.FILLED,
            limit_price=lmt_dec,
            stop_loss_price=sl_dec,
            approval_token=approval_token,
            created_at=now,
            updated_at=now,
            trading_mode="PAPER",
        )
        self.orders[order_id] = filled_order
        self.orders_by_request_id[req_id] = filled_order
        self.fills[fill.fill_id] = fill
        self._total_fills_executed += 1

        return {
            "success": True,
            "order_id": str(order_id),
            "fill_id": str(fill.fill_id),
            "status": "FILLED",
            "symbol": fill.symbol,
            "side": fill.side.value,
            "fill_price": str(fill.fill_price),
            "fill_quantity": str(fill.fill_quantity),
            "slippage_bps": str(fill.slippage_bps),
            "statutory_fees": {
                "stt_tax": str(fill.stt_tax),
                "exchange_fees": str(fill.exchange_fees),
                "gst_tax": str(fill.gst_tax),
                "stamp_duty": str(fill.stamp_duty),
                "total_fees": str(fill.total_fees),
            },
            "filled_at": fill.filled_at.isoformat(),
            "trading_mode": "PAPER",
        }

    def get_order_status(self, order_id: UUID | str) -> dict[str, Any] | None:
        """Lookup order by UUID (supports broker order_id or order_request_id)."""
        oid = UUID(str(order_id)) if not isinstance(order_id, UUID) else order_id
        order = self.orders.get(oid) or self.orders_by_request_id.get(oid)
        if order is None:
            return None
        return {
            "order_id": str(order.order_id),
            "order_request_id": str(order.order_request_id),
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": str(order.quantity),
            "filled_quantity": str(order.filled_quantity),
            "average_fill_price": (
                str(order.average_fill_price) if order.average_fill_price else None
            ),
            "status": order.status.value,
            "trading_mode": order.trading_mode,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
        }

    def get_health(self) -> dict[str, Any]:
        """Return operational health of the Paper Execution Broker."""
        return {
            "status": "HEALTHY",
            "trading_mode": "PAPER",
            "live_trading_enabled": False,
            "total_orders_received": self._total_orders_received,
            "total_fills_executed": self._total_fills_executed,
            "total_rejections": self._total_rejections,
        }


# Global broker singleton for FastMCP server
broker_service = PaperExecutionBroker()


@mcp.tool()
def execute_paper_order(
    order_request_id: str,
    symbol: str,
    side: str,
    quantity: str,
    order_type: str,
    current_market_price: str,
    approval_token: str,
    limit_price: str | None = None,
    stop_loss_price: str | None = None,
    correlation_id: str | None = None,
    trigger_event_id: str | None = None,
) -> dict[str, Any]:
    """Execute a paper order after verifying RiskApprovalToken and statutory costs."""
    return broker_service.execute_order(
        order_request_id=order_request_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        current_market_price=current_market_price,
        approval_token=approval_token,
        limit_price=limit_price,
        stop_loss_price=stop_loss_price,
        correlation_id=correlation_id,
        trigger_event_id=trigger_event_id,
    )


@mcp.tool()
def get_paper_order_status(order_id: str) -> dict[str, Any]:
    """Get the current status and fill summary of a paper order."""
    status = broker_service.get_order_status(order_id)
    if status is None:
        return {"status": "NOT_FOUND", "order_id": order_id}
    return status


@mcp.tool()
def get_broker_health() -> dict[str, Any]:
    """Check the health and operational metrics of the Paper Execution Broker."""
    return broker_service.get_health()


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
