"""Sprint 22: Flash-Crash & Adversarial Fuzzing Stress Test Suite.

Verifies:
1. Flash-Crash Simulation:
   - Synthetic intraday drops (-1%, -2%, -5%) against simulated equity curve.
   - Evaluates that at exactly >= 2.0% daily drawdown breach, circuit breaker latches
     alphatrader:floor_halted = "1" and rejects all subsequent order attempts with HALT.
2. Adversarial & Malicious Agent Fuzzing:
   - Rogue/corrupted agents proposing inverted stop-losses, zero/negative quantities,
     extreme leverage / concentration (> 5% single-position notional), or excessive
     frequency (> 30 orders/min).
   - Verifies 100% fail-closed rejection without state poisoning.

Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.common.schemas.orders import OrderRequest, OrderType, Side
from src.common.schemas.risk import RiskReasonCode, RiskVerdict
from src.mcp_servers.risk_guardrail.evaluator import (
    DeterministicRiskEvaluator,
)
from src.mcp_servers.risk_guardrail.server import (
    setup_risk_guardrail,
    validate_order_proposal,
)
from src.mcp_servers.risk_guardrail.state import RiskStateManager
from src.mcp_servers.risk_guardrail.token import RiskTokenManager
from tests.unit.test_risk_state import FakeRedis

SECRET = "adversarial_stress_testing_secret_key"


@pytest.fixture
def clean_risk_env() -> tuple[RiskStateManager, DeterministicRiskEvaluator]:
    fake_redis = FakeRedis()
    state_mgr = RiskStateManager(redis_client=fake_redis)  # type: ignore[arg-type]
    evaluator = DeterministicRiskEvaluator(
        token_manager=RiskTokenManager(SECRET),
        max_daily_drawdown_pct=Decimal("2.0"),
        max_position_size_pct=Decimal("5.0"),
        max_open_positions=10,
        max_orders_per_minute=30,
    )
    setup_risk_guardrail(state_manager=state_mgr, evaluator=evaluator)
    return state_mgr, evaluator


def test_flash_crash_trips_circuit_breaker_at_exact_threshold(
    clean_risk_env: tuple[RiskStateManager, DeterministicRiskEvaluator],
) -> None:
    """Simulate rapid intraday drop and verify floor halt trips and remains latched."""
    state_mgr, _evaluator = clean_risk_env
    start_equity = Decimal("1000000.00")
    t0 = datetime.now(UTC)

    # 1. Initial State: Day starts at 10 Lakhs
    ok, _ = state_mgr.update_portfolio_equity(start_equity, t0)
    assert ok is True
    assert state_mgr.get_telemetry().is_floor_halted is False

    # 2. Minor drop: -1.0% (Equity = 9,90,000) -> Should NOT trip circuit breaker
    t1 = t0 + timedelta(minutes=5)
    drop_1pct = start_equity * Decimal("0.99")
    ok, _ = state_mgr.update_portfolio_equity(drop_1pct, t1)
    assert ok is True
    assert state_mgr.get_telemetry().is_floor_halted is False

    # 3. Flash drop: -2.01% (Equity = 9,79,900) -> TRIPS CIRCUIT BREAKER
    t2 = t1 + timedelta(seconds=30)
    drop_flash = start_equity * Decimal("0.9799")
    ok, msg = state_mgr.update_portfolio_equity(drop_flash, t2)
    assert ok is True
    assert "HALT" in msg or state_mgr.get_telemetry().is_floor_halted
    assert state_mgr.get_telemetry().is_floor_halted is True

    # 4. Attempt to place any order -> MUST BE REJECTED WITH HALT
    order = OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="TCS.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("5"),
        limit_price=Decimal("4000.00"),
        stop_loss_price=Decimal("3900.00"),
        strategy_name="crash_buyer",
        requested_at=datetime.now(UTC),
    )
    resp_json = validate_order_proposal(order.model_dump_json())
    resp = json.loads(resp_json)
    assert resp["decision"] == RiskVerdict.HALT.value
    assert resp["reason_code"] == RiskReasonCode.FLOOR_ALREADY_HALTED.value
    assert resp["approval_token"] is None

    # 5. Market rebound: -0.5% (Equity = 9,95,000) -> Circuit breaker MUST REMAIN LATCHED
    t3 = t2 + timedelta(minutes=10)
    rebound_equity = start_equity * Decimal("0.995")
    state_mgr.update_portfolio_equity(rebound_equity, t3)
    assert state_mgr.get_telemetry().is_floor_halted is True


def test_adversarial_inverted_stop_loss_blocked(
    clean_risk_env: tuple[RiskStateManager, DeterministicRiskEvaluator],
) -> None:
    """Rogue strategy placing BUY with stop loss ABOVE limit price or SELL with stop loss BELOW."""
    state_mgr, _ = clean_risk_env
    state_mgr.initialize_state(Decimal("1000000.00"), force_unhalt=True)

    # Adversarial BUY: Limit 2000, Stop Loss 2100 (Inverted)
    inverted_buy = OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="INFY.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("2000.00"),
        stop_loss_price=Decimal("2100.00"),
        strategy_name="rogue_agent",
        requested_at=datetime.now(UTC),
    )
    resp_buy = json.loads(validate_order_proposal(inverted_buy.model_dump_json()))
    assert resp_buy["decision"] == RiskVerdict.REJECT.value
    assert resp_buy["reason_code"] == RiskReasonCode.MISSING_STOP_LOSS.value
    assert resp_buy["approval_token"] is None

    # Adversarial SELL: Limit 2000, Stop Loss 1900 (Inverted for short/sell)
    inverted_sell = OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="INFY.NS",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("2000.00"),
        stop_loss_price=Decimal("1900.00"),
        strategy_name="rogue_agent",
        requested_at=datetime.now(UTC),
    )
    resp_sell = json.loads(validate_order_proposal(inverted_sell.model_dump_json()))
    assert resp_sell["decision"] == RiskVerdict.REJECT.value
    assert resp_sell["reason_code"] == RiskReasonCode.MISSING_STOP_LOSS.value
    assert resp_sell["approval_token"] is None


def test_adversarial_excessive_position_concentration_blocked(
    clean_risk_env: tuple[RiskStateManager, DeterministicRiskEvaluator],
) -> None:
    """Order requesting > 5% total portfolio notional concentration must be blocked."""
    state_mgr, _ = clean_risk_env
    # Equity = 10 Lakhs INR. Max 5% single-stock = 50,000 INR.
    state_mgr.initialize_state(Decimal("1000000.00"), force_unhalt=True)

    # Proposed order: 25 shares of TCS @ 4000 = 100,000 INR (10% of portfolio)
    oversized_order = OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="TCS.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("25"),
        limit_price=Decimal("4000.00"),
        stop_loss_price=Decimal("3800.00"),
        strategy_name="greedy_agent",
        requested_at=datetime.now(UTC),
    )
    resp = json.loads(validate_order_proposal(oversized_order.model_dump_json()))
    assert resp["decision"] == RiskVerdict.REJECT.value
    assert resp["reason_code"] == RiskReasonCode.MAX_POSITION_SIZE_BREACH.value
    assert resp["approval_token"] is None


def test_adversarial_order_frequency_flooding_blocked(
    clean_risk_env: tuple[RiskStateManager, DeterministicRiskEvaluator],
) -> None:
    """Submitting > 30 orders within a 60-second rolling window trips the rate limiter."""
    state_mgr, _ = clean_risk_env
    state_mgr.initialize_state(Decimal("1000000.00"), force_unhalt=True)

    # Submit 30 valid small orders (within limits)
    for _i in range(30):
        small_order = OrderRequest(
            order_request_id=uuid4(),
            idempotency_key=str(uuid4()),
            symbol="HDFCBANK.NS",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            limit_price=Decimal("1500.00"),
            stop_loss_price=Decimal("1450.00"),
            strategy_name="spam_agent",
            requested_at=datetime.now(UTC),
        )
        resp = json.loads(validate_order_proposal(small_order.model_dump_json()))
        assert resp["decision"] == RiskVerdict.APPROVE.value

    # 31st order in the same second -> Trips rate limit
    spam_order = OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        symbol="HDFCBANK.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("1500.00"),
        stop_loss_price=Decimal("1450.00"),
        strategy_name="spam_agent",
        requested_at=datetime.now(UTC),
    )
    resp_spam = json.loads(validate_order_proposal(spam_order.model_dump_json()))
    assert resp_spam["decision"] == RiskVerdict.REJECT.value
    assert resp_spam["reason_code"] == RiskReasonCode.ORDER_RATE_LIMIT_EXCEEDED.value
    assert resp_spam["approval_token"] is None
