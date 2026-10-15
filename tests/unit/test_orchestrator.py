"""Unit tests for LangGraph Stateful Multi-Agent Orchestrator (Sprint 12).

Tests:
1. End-to-end full execution (Analyst -> Quant -> Risk -> Execution -> Filled).
2. Neutral / FLAT signal termination (Analyst -> Completed without trade).
3. Zero-sizing / no edge proposal termination (Quant -> Completed without trade).
4. Risk Guardrail rejection branch (Risk -> Rejected with audit reasons).
5. Floor circuit breaker halt branch (Active Redis latch -> Floor Halted).
6. Execution rejection branch (ExecutionAgent returns error -> Rejected).
7. Replay / checkpoint save and load idempotency.
8. Non-paper trading mode rejection invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.agents.analyst.agent import AnalystAgent
from src.agents.base import (
    AnalystResult,
    ExecutionResult,
    QuantResult,
    RiskResult,
)
from src.agents.execution.agent import ExecutionAgent
from src.agents.mcp_client import InMemoryMCPClientSession
from src.agents.quant.agent import QuantAgent
from src.agents.risk.agent import RiskAgent
from src.common.schemas.market import CandleInterval
from src.common.schemas.orders import Fill, OrderRequest, OrderStatus, OrderType, Side
from src.common.schemas.risk import RiskDecision
from src.common.schemas.signals import Direction, TechnicalSignal
from src.orchestrator.checkpoint import OrchestratorCheckpointer
from src.orchestrator.graph import TradingFloorOrchestrator
from src.orchestrator.state import OrchestratorStage, TradingFloorState


class FakeRedis:
    def __init__(self, halted: bool = False) -> None:
        self.store: dict[str, Any] = {}
        if halted:
            self.store["alphatrader:floor_halted"] = "true"

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self.store[key] = value
        return True


def make_dummy_signal(direction: Direction = Direction.LONG) -> TechnicalSignal:
    n_long = ["EMA_BULLISH", "RSI_MOMENTUM"] if direction == Direction.LONG else []
    n_short = ["EMA_BEARISH"] if direction == Direction.SHORT else []
    if direction == Direction.FLAT:
        n_long = ["EMA_BULLISH"]
        n_short = ["EMA_BEARISH"]

    return TechnicalSignal(
        signal_id=uuid4(),
        symbol="RELIANCE.NS",
        evaluated_at=datetime.now(UTC),
        interval=CandleInterval.FIVE_MINUTES,
        direction=direction,
        confidence=Decimal("1.00") if direction != Direction.FLAT else Decimal("0.50"),
        indicators={"rsi_14": Decimal("65.5000"), "atr_14": Decimal("25.0000")},
        rules_long=n_long,
        rules_short=n_short,
        config_version="1.0.0",
        config_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def make_dummy_order() -> OrderRequest:
    return OrderRequest(
        order_request_id=uuid4(),
        idempotency_key=str(uuid4()),
        strategy_name="intraday_momentum",
        symbol="RELIANCE.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("2500.00"),
        stop_loss_price=Decimal("2450.00"),
        requested_at=datetime.now(UTC),
        trading_mode="PAPER",
    )


def make_dummy_fill(order_req: OrderRequest) -> Fill:
    stt = Decimal("25.0000")
    exch = Decimal("0.8500")
    gst = Decimal("0.8485")
    stamp = Decimal("3.7500")
    return Fill(
        fill_id=uuid4(),
        order_id=uuid4(),
        order_request_id=order_req.order_request_id,
        symbol=order_req.symbol,
        side=order_req.side,
        fill_price=Decimal("2500.00"),
        fill_quantity=order_req.quantity,
        slippage_bps=Decimal("2.5000"),
        stt_tax=stt,
        exchange_fees=exch,
        gst_tax=gst,
        stamp_duty=stamp,
        total_fees=stt + exch + gst + stamp,
        filled_at=datetime.now(UTC),
    )


# =============================================================================
# Unit Tests
# =============================================================================
def test_orchestrator_end_to_end_success() -> None:
    """Verify standard happy-path from tick ingestion to paper fill."""
    mock_mcp = InMemoryMCPClientSession()
    fake_redis = FakeRedis(halted=False)
    checkpointer = OrchestratorCheckpointer(redis_client=fake_redis)

    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)
    risk_agent = MagicMock(spec=RiskAgent)
    exec_agent = MagicMock(spec=ExecutionAgent)

    signal = make_dummy_signal(Direction.LONG)
    order = make_dummy_order()
    fill = make_dummy_fill(order)

    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Bullish momentum",
        correlation_id="corr-1",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=True,
        order_proposal=order,
        alpha_score=Decimal("0.75"),
        explanation="Alpha score high",
        correlation_id="corr-1",
        causation_id=signal.signal_id,
    )
    risk_agent.process.return_value = RiskResult(
        success=True,
        approved=True,
        risk_decision=None,
        approval_token="HMAC-TEST-TOKEN",
        rejection_reasons=[],
        correlation_id="corr-1",
        causation_id=order.order_request_id,
    )
    exec_agent.process.return_value = ExecutionResult(
        success=True,
        status=OrderStatus.FILLED,
        fill=fill,
        order_id=fill.order_id,
        fill_id=fill.fill_id,
        statutory_fees={"total_fees": fill.total_fees},
        correlation_id="corr-1",
        causation_id=order.order_request_id,
    )

    orch = TradingFloorOrchestrator(
        mcp_client=mock_mcp,
        redis_client=fake_redis,
        checkpointer=checkpointer,
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
        risk_agent=risk_agent,
        execution_agent=exec_agent,
    )

    initial_state = TradingFloorState(
        correlation_id="corr-1",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    final_state = orch.run(initial_state)

    assert final_state.stage == OrchestratorStage.COMPLETED
    assert final_state.analyst_signal is not None
    assert final_state.quant_order is not None
    assert final_state.approval_token == "HMAC-TEST-TOKEN"
    assert final_state.execution_status == OrderStatus.FILLED
    assert final_state.fill == fill

    # Checkpoint verification
    saved = checkpointer.load("corr-1")
    assert saved is not None
    assert saved["stage"] == "COMPLETED"


def test_orchestrator_floor_halted_branch() -> None:
    """Verify floor halt circuit breaker immediately short-circuits traversal."""
    fake_redis = FakeRedis(halted=True)
    orch = TradingFloorOrchestrator(redis_client=fake_redis)

    initial_state = TradingFloorState(
        correlation_id="corr-halt",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    final_state = orch.run(initial_state)

    assert final_state.stage == OrchestratorStage.FLOOR_HALTED
    assert final_state.floor_halted is True
    assert "Trading floor circuit breaker active." in final_state.rejection_reasons


def test_orchestrator_flat_signal_branch() -> None:
    """Verify neutral / FLAT regime terminates without proceeding to quant sizing."""
    mock_mcp = InMemoryMCPClientSession()
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)

    flat_signal = make_dummy_signal(Direction.FLAT)
    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=flat_signal,
        regime=None,
        explanation="Flat regime",
        correlation_id="corr-flat",
        causation_id=uuid4(),
    )

    orch = TradingFloorOrchestrator(
        mcp_client=mock_mcp,
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
    )

    state = TradingFloorState(
        correlation_id="corr-flat",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="TCS.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("3500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.COMPLETED
    assert res.quant_order is None
    quant_agent.process.assert_not_called()


def test_orchestrator_quant_zero_sizing_branch() -> None:
    """Verify zero allocation proposal terminates without calling risk agent."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)
    risk_agent = MagicMock(spec=RiskAgent)

    signal = make_dummy_signal(Direction.LONG)
    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Long trend",
        correlation_id="corr-no-size",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=True,
        order_proposal=None,
        alpha_score=Decimal("0.10"),
        explanation="Zero size",
        correlation_id="corr-no-size",
        causation_id=signal.signal_id,
    )

    orch = TradingFloorOrchestrator(
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
        risk_agent=risk_agent,
    )

    state = TradingFloorState(
        correlation_id="corr-no-size",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.COMPLETED
    assert res.quant_order is None
    risk_agent.process.assert_not_called()


def test_orchestrator_risk_rejection_branch() -> None:
    """Verify risk rejection logs reasons and transitions to REJECTED."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)
    risk_agent = MagicMock(spec=RiskAgent)
    exec_agent = MagicMock(spec=ExecutionAgent)

    signal = make_dummy_signal(Direction.LONG)
    order = make_dummy_order()

    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Long trend",
        correlation_id="corr-risk-rej",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=True,
        order_proposal=order,
        alpha_score=Decimal("0.70"),
        explanation="Alpha score high",
        correlation_id="corr-risk-rej",
        causation_id=signal.signal_id,
    )
    risk_agent.process.return_value = RiskResult(
        success=True,
        approved=False,
        risk_decision=None,
        approval_token=None,
        rejection_reasons=["EXCEEDS_SINGLE_POSITION_LIMIT"],
        correlation_id="corr-risk-rej",
        causation_id=order.order_request_id,
    )

    orch = TradingFloorOrchestrator(
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
        risk_agent=risk_agent,
        execution_agent=exec_agent,
    )

    state = TradingFloorState(
        correlation_id="corr-risk-rej",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.REJECTED
    assert "EXCEEDS_SINGLE_POSITION_LIMIT" in res.rejection_reasons
    exec_agent.process.assert_not_called()


def test_orchestrator_execution_rejection_branch() -> None:
    """Verify execution rejection transitions state to REJECTED."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)
    risk_agent = MagicMock(spec=RiskAgent)
    exec_agent = MagicMock(spec=ExecutionAgent)

    signal = make_dummy_signal(Direction.LONG)
    order = make_dummy_order()

    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Long trend",
        correlation_id="corr-exec-rej",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=True,
        order_proposal=order,
        alpha_score=Decimal("0.70"),
        explanation="Alpha score high",
        correlation_id="corr-exec-rej",
        causation_id=signal.signal_id,
    )
    risk_agent.process.return_value = RiskResult(
        success=True,
        approved=True,
        risk_decision=None,
        approval_token="HMAC-TOKEN",
        rejection_reasons=[],
        correlation_id="corr-exec-rej",
        causation_id=order.order_request_id,
    )
    exec_agent.process.return_value = ExecutionResult(
        success=False,
        status=OrderStatus.REJECTED,
        fill=None,
        order_id=None,
        fill_id=None,
        statutory_fees=None,
        correlation_id="corr-exec-rej",
        causation_id=order.order_request_id,
        error_code="BROKER_UNAVAILABLE",
    )

    orch = TradingFloorOrchestrator(
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
        risk_agent=risk_agent,
        execution_agent=exec_agent,
    )

    state = TradingFloorState(
        correlation_id="corr-exec-rej",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.REJECTED
    assert "BROKER_UNAVAILABLE" in res.rejection_reasons


def test_orchestrator_analyst_failure_handling() -> None:
    """Verify unhandled analyst failure transitions stage to FAILED."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    analyst_agent.process.return_value = AnalystResult(
        success=False,
        signal=None,
        regime=None,
        explanation="Feed disconnected",
        correlation_id="corr-fail",
        causation_id=uuid4(),
        error_code="FEED_DISCONNECTED",
    )

    orch = TradingFloorOrchestrator(analyst_agent=analyst_agent)
    state = TradingFloorState(
        correlation_id="corr-fail",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.FAILED
    assert any("FEED_DISCONNECTED" in e for e in res.errors)


def test_orchestrator_quant_failure_handling() -> None:
    """Verify unhandled quant failure transitions stage to FAILED."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)

    signal = make_dummy_signal(Direction.LONG)
    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Long trend",
        correlation_id="corr-q-fail",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=False,
        order_proposal=None,
        alpha_score=None,
        explanation="Model error",
        correlation_id="corr-q-fail",
        causation_id=signal.signal_id,
        error_code="MODEL_ERROR",
    )

    orch = TradingFloorOrchestrator(analyst_agent=analyst_agent, quant_agent=quant_agent)
    state = TradingFloorState(
        correlation_id="corr-q-fail",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.FAILED
    assert any("MODEL_ERROR" in e for e in res.errors)


def test_orchestrator_in_memory_checkpointer_fallback() -> None:
    """Verify OrchestratorCheckpointer works reliably in in-memory mode."""
    checkpointer = OrchestratorCheckpointer(redis_client=None)
    state = TradingFloorState(
        correlation_id="corr-mem",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="INFY.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("1500.00"),
        stage=OrchestratorStage.COMPLETED,
    )
    checkpointer.save(state)
    loaded = checkpointer.load("corr-mem")
    assert loaded is not None
    assert loaded["correlation_id"] == "corr-mem"
    assert loaded["stage"] == "COMPLETED"
    assert checkpointer.load("non-existent") is None


def test_orchestrator_redis_failure_resilience() -> None:
    """Verify Redis exceptions in checkpointer or latch fail gracefully."""
    bad_redis = MagicMock()
    bad_redis.get.side_effect = RuntimeError("Redis connection dropped")
    bad_redis.set.side_effect = RuntimeError("Redis write failed")

    checkpointer = OrchestratorCheckpointer(redis_client=bad_redis)
    state = TradingFloorState(
        correlation_id="corr-resilience",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="INFY.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("1500.00"),
    )
    # save should not raise
    checkpointer.save(state)
    # load should return in-memory fallback
    loaded = checkpointer.load("corr-resilience")
    assert loaded is not None

    orch = TradingFloorOrchestrator(redis_client=bad_redis)
    # is_floor_halted should not raise, defaults to False
    assert orch.is_floor_halted() is False


def test_orchestrator_non_paper_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify orchestrator fails closed if TRADING_MODE is not PAPER."""
    from src.orchestrator import graph

    fake_settings = MagicMock()
    fake_settings.trading_mode = "LIVE"
    monkeypatch.setattr(graph, "get_settings", lambda: fake_settings)

    with pytest.raises(ValueError, match="requires TRADING_MODE='PAPER'"):
        TradingFloorOrchestrator()


def test_orchestrator_risk_decision_causation() -> None:
    """Verify state.causation_id updates to risk_decision.decision_id when present."""
    analyst_agent = MagicMock(spec=AnalystAgent)
    quant_agent = MagicMock(spec=QuantAgent)
    risk_agent = MagicMock(spec=RiskAgent)
    exec_agent = MagicMock(spec=ExecutionAgent)

    signal = make_dummy_signal(Direction.LONG)
    order = make_dummy_order()
    fill = make_dummy_fill(order)
    decision = MagicMock(spec=RiskDecision)
    decision.decision_id = uuid4()

    analyst_agent.process.return_value = AnalystResult(
        success=True,
        signal=signal,
        regime=None,
        explanation="Long trend",
        correlation_id="corr-causation",
        causation_id=uuid4(),
    )
    quant_agent.process.return_value = QuantResult(
        success=True,
        order_proposal=order,
        alpha_score=Decimal("0.80"),
        explanation="Alpha score high",
        correlation_id="corr-causation",
        causation_id=signal.signal_id,
    )
    risk_agent.process.return_value = RiskResult(
        success=True,
        approved=True,
        risk_decision=decision,
        approval_token="HMAC-TOKEN",
        rejection_reasons=[],
        correlation_id="corr-causation",
        causation_id=order.order_request_id,
    )
    exec_agent.process.return_value = ExecutionResult(
        success=True,
        status=OrderStatus.FILLED,
        fill=fill,
        order_id=fill.order_id,
        fill_id=fill.fill_id,
        statutory_fees={"total_fees": fill.total_fees},
        correlation_id="corr-causation",
        causation_id=decision.decision_id,
    )

    orch = TradingFloorOrchestrator(
        analyst_agent=analyst_agent,
        quant_agent=quant_agent,
        risk_agent=risk_agent,
        execution_agent=exec_agent,
    )

    state = TradingFloorState(
        correlation_id="corr-causation",
        trigger_event_id=uuid4(),
        causation_id=uuid4(),
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=Decimal("2500.00"),
    )

    res = orch.run(state)
    assert res.stage == OrchestratorStage.COMPLETED
    assert res.causation_id == decision.decision_id
