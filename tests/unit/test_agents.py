"""Unit tests for Sprint 11: Specialized Autonomous Agents.

Verifies:
1. AnalystAgent:
   - Evaluates indicators and signal from Technical Analyst MCP Server 2.
   - Preserves correlation_id and sets causation_id to trigger_event_id.
   - Handles NOT_READY and missing indicators gracefully.
2. QuantAgent:
   - Evaluates alpha models and ATR/Kelly sizing from Quant Engine MCP Server 3.
   - Formulates OrderRequest with source_signal_ids linking to analyst signal.
   - Enforces cash delivery: FLAT signal yields no order proposal.
3. RiskAgent:
   - Validates proposals against Risk Guardrail MCP Server 4.
   - Preserves cryptographic RiskApprovalToken without client modification.
   - Maps rejection reasons accurately without LLM override.
4. ExecutionAgent:
   - Dispatches orders to Paper Execution Broker MCP Server 5.
   - Extracts exact fill metrics and statutory tax breakdown.
   - Idempotency: Duplicate dispatches of identical order_request_id return cached execution.
   - Ambiguous outcome / timeout reconciliation logic.
5. Full 4-Agent Pipeline Provenance & Tracing:
   - Seamless handoff: Analyst -> Quant -> Risk -> Execution.
   - Immutability of correlation_id across all four stages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.agents.analyst.agent import AnalystAgent
from src.agents.base import (
    AnalystRequest,
    ExecutionRequest,
    QuantRequest,
    RiskRequest,
)
from src.agents.execution.agent import ExecutionAgent
from src.agents.mcp_client import InMemoryMCPClientSession
from src.agents.quant.agent import QuantAgent
from src.agents.risk.agent import RiskAgent
from src.common.schemas.market import CandleInterval
from src.common.schemas.orders import OrderRequest, OrderStatus, OrderType, Side
from src.common.schemas.signals import Direction, MarketRegime, TechnicalSignal


def make_valid_technical_signal(
    symbol: str = "RELIANCE.NS",
    direction: Direction = Direction.LONG,
    regime: MarketRegime = MarketRegime.TRENDING_UP,
) -> TechnicalSignal:
    """Helper creating schema-compliant TechnicalSignal."""
    if direction == Direction.LONG:
        rules_long = ["RSI_OVERSOLD"]
        rules_short = []
        conf = Decimal("1.00")
    elif direction == Direction.SHORT:
        rules_long = []
        rules_short = ["RSI_OVERBOUGHT"]
        conf = Decimal("1.00")
    else:  # FLAT
        rules_long = ["RSI_OVERSOLD"]
        rules_short = ["RSI_OVERBOUGHT"]
        conf = Decimal("0.50")
    return TechnicalSignal(
        signal_id=uuid4(),
        symbol=symbol,
        evaluated_at=datetime.now(UTC),
        interval=CandleInterval.FIVE_MINUTES,
        direction=direction,
        confidence=conf,
        regime=regime,
        indicators={"rsi_14": Decimal("28.50"), "atr_14": Decimal("15.20")},
        rules_long=rules_long,
        rules_short=rules_short,
        config_version="1.0.0",
        config_hash="a" * 64,
    )


def make_valid_order_request(
    symbol: str = "RELIANCE.NS",
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10.0000"),
    limit_price: Decimal = Decimal("2500.0000"),
    stop_loss_price: Decimal = Decimal("2450.0000"),
) -> OrderRequest:
    """Helper creating schema-compliant OrderRequest."""
    req_id = uuid4()
    return OrderRequest(
        order_request_id=req_id,
        idempotency_key=str(req_id),
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        limit_price=limit_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=limit_price * Decimal("1.04"),
        strategy_name="momentum_trend_v1",
        requested_at=datetime.now(UTC),
        trading_mode="PAPER",
    )


@pytest.fixture
def mock_mcp_session() -> InMemoryMCPClientSession:
    """Fixture providing populated in-memory MCP client session."""
    session = InMemoryMCPClientSession()

    # Server 2: technical-analyst tools
    session.register_tool(
        "technical-analyst",
        "get_indicators",
        lambda symbol, interval: {
            "symbol": symbol,
            "interval": interval,
            "regime": "TRENDING_UP",
            "indicators": {"rsi_14": 28.5, "atr_14": 15.2, "adx_14": 32.0},
        },
    )

    sample_signal = make_valid_technical_signal("RELIANCE.NS", Direction.LONG)
    session.register_tool(
        "technical-analyst",
        "get_signal",
        lambda symbol, interval: sample_signal.model_dump(mode="json"),
    )

    # Server 3: quant-strategy-engine tools
    session.register_tool(
        "quant-strategy-engine",
        "evaluate_alpha_factors",
        lambda symbol, interval: {"symbol": symbol, "composite_alpha": "0.78"},
    )

    session.register_tool(
        "quant-strategy-engine",
        "propose_sized_order",
        lambda symbol, strategy_name, interval: {
            "proposal": {
                "symbol": symbol,
                "strategy_name": strategy_name,
                "quantity": "10.0000",
                "limit_price": "2500.0000",
                "stop_loss_price": "2450.0000",
                "take_profit_price": "2600.0000",
            }
        },
    )

    # Server 4: risk-guardrail tools
    def fake_validate_order(order_json: str) -> dict[str, Any]:
        import json

        data = json.loads(order_json)
        req_id = data["order_request_id"]
        qty = Decimal(str(data["quantity"]))
        inputs_snap = {
            "symbol": data["symbol"],
            "proposed_notional": "25000.00",
            "portfolio_equity": "1000000.00",
            "current_drawdown_pct": "0.50",
            "max_daily_drawdown_pct": "2.00",
            "post_trade_position_pct": "2.50",
            "max_position_size_pct": "5.00",
            "open_positions_count": 2,
            "max_open_positions": 10,
            "orders_last_minute": 1,
        }

        # Reject if qty > 100
        if qty > Decimal("100"):
            return {
                "decision_id": str(uuid4()),
                "order_request_id": req_id,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "decision": "REJECT",
                "rule_fired": "MAX_POSITION_SIZE",
                "reason_code": "MAX_POSITION_SIZE_BREACH",
                "inputs_snapshot": inputs_snap,
                "message": "Order quantity exceeds maximum position concentration.",
                "approval_token": None,
            }
        return {
            "decision_id": str(uuid4()),
            "order_request_id": req_id,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "decision": "APPROVE",
            "rule_fired": "WITHIN_LIMITS",
            "reason_code": "WITHIN_LIMITS",
            "inputs_snapshot": inputs_snap,
            "message": "All pre-trade risk checks passed.",
            "approval_token": f"HMAC-FAKE-TOKEN-{req_id}",
        }

    session.register_tool("risk-guardrail", "validate_order_proposal", fake_validate_order)

    # Server 5: paper-execution-broker tools
    def fake_execute_order(order_dict: dict[str, Any], risk_token: str) -> dict[str, Any]:
        if "HMAC-FAKE-TOKEN" not in risk_token:
            return {"success": False, "status": "REJECTED", "reason": "Invalid risk token."}
        oid = str(uuid4())
        fid = str(uuid4())
        # Fees must sum up: stt(25) + exch(0.85) + stamp(3.75) + gst(0.8485) = 30.4485
        return {
            "success": True,
            "order_id": oid,
            "fill_id": fid,
            "status": "FILLED",
            "fill_price": order_dict["limit_price"],
            "fill_quantity": order_dict["quantity"],
            "slippage_bps": "2.5000",
            "statutory_fees": {
                "stt_tax": "25.0000",
                "exchange_fees": "0.8500",
                "stamp_duty": "3.7500",
                "gst_tax": "0.8485",
                "total_fees": "30.4485",
            },
            "filled_at": datetime.now(UTC).isoformat(),
        }

    session.register_tool("paper-execution-broker", "execute_paper_order", fake_execute_order)

    return session


# -----------------------------------------------------------------------------
# 1. Analyst Agent Unit Tests
# -----------------------------------------------------------------------------
def test_analyst_agent_success(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify AnalystAgent retrieves indicators, evaluates regime, and emits TechnicalSignal."""
    agent = AnalystAgent(mcp_client=mock_mcp_session)
    trigger_id = uuid4()
    req = AnalystRequest(
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        correlation_id="corr-analyst-1",
        trigger_event_id=trigger_id,
        current_market_price=Decimal("2500.00"),
    )

    res = agent.process(req)
    assert res.success is True
    assert res.correlation_id == "corr-analyst-1"
    assert res.causation_id == trigger_id
    assert res.regime == MarketRegime.TRENDING_UP
    assert res.signal is not None
    assert res.signal.direction == Direction.LONG
    assert res.signal.confidence == Decimal("1.00")
    assert "RSI=28.5" in res.explanation


def test_analyst_agent_server_error(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify AnalystAgent handles Server 2 error responses cleanly."""
    mock_mcp_session.register_tool(
        "technical-analyst",
        "get_indicators",
        lambda symbol, interval: {"error": "NOT_READY", "message": "Warming up."},
    )
    agent = AnalystAgent(mcp_client=mock_mcp_session)
    req = AnalystRequest(
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        correlation_id="corr-err",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "NOT_READY"
    assert res.signal is None


def test_analyst_agent_no_client() -> None:
    """Verify AnalystAgent fails gracefully if no MCP client is provided."""
    agent = AnalystAgent(mcp_client=None)
    req = AnalystRequest(
        symbol="TCS.NS",
        interval=CandleInterval.FIVE_MINUTES,
        correlation_id="corr-none",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "NO_MCP_CLIENT"


# -----------------------------------------------------------------------------
# 2. Quant Agent Unit Tests
# -----------------------------------------------------------------------------
def test_quant_agent_success(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify QuantAgent sizes order and creates OrderRequest linking signal."""
    signal = make_valid_technical_signal("RELIANCE.NS", Direction.LONG)

    agent = QuantAgent(mcp_client=mock_mcp_session)
    trigger_id = uuid4()
    req = QuantRequest(
        analyst_signal=signal,
        portfolio_equity=Decimal("1000000.00"),
        correlation_id="corr-quant-1",
        trigger_event_id=trigger_id,
    )

    res = agent.process(req)
    assert res.success is True
    assert res.correlation_id == "corr-quant-1"
    assert res.causation_id == trigger_id
    assert res.alpha_score == Decimal("0.78")
    assert res.order_proposal is not None
    assert res.order_proposal.symbol == "RELIANCE.NS"
    assert res.order_proposal.side == Side.BUY
    assert res.order_proposal.quantity == Decimal("10.0000")


def test_quant_agent_flat_signal(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify QuantAgent skips order creation for FLAT signal."""
    signal = make_valid_technical_signal("INFY.NS", Direction.FLAT)
    agent = QuantAgent(mcp_client=mock_mcp_session)
    req = QuantRequest(
        analyst_signal=signal,
        portfolio_equity=Decimal("1000000.00"),
        correlation_id="corr-flat",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is True
    assert res.order_proposal is None
    assert "is FLAT" in res.explanation


def test_quant_agent_sizing_error(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify QuantAgent handles sizing errors from Server 3."""
    mock_mcp_session.register_tool(
        "quant-strategy-engine",
        "propose_sized_order",
        lambda symbol, strategy_name, interval: {
            "error": "INSUFFICIENT_DATA",
            "message": "No ATR.",
        },
    )
    signal = make_valid_technical_signal("SBIN.NS", Direction.LONG)
    agent = QuantAgent(mcp_client=mock_mcp_session)
    req = QuantRequest(
        analyst_signal=signal,
        portfolio_equity=Decimal("1000000.00"),
        correlation_id="corr-size-err",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "INSUFFICIENT_DATA"


# -----------------------------------------------------------------------------
# 3. Risk Agent Unit Tests
# -----------------------------------------------------------------------------
def test_risk_agent_approved(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify RiskAgent retrieves signed token on valid order approval."""
    order = make_valid_order_request(quantity=Decimal("10.0000"))
    agent = RiskAgent(mcp_client=mock_mcp_session)
    trigger_id = uuid4()
    req = RiskRequest(
        order_proposal=order,
        correlation_id="corr-risk-1",
        trigger_event_id=trigger_id,
    )

    res = agent.process(req)
    assert res.success is True
    assert res.approved is True
    assert res.approval_token is not None
    assert str(order.order_request_id) in res.approval_token
    assert res.causation_id == trigger_id


def test_risk_agent_rejected(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify RiskAgent returns rejection reason without synthesizing token."""
    order = make_valid_order_request(
        quantity=Decimal("500.0000")
    )  # Triggers mock concentration rejection
    agent = RiskAgent(mcp_client=mock_mcp_session)
    req = RiskRequest(
        order_proposal=order,
        correlation_id="corr-risk-reject",
        trigger_event_id=uuid4(),
    )

    res = agent.process(req)
    assert res.success is True
    assert res.approved is False
    assert res.approval_token is None
    assert "MAX_POSITION_SIZE_BREACH" in res.rejection_reasons[0]


# -----------------------------------------------------------------------------
# 4. Execution Agent Unit Tests
# -----------------------------------------------------------------------------
def test_execution_agent_success(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify ExecutionAgent dispatches approved order and returns Fill with fees."""
    order = make_valid_order_request(quantity=Decimal("10.0000"))
    agent = ExecutionAgent(mcp_client=mock_mcp_session)
    trigger_id = uuid4()
    req = ExecutionRequest(
        order_proposal=order,
        approval_token=f"HMAC-FAKE-TOKEN-{order.order_request_id}",
        current_market_price=Decimal("2500.0000"),
        correlation_id="corr-exec-1",
        trigger_event_id=trigger_id,
    )

    res = agent.process(req)
    assert res.success is True
    assert res.status == OrderStatus.FILLED
    assert res.fill is not None
    assert res.fill.symbol == "RELIANCE.NS"
    assert res.fill.fill_quantity == Decimal("10.0000")
    assert res.statutory_fees is not None
    assert res.statutory_fees["stt_tax"] == Decimal("25.0000")
    assert res.statutory_fees["total_fees"] == Decimal("30.4485")


def test_execution_agent_idempotent_duplicate(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify ExecutionAgent returns cached execution result on duplicate order_request_id."""
    order = make_valid_order_request(symbol="TCS.NS", quantity=Decimal("5.0000"))
    agent = ExecutionAgent(mcp_client=mock_mcp_session)
    req = ExecutionRequest(
        order_proposal=order,
        approval_token=f"HMAC-FAKE-TOKEN-{order.order_request_id}",
        current_market_price=Decimal("3500.0000"),
        correlation_id="corr-dup",
        trigger_event_id=uuid4(),
    )

    res1 = agent.process(req)
    res2 = agent.process(req)  # Replay identical order_request_id

    assert res1.fill_id == res2.fill_id
    assert res2.status == OrderStatus.FILLED


def test_execution_agent_invalid_token(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify ExecutionAgent rejects order with forged/invalid risk token."""
    order = make_valid_order_request()
    agent = ExecutionAgent(mcp_client=mock_mcp_session)
    req = ExecutionRequest(
        order_proposal=order,
        approval_token="FORGED-TOKEN-123",
        current_market_price=Decimal("2500.0000"),
        correlation_id="corr-bad-tok",
        trigger_event_id=uuid4(),
    )

    res = agent.process(req)
    assert res.success is False
    assert res.status == OrderStatus.REJECTED
    assert "Invalid risk token" in str(res.error_code)


def test_execution_agent_timeout_reconciliation(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify ExecutionAgent resolves ambiguous timeout by querying order status."""
    order = make_valid_order_request(symbol="INFY.NS")
    # Simulate timeout on execute_paper_order
    mock_mcp_session.register_tool(
        "paper-execution-broker",
        "execute_paper_order",
        lambda order_dict, risk_token: {"error": "TIMEOUT", "message": "Socket timed out."},
    )
    # Simulate reconciliation query confirming order was filled on broker side
    reconciled_oid = uuid4()
    mock_mcp_session.register_tool(
        "paper-execution-broker",
        "get_paper_order_status",
        lambda order_id: {
            "order_id": str(reconciled_oid),
            "order_request_id": str(order.order_request_id),
            "status": "FILLED",
        },
    )

    agent = ExecutionAgent(mcp_client=mock_mcp_session)
    req = ExecutionRequest(
        order_proposal=order,
        approval_token=f"HMAC-FAKE-TOKEN-{order.order_request_id}",
        current_market_price=Decimal("1500.0000"),
        correlation_id="corr-timeout-recon",
        trigger_event_id=uuid4(),
    )

    res = agent.process(req)
    assert res.success is True
    assert res.status == OrderStatus.FILLED
    assert res.order_id == reconciled_oid


# -----------------------------------------------------------------------------
# 5. Full End-to-End Pipeline Provenance & Tracing Test
# -----------------------------------------------------------------------------
def test_full_agent_pipeline_provenance(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify complete Analyst -> Quant -> Risk -> Execution pipeline
    with strict correlation preservation."""
    root_candle_id = uuid4()
    correlation_id = "TRADE-CORR-FULL-PIPELINE-001"

    # Step 1: Analyst Agent
    analyst = AnalystAgent(mcp_client=mock_mcp_session)
    analyst_res = analyst.process(
        AnalystRequest(
            symbol="RELIANCE.NS",
            interval=CandleInterval.FIVE_MINUTES,
            correlation_id=correlation_id,
            trigger_event_id=root_candle_id,
            current_market_price=Decimal("2500.00"),
        )
    )
    assert analyst_res.success is True
    assert analyst_res.correlation_id == correlation_id
    assert analyst_res.causation_id == root_candle_id
    assert analyst_res.signal is not None

    # Step 2: Quant Agent
    quant = QuantAgent(mcp_client=mock_mcp_session)
    quant_res = quant.process(
        QuantRequest(
            analyst_signal=analyst_res.signal,
            portfolio_equity=Decimal("1000000.00"),
            correlation_id=correlation_id,
            trigger_event_id=analyst_res.signal.signal_id,
        )
    )
    assert quant_res.success is True
    assert quant_res.correlation_id == correlation_id
    assert quant_res.causation_id == analyst_res.signal.signal_id
    assert quant_res.order_proposal is not None

    # Step 3: Risk Agent
    risk = RiskAgent(mcp_client=mock_mcp_session)
    risk_res = risk.process(
        RiskRequest(
            order_proposal=quant_res.order_proposal,
            correlation_id=correlation_id,
            trigger_event_id=quant_res.order_proposal.order_request_id,
        )
    )
    assert risk_res.success is True
    assert risk_res.approved is True
    assert risk_res.correlation_id == correlation_id
    assert risk_res.causation_id == quant_res.order_proposal.order_request_id
    assert risk_res.approval_token is not None

    # Step 4: Execution Agent
    execution = ExecutionAgent(mcp_client=mock_mcp_session)
    exec_res = execution.process(
        ExecutionRequest(
            order_proposal=quant_res.order_proposal,
            approval_token=risk_res.approval_token,
            current_market_price=Decimal("2500.0000"),
            correlation_id=correlation_id,
            trigger_event_id=risk_res.risk_decision.decision_id
            if risk_res.risk_decision
            else uuid4(),
        )
    )
    assert exec_res.success is True
    assert exec_res.status == OrderStatus.FILLED
    assert exec_res.correlation_id == correlation_id
    assert exec_res.fill is not None
    assert exec_res.fill.symbol == "RELIANCE.NS"
    assert exec_res.fill.total_fees == Decimal("30.4485")


# -----------------------------------------------------------------------------
# 6. Branch Coverage Expansion Tests
# -----------------------------------------------------------------------------
def test_analyst_agent_signal_error_branch(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify AnalystAgent handles get_signal error response from Server 2."""
    mock_mcp_session.register_tool(
        "technical-analyst",
        "get_signal",
        lambda symbol, interval: {"error": "SIGNAL_FAILED", "message": "Signal engine unready."},
    )
    agent = AnalystAgent(mcp_client=mock_mcp_session)
    req = AnalystRequest(
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        correlation_id="corr-sig-err",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "SIGNAL_FAILED"


def test_analyst_agent_nested_signal_parsing(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify AnalystAgent handles nested signal dict in Server 2 response."""
    sample_signal = make_valid_technical_signal("RELIANCE.NS", Direction.LONG)
    mock_mcp_session.register_tool(
        "technical-analyst",
        "get_signal",
        lambda symbol, interval: {"signal": sample_signal.model_dump(mode="json")},
    )
    agent = AnalystAgent(mcp_client=mock_mcp_session)
    req = AnalystRequest(
        symbol="RELIANCE.NS",
        interval=CandleInterval.FIVE_MINUTES,
        correlation_id="corr-nested",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is True
    assert res.signal is not None
    assert res.signal.symbol == "RELIANCE.NS"


def test_quant_agent_no_client() -> None:
    """Verify QuantAgent handles missing MCP client."""
    agent = QuantAgent(mcp_client=None)
    signal = make_valid_technical_signal("TCS.NS", Direction.LONG)
    req = QuantRequest(
        analyst_signal=signal,
        portfolio_equity=Decimal("1000000.00"),
        correlation_id="corr-quant-none",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "NO_MCP_CLIENT"


def test_quant_agent_alpha_failure_fallback(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify QuantAgent continues with default alpha when alpha tool fails."""
    mock_mcp_session.register_tool(
        "quant-strategy-engine",
        "evaluate_alpha_factors",
        lambda symbol, interval: {"error": "ALPHA_UNAVAILABLE"},
    )
    signal = make_valid_technical_signal("TCS.NS", Direction.LONG)
    agent = QuantAgent(mcp_client=mock_mcp_session)
    req = QuantRequest(
        analyst_signal=signal,
        portfolio_equity=Decimal("1000000.00"),
        correlation_id="corr-alpha-fb",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is True
    assert res.alpha_score == Decimal("0.50")


def test_risk_agent_no_client() -> None:
    """Verify RiskAgent handles missing MCP client."""
    agent = RiskAgent(mcp_client=None)
    order = make_valid_order_request()
    req = RiskRequest(
        order_proposal=order, correlation_id="corr-risk-none", trigger_event_id=uuid4()
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "NO_MCP_CLIENT"


def test_risk_agent_server_error(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify RiskAgent handles error returned by Server 4."""
    mock_mcp_session.register_tool(
        "risk-guardrail",
        "validate_order_proposal",
        lambda order_json: {"error": "CIRCUIT_BREAKER_ACTIVE", "message": "Floor halted."},
    )
    order = make_valid_order_request()
    agent = RiskAgent(mcp_client=mock_mcp_session)
    req = RiskRequest(
        order_proposal=order, correlation_id="corr-risk-err", trigger_event_id=uuid4()
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "CIRCUIT_BREAKER_ACTIVE"


def test_risk_agent_malformed_response(mock_mcp_session: InMemoryMCPClientSession) -> None:
    """Verify RiskAgent handles malformed response from Server 4."""
    mock_mcp_session.register_tool(
        "risk-guardrail",
        "validate_order_proposal",
        lambda order_json: {"random_key": "not a risk decision"},
    )
    order = make_valid_order_request()
    agent = RiskAgent(mcp_client=mock_mcp_session)
    req = RiskRequest(
        order_proposal=order, correlation_id="corr-risk-mal", trigger_event_id=uuid4()
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "MALFORMED_DECISION"


def test_execution_agent_no_client() -> None:
    """Verify ExecutionAgent handles missing MCP client."""
    agent = ExecutionAgent(mcp_client=None)
    order = make_valid_order_request()
    req = ExecutionRequest(
        order_proposal=order,
        approval_token="TOK",
        current_market_price=Decimal("2500.00"),
        correlation_id="corr-exec-none",
        trigger_event_id=uuid4(),
    )
    res = agent.process(req)
    assert res.success is False
    assert res.error_code == "NO_MCP_CLIENT"


def test_mcp_client_session_coverage() -> None:
    """Verify InMemoryMCPClientSession edge branches
    (unregistered tool, json parsing, execution exception)."""
    session = InMemoryMCPClientSession()

    # 1. Unregistered tool
    res = session.call_tool("server", "missing_tool", {})
    assert res["error"] == "TOOL_NOT_FOUND"

    # 2. String JSON return
    session.register_tool("srv", "json_str", lambda: '{"status": "OK"}')
    res_str = session.call_tool("srv", "json_str", {})
    assert res_str["status"] == "OK"

    # 3. Plain non-json string return
    session.register_tool("srv", "plain_str", lambda: "plain text response")
    res_plain = session.call_tool("srv", "plain_str", {})
    assert res_plain["result"] == "plain text response"

    # 4. Exception in handler
    def bad_handler() -> Any:
        raise ValueError("Boom")

    session.register_tool("srv", "bad", bad_handler)
    res_bad = session.call_tool("srv", "bad", {})
    assert res_bad["error"] == "TOOL_EXECUTION_ERROR"
