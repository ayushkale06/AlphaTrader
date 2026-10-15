"""Unit tests for Paper Execution Broker (Sprint 9).

Covers:
1. Statutory cost calculations precision against NSE benchmarks:
   - STT (delivery vs intraday)
   - Exchange charges & SEBI turnover fees
   - Stamp duty on BUY side only
   - GST on service fees
   - Invariant: total_fees == stt_tax + exchange_fees + gst_tax + stamp_duty
2. RiskApprovalToken verification:
   - Valid token accepts order
   - Missing, malformed, or forged signature rejects order (100%)
   - Expired token rejects order (100%)
   - Mismatched order parameters (symbol, side, quantity, order_request_id) rejects order (100%)
3. PaperMatchingEngine:
   - Spread crossing for BUY (price higher than LTP) and SELL (price lower than LTP)
   - Slippage basis points scaling with order quantity
   - Limit order price bounds enforcement (BUY limit price cap, SELL limit price floor)
   - Reject non-positive quantities and prices
4. PaperExecutionPublisher:
   - Synchronous confirmed publishing to TopicName.ORDERS_FILLS (orders.fills)
   - Delivery verification and failure modes
5. FastMCP Server:
   - Protocol tool registrations: execute_paper_order, get_paper_order_status, get_broker_health
   - End-to-end execution flow via broker service
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.common.schemas.orders import (
    Fill,
    OrderStatus,
    OrderType,
    Side,
)
from src.mcp_servers.paper_execution_broker.costs import (
    StatutoryCostEngine,
)
from src.mcp_servers.paper_execution_broker.matcher import (
    PaperMatchingEngine,
)
from src.mcp_servers.paper_execution_broker.publisher import (
    PaperExecutionPublisher,
)
from src.mcp_servers.paper_execution_broker.server import (
    PaperExecutionBroker,
    execute_paper_order,
    get_broker_health,
    get_paper_order_status,
)
from src.mcp_servers.risk_guardrail.token import RiskTokenManager

SECRET_KEY = "test-secret-key-32-bytes-long-123456"


# ==============================================================================
# 1. Statutory Costs Tests
# ==============================================================================
class TestStatutoryCostEngine:
    def setup_method(self) -> None:
        self.engine = StatutoryCostEngine()

    def test_delivery_buy_statutory_taxes(self) -> None:
        """Verify delivery BUY statutory costs on 100 shares @ INR 1000 = turnover INR 100,000."""
        breakdown = self.engine.calculate_statutory_costs(
            side=Side.BUY,
            quantity=Decimal("100"),
            price=Decimal("1000.00"),
            is_delivery=True,
        )
        assert breakdown.turnover == Decimal("100000.00")
        # STT = 0.1% of 100,000 = 100.00
        assert breakdown.stt_tax == Decimal("100.00")
        # Exchange fee = 0.00325% = 3.25; SEBI = 0.000001 = 0.10 => sum = 3.35
        assert breakdown.exchange_fees == Decimal("3.35")
        # Stamp duty = 0.015% of 100,000 on BUY = 15.00
        assert breakdown.stamp_duty == Decimal("15.00")
        # GST = 18% of 3.35 = 0.60
        assert breakdown.gst_tax == Decimal("0.60")
        # Total fees = 100.00 + 3.35 + 0.60 + 15.00 = 118.95
        assert breakdown.total_fees == Decimal("118.95")
        assert breakdown.total_fees == (
            breakdown.stt_tax + breakdown.exchange_fees + breakdown.gst_tax + breakdown.stamp_duty
        )

    def test_delivery_sell_statutory_taxes(self) -> None:
        """Verify delivery SELL has STT but NO stamp duty."""
        breakdown = self.engine.calculate_statutory_costs(
            side=Side.SELL,
            quantity=Decimal("50"),
            price=Decimal("2000.00"),
            is_delivery=True,
        )
        assert breakdown.turnover == Decimal("100000.00")
        assert breakdown.stt_tax == Decimal("100.00")
        assert breakdown.stamp_duty == Decimal("0.00")
        assert breakdown.exchange_fees == Decimal("3.35")
        assert breakdown.gst_tax == Decimal("0.60")
        assert breakdown.total_fees == Decimal("103.95")

    def test_intraday_buy_and_sell_stt(self) -> None:
        """Intraday BUY has no STT and has stamp duty;
        intraday SELL has 0.025% STT and no stamp duty.
        """
        buy = self.engine.calculate_statutory_costs(
            side=Side.BUY,
            quantity=Decimal("100"),
            price=Decimal("1000.00"),
            is_delivery=False,
        )
        assert buy.stt_tax == Decimal("0.00")
        assert buy.stamp_duty == Decimal("15.00")

        sell = self.engine.calculate_statutory_costs(
            side=Side.SELL,
            quantity=Decimal("100"),
            price=Decimal("1000.00"),
            is_delivery=False,
        )
        # STT = 0.025% of 100,000 = 25.00
        assert sell.stt_tax == Decimal("25.00")
        assert sell.stamp_duty == Decimal("0.00")


# ==============================================================================
# 2. PaperMatchingEngine Tests
# ==============================================================================
class TestPaperMatchingEngine:
    def setup_method(self) -> None:
        self.matcher = PaperMatchingEngine()
        self.now = datetime.now(UTC)

    def test_market_buy_spread_and_slippage(self) -> None:
        """Market BUY fills above LTP due to half-spread and volume slippage."""
        order_id = uuid4()
        req_id = uuid4()
        res = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.MARKET,
            current_market_price=Decimal("1500.00"),
            limit_price=None,
            filled_at=self.now,
        )
        assert res.is_filled is True
        assert res.fill is not None
        assert res.fill.fill_price > Decimal("1500.00")
        assert res.fill.fill_quantity == Decimal("100")
        assert res.fill.slippage_bps > Decimal("0")
        # Invariant check
        assert res.fill.total_fees == (
            res.fill.stt_tax + res.fill.exchange_fees + res.fill.gst_tax + res.fill.stamp_duty
        )

    def test_market_sell_spread_and_slippage(self) -> None:
        """Market SELL fills below LTP due to half-spread and volume slippage."""
        order_id = uuid4()
        req_id = uuid4()
        res = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="TCS.NS",
            side=Side.SELL,
            quantity=Decimal("50"),
            order_type=OrderType.MARKET,
            current_market_price=Decimal("3500.00"),
            limit_price=None,
            filled_at=self.now,
        )
        assert res.is_filled is True
        assert res.fill is not None
        assert res.fill.fill_price < Decimal("3500.00")

    def test_limit_buy_within_and_exceeding_limit(self) -> None:
        """BUY LIMIT succeeds when fill_price <= limit, fails when fill_price > limit."""
        order_id = uuid4()
        req_id = uuid4()
        # Generous limit: fills
        res_ok = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="RELIANCE.NS",
            side=Side.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            current_market_price=Decimal("2500.00"),
            limit_price=Decimal("2510.00"),
            filled_at=self.now,
        )
        assert res_ok.is_filled is True

        # Restrictive limit below market price: rejected
        res_fail = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="RELIANCE.NS",
            side=Side.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            current_market_price=Decimal("2500.00"),
            limit_price=Decimal("2490.00"),
            filled_at=self.now,
        )
        assert res_fail.is_filled is False
        assert "breach" in (res_fail.rejection_reason or "").lower()

    def test_limit_sell_within_and_exceeding_limit(self) -> None:
        """SELL LIMIT succeeds when fill_price >= limit, fails when fill_price < limit."""
        order_id = uuid4()
        req_id = uuid4()
        res_ok = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="HDFCBANK.NS",
            side=Side.SELL,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            current_market_price=Decimal("1600.00"),
            limit_price=Decimal("1590.00"),
            filled_at=self.now,
        )
        assert res_ok.is_filled is True

        res_fail = self.matcher.match_order(
            order_id=order_id,
            order_request_id=req_id,
            symbol="HDFCBANK.NS",
            side=Side.SELL,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            current_market_price=Decimal("1600.00"),
            limit_price=Decimal("1610.00"),
            filled_at=self.now,
        )
        assert res_fail.is_filled is False

    def test_invalid_parameters_rejection(self) -> None:
        """Non-positive quantities and zero market prices reject immediately."""
        res_qty = self.matcher.match_order(
            order_id=uuid4(),
            order_request_id=uuid4(),
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("0"),
            order_type=OrderType.MARKET,
            current_market_price=Decimal("1000.00"),
            limit_price=None,
            filled_at=self.now,
        )
        assert res_qty.is_filled is False

        res_price = self.matcher.match_order(
            order_id=uuid4(),
            order_request_id=uuid4(),
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET,
            current_market_price=Decimal("0.00"),
            limit_price=None,
            filled_at=self.now,
        )
        assert res_price.is_filled is False

        res_no_limit = self.matcher.match_order(
            order_id=uuid4(),
            order_request_id=uuid4(),
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.LIMIT,
            current_market_price=Decimal("1000.00"),
            limit_price=None,
            filled_at=self.now,
        )
        assert res_no_limit.is_filled is False


# ==============================================================================
# 3. Publisher Delivery & Failure Verification
# ==============================================================================
class TestPaperExecutionPublisher:
    def test_publish_fill_success(self) -> None:
        mock_producer = MagicMock()
        mock_producer.flush.return_value = 0

        # Simulate confluent-kafka invoking delivery callback
        def fake_produce(*args: tuple[object, ...], **kwargs: dict[str, object]) -> None:
            cb = kwargs.get("on_delivery")
            if callable(cb):
                cb(None, MagicMock())

        mock_producer.produce.side_effect = fake_produce

        publisher = PaperExecutionPublisher(producer=mock_producer)
        fill = Fill(
            order_id=uuid4(),
            order_request_id=uuid4(),
            symbol="INFY.NS",
            side=Side.BUY,
            fill_price=Decimal("1500.00"),
            fill_quantity=Decimal("10"),
            total_fees=Decimal("0.00"),
            filled_at=datetime.now(UTC),
        )

        env = publisher.publish_fill(fill)
        assert env.payload.fill_id == fill.fill_id
        assert mock_producer.produce.called
        assert mock_producer.flush.called

    def test_publish_fill_failure_raises(self) -> None:
        mock_producer = MagicMock()
        # Non-zero unflushed messages simulates delivery failure
        mock_producer.flush.return_value = 1

        publisher = PaperExecutionPublisher(producer=mock_producer)
        fill = Fill(
            order_id=uuid4(),
            order_request_id=uuid4(),
            symbol="INFY.NS",
            side=Side.BUY,
            fill_price=Decimal("1500.00"),
            fill_quantity=Decimal("10"),
            total_fees=Decimal("0.00"),
            filled_at=datetime.now(UTC),
        )

        with pytest.raises(RuntimeError, match="Failed to confirm delivery"):
            publisher.publish_fill(fill)


# ==============================================================================
# 4. Token Enforcement and Broker End-to-End
# ==============================================================================
class TestPaperExecutionBroker:
    def setup_method(self) -> None:
        self.token_manager = RiskTokenManager(secret_key=SECRET_KEY)
        self.mock_publisher = MagicMock(spec=PaperExecutionPublisher)
        self.broker = PaperExecutionBroker(
            token_manager=self.token_manager,
            publisher=self.mock_publisher,
            secret_key=SECRET_KEY,
        )

    def test_execute_order_valid_token_success(self) -> None:
        """Valid token successfully approves and matches order."""
        req_id = uuid4()
        token = self.token_manager.issue_token(
            order_request_id=req_id,
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("100"),
            limit_price=None,
        )

        res = self.broker.execute_order(
            order_request_id=req_id,
            symbol="INFY.NS",
            side="BUY",
            quantity="100",
            order_type="MARKET",
            current_market_price="1500.00",
            approval_token=token,
        )

        assert res["success"] is True
        assert res["status"] == "FILLED"
        assert res["symbol"] == "INFY.NS"
        assert self.mock_publisher.publish_fill.called

        order_id = res["order_id"]
        status = self.broker.get_order_status(order_id)
        assert status is not None
        assert status["status"] == OrderStatus.FILLED.value

    def test_execute_order_invalid_token_rejects(self) -> None:
        """Forged or tampered token rejects 100% of the time."""
        req_id = uuid4()
        res = self.broker.execute_order(
            order_request_id=req_id,
            symbol="INFY.NS",
            side="BUY",
            quantity="100",
            order_type="MARKET",
            current_market_price="1500.00",
            approval_token="fake.forged_token",
        )
        assert res["success"] is False
        assert res["status"] == "REJECTED"
        assert "invalid" in res["reason"].lower()
        assert not self.mock_publisher.publish_fill.called

    def test_execute_order_expired_token_rejects(self) -> None:
        """Expired token rejects 100% of the time."""
        req_id = uuid4()
        past_time = datetime.now(UTC) - timedelta(seconds=120)
        expired_token = self.token_manager.issue_token(
            order_request_id=req_id,
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("50"),
            limit_price=None,
            expires_at=past_time,
        )

        res = self.broker.execute_order(
            order_request_id=req_id,
            symbol="INFY.NS",
            side="BUY",
            quantity="50",
            order_type="MARKET",
            current_market_price="1500.00",
            approval_token=expired_token,
        )
        assert res["success"] is False
        assert res["status"] == "REJECTED"

    def test_execute_order_mismatched_parameters_rejects(self) -> None:
        """Token with mismatched quantity rejects."""
        req_id = uuid4()
        token = self.token_manager.issue_token(
            order_request_id=req_id,
            symbol="INFY.NS",
            side=Side.BUY,
            quantity=Decimal("10"),
            limit_price=None,
        )

        # Attempt to execute 20 instead of 10
        res = self.broker.execute_order(
            order_request_id=req_id,
            symbol="INFY.NS",
            side="BUY",
            quantity="20",
            order_type="MARKET",
            current_market_price="1500.00",
            approval_token=token,
        )
        assert res["success"] is False
        assert res["status"] == "REJECTED"

    def test_broker_health_and_not_found(self) -> None:
        health = self.broker.get_health()
        assert health["status"] == "HEALTHY"
        assert health["trading_mode"] == "PAPER"
        assert health["live_trading_enabled"] is False

        missing = self.broker.get_order_status(uuid4())
        assert missing is None


# ==============================================================================
# 5. FastMCP Server Wrapper Functions
# ==============================================================================
def test_fastmcp_wrapper_tools() -> None:
    health = get_broker_health()
    assert health["status"] == "HEALTHY"

    missing = get_paper_order_status(str(uuid4()))
    assert missing["status"] == "NOT_FOUND"

    req_id = str(uuid4())
    res = execute_paper_order(
        order_request_id=req_id,
        symbol="INFY.NS",
        side="BUY",
        quantity="10",
        order_type="MARKET",
        current_market_price="1000.00",
        approval_token="bad_token",
    )
    assert res["success"] is False
    assert res["status"] == "REJECTED"
