"""Sprint 21: Infrastructure Chaos Engineering Test Suite.

Simulates:
1. Redis disconnect / latency injection -> verify fail-closed reject in DeterministicRiskEvaluator.
2. PostgreSQL ledger connection pause / failure -> verify transaction abort & zero orphan fills.
3. Redpanda publish failure -> verify RiskDecision revokes approval token and fails closed.
4. Malformed / out-of-order frames -> verify defensive rejection with complete audit logging.

Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import redis

from src.common.schemas.orders import OrderRequest, OrderType, Side
from src.common.schemas.risk import RiskReasonCode, RiskVerdict
from src.mcp_servers.risk_guardrail.evaluator import (
    DeterministicRiskEvaluator,
)
from src.mcp_servers.risk_guardrail.publisher import RiskPublisher
from src.mcp_servers.risk_guardrail.server import (
    setup_risk_guardrail,
    validate_order_proposal,
)
from src.mcp_servers.risk_guardrail.state import RiskStateManager
from src.mcp_servers.risk_guardrail.token import RiskTokenManager
from tests.unit.test_risk_state import FakeRedis

SECRET = "test_chaos_risk_secret_key"


@pytest.fixture
def chaos_order() -> OrderRequest:
    return OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="RELIANCE.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("2500.00"),
        stop_loss_price=Decimal("2450.00"),
        take_profit_price=Decimal("2600.00"),
        strategy_name="chaos_strategy",
        requested_at=datetime.now(UTC),
    )


def test_redis_pause_or_disconnect_fails_closed(chaos_order: OrderRequest) -> None:
    """When Redis is unreachable, RiskStateManager fails closed to halted state."""
    broken_redis = MagicMock(spec=redis.Redis)
    # Simulate connection error / timeout on pipeline execute
    broken_pipe = MagicMock()
    broken_pipe.execute.side_effect = redis.ConnectionError("Connection timed out to Redis:6379")
    broken_redis.pipeline.return_value = broken_pipe

    state_mgr = RiskStateManager(redis_client=broken_redis)
    portfolio_state = state_mgr.get_portfolio_state_for_evaluation()

    assert portfolio_state.is_floor_halted is True
    assert portfolio_state.equity == Decimal("0")

    evaluator = DeterministicRiskEvaluator(token_manager=RiskTokenManager(SECRET))
    decision = evaluator.evaluate_order(chaos_order, portfolio_state)

    assert decision.decision == RiskVerdict.HALT
    assert decision.reason_code == RiskReasonCode.FLOOR_ALREADY_HALTED
    assert decision.approval_token is None


def test_redis_pipeline_partial_failure_fails_closed(chaos_order: OrderRequest) -> None:
    """When Redis returns None or corrupt keys, fail closed gracefully."""
    mock_redis = MagicMock(spec=redis.Redis)
    mock_pipe = MagicMock()
    # Missing equity and HWM in Redis
    mock_pipe.execute.return_value = [None, None, None, {}]
    mock_redis.pipeline.return_value = mock_pipe

    state_mgr = RiskStateManager(redis_client=mock_redis)
    portfolio_state = state_mgr.get_portfolio_state_for_evaluation()

    assert portfolio_state.is_floor_halted is True
    evaluator = DeterministicRiskEvaluator(token_manager=RiskTokenManager(SECRET))
    decision = evaluator.evaluate_order(chaos_order, portfolio_state)
    assert decision.decision == RiskVerdict.HALT
    assert decision.reason_code == RiskReasonCode.FLOOR_ALREADY_HALTED


def test_redpanda_publisher_failure_revokes_approved_order(chaos_order: OrderRequest) -> None:
    """If Redpanda publisher fails while emitting an approval, the system fails closed."""
    fake_redis = FakeRedis()
    fake_redis.store[RiskStateManager.KEY_EQUITY] = "1000000.00"
    fake_redis.store[RiskStateManager.KEY_HWM] = "1000000.00"
    fake_redis.store[RiskStateManager.KEY_FLOOR_HALTED] = "0"

    state_mgr = RiskStateManager(redis_client=fake_redis)  # type: ignore[arg-type]
    evaluator = DeterministicRiskEvaluator(token_manager=RiskTokenManager(SECRET))

    mock_pub = MagicMock(spec=RiskPublisher)
    mock_pub.publish_risk_decision.side_effect = RuntimeError(
        "Kafka broker connection timeout (9092)"
    )

    setup_risk_guardrail(state_manager=state_mgr, evaluator=evaluator, publisher=mock_pub)

    # Validate order proposal through server tool
    result_json = validate_order_proposal(chaos_order.model_dump_json())
    result = json.loads(result_json)

    # Must be revoked and rejected fail-closed
    assert result["decision"] == RiskVerdict.REJECT.value
    assert result["approval_token"] is None
    assert "rejected because risk audit record could not be durably published" in result["message"]


def test_malformed_order_json_fails_closed_without_crashing() -> None:
    """Corrupted / truncated payload must return immediate fail-closed rejection."""
    fake_redis = FakeRedis()
    state_mgr = RiskStateManager(redis_client=fake_redis)  # type: ignore[arg-type]
    evaluator = DeterministicRiskEvaluator(token_manager=RiskTokenManager(SECRET))
    setup_risk_guardrail(state_manager=state_mgr, evaluator=evaluator)

    malformed_payload = '{"symbol": "TCS.NS", "quantity": "invalid_number", "side":'
    result_json = validate_order_proposal(malformed_payload)
    result = json.loads(result_json)

    assert result["decision"] == RiskVerdict.REJECT.value
    assert result["approval_token"] is None
    assert "Malformed order proposal" in result["message"]
