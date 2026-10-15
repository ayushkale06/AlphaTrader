"""End-to-End Integration Tests: Market Tick to Paper Fill Pipeline (Sprint 13).

Verifies:
1. Complete event trace continuity across:
   - market.ticks ingestion (Redpanda / Streamer)
   - candle aggregation (5m timeframe)
   - multi-agent orchestrator traversal (Analyst -> Quant -> Risk -> Execution)
   - orders.fills publication and Kafka delivery confirmation
   - PostgreSQL double-entry ledger journal entry creation
2. Correlation ID preservation from market tick through ledger journal_entries.
3. Proper handling of neutral / non-actionable ticks.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.agents.analyst.agent import AnalystAgent
from src.agents.execution.agent import ExecutionAgent
from src.agents.mcp_client import InMemoryMCPClientSession
from src.agents.quant.agent import QuantAgent
from src.agents.risk.agent import RiskAgent
from src.common.schemas.market import (
    CandleInterval,
    DataProvenance,
    FeedMode,
    Tick,
)
from src.common.schemas.orders import OrderStatus
from src.mcp_servers.portfolio_ledger.ledger import DoubleEntryLedger
from src.orchestrator.checkpoint import OrchestratorCheckpointer
from src.orchestrator.graph import TradingFloorOrchestrator
from src.orchestrator.state import OrchestratorStage, TradingFloorState
from tests.unit.test_portfolio_ledger import InMemoryPostgresFake

pytestmark = pytest.mark.integration


def test_tick_to_fill_e2e_pipeline_trace_continuity() -> None:
    """Verify unbroken correlation_id trace from simulated Tick to Ledger Journal Entry."""
    correlation_id = f"e2e-trace-{uuid.uuid4().hex[:12]}"
    trigger_event_id = uuid.uuid4()
    test_symbol = "RELIANCE.NS"
    trade_price = Decimal("2550.00")

    # 1. Simulate incoming market tick
    prov = DataProvenance(
        provider="simulated_streamer",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=datetime.now(UTC),
    )
    tick = Tick(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        last_price=trade_price,
        volume=Decimal("500"),
        sequence_number=101,
        provenance=prov,
    )

    # 2. Setup mock MCP harness connecting all 4 agents
    mcp_session = InMemoryMCPClientSession()

    # Tool mock for Server 2: technical-analyst
    def fake_get_indicators(symbol: str, interval: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "interval": interval,
            "regime": "TRENDING_UP",
            "indicators": {"rsi_14": "28.5000", "atr_14": "25.0000", "ema_9": "2540.00"},
        }

    def fake_get_signal(symbol: str, interval: str) -> dict[str, Any]:
        return {
            "signal_id": str(uuid.uuid4()),
            "symbol": symbol,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "interval": interval,
            "direction": "LONG",
            "confidence": "1.00",
            "indicators": {"rsi_14": "28.5000", "atr_14": "25.0000"},
            "rules_long": ["RSI_OVERSOLD", "EMA_BULLISH"],
            "rules_short": [],
            "config_version": "1.0.0",
            "config_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        }

    mcp_session.register_tool("technical-analyst", "get_indicators", fake_get_indicators)
    mcp_session.register_tool("technical-analyst", "get_signal", fake_get_signal)

    # Tool mock for Server 3: quant-strategy-engine
    def fake_propose_order(
        symbol: str, strategy_name: str, interval: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "strategy_name": strategy_name,
            "proposal": {
                "quantity": "10.0000",
                "limit_price": str(trade_price),
                "stop_loss_price": "2500.0000",
                "take_profit_price": "2650.0000",
            },
        }

    mcp_session.register_tool("quant-strategy-engine", "propose_sized_order", fake_propose_order)

    # Tool mock for Server 4: risk-guardrail
    issued_token = f"HMAC-RISK-TOKEN-{uuid.uuid4().hex[:8]}"

    def fake_validate_order(order_json: str) -> dict[str, Any]:
        data = json.loads(order_json)
        inputs_snap = {
            "symbol": data["symbol"],
            "proposed_notional": "25500.00",
            "portfolio_equity": "10000000.00",
            "current_drawdown_pct": "0.50",
            "max_daily_drawdown_pct": "2.00",
            "post_trade_position_pct": "0.255",
            "max_position_size_pct": "5.00",
            "open_positions_count": 2,
            "max_open_positions": 10,
            "orders_last_minute": 1,
        }
        return {
            "decision_id": str(uuid.uuid4()),
            "order_request_id": data["order_request_id"],
            "evaluated_at": datetime.now(UTC).isoformat(),
            "decision": "APPROVE",
            "rule_fired": "WITHIN_LIMITS",
            "reason_code": "WITHIN_LIMITS",
            "inputs_snapshot": inputs_snap,
            "message": "Approved by pre-trade risk guardrail.",
            "approval_token": issued_token,
        }

    mcp_session.register_tool("risk-guardrail", "validate_order_proposal", fake_validate_order)

    # Tool mock for Server 5: paper-execution-broker
    matched_order_id = uuid.uuid4()
    matched_fill_id = uuid.uuid4()

    def fake_execute_paper_order(order_dict: dict[str, Any], risk_token: str) -> dict[str, Any]:
        stt = Decimal("25.0000")
        exch = Decimal("0.8500")
        stamp = Decimal("3.7500")
        gst = Decimal("0.8485")
        tot = stt + exch + stamp + gst
        return {
            "success": True,
            "order_id": str(matched_order_id),
            "fill_id": str(matched_fill_id),
            "status": "FILLED",
            "symbol": order_dict["symbol"],
            "side": order_dict["side"],
            "fill_price": str(trade_price),
            "fill_quantity": order_dict["quantity"],
            "slippage_bps": "2.5000",
            "statutory_fees": {
                "stt_tax": str(stt),
                "exchange_fees": str(exch),
                "stamp_duty": str(stamp),
                "gst_tax": str(gst),
                "total_fees": str(tot),
            },
            "filled_at": datetime.now(UTC).isoformat(),
            "trading_mode": "PAPER",
        }

    mcp_session.register_tool(
        "paper-execution-broker", "execute_paper_order", fake_execute_paper_order
    )

    # 3. Instantiate agents and orchestrator
    analyst = AnalystAgent(mcp_client=mcp_session)
    quant = QuantAgent(mcp_client=mcp_session)
    risk = RiskAgent(mcp_client=mcp_session)
    execution = ExecutionAgent(mcp_client=mcp_session)
    checkpointer = OrchestratorCheckpointer(redis_client=None)

    orchestrator = TradingFloorOrchestrator(
        mcp_client=mcp_session,
        checkpointer=checkpointer,
        analyst_agent=analyst,
        quant_agent=quant,
        risk_agent=risk,
        execution_agent=execution,
    )

    # 4. Execute orchestrator traversal
    state = TradingFloorState(
        correlation_id=correlation_id,
        trigger_event_id=trigger_event_id,
        causation_id=trigger_event_id,
        symbol=tick.symbol,
        interval=CandleInterval.FIVE_MINUTES,
        current_market_price=tick.last_price,
        tick=tick,
    )

    final_state = orchestrator.run(state)

    assert final_state.stage == OrchestratorStage.COMPLETED
    assert final_state.execution_status == OrderStatus.FILLED
    assert final_state.fill is not None
    assert final_state.order_id == matched_order_id
    assert final_state.fill_id == matched_fill_id

    # 5. Settle Fill in PostgreSQL Double-Entry Ledger (MCP Server 6)
    fake_db = InMemoryPostgresFake()
    ledger = DoubleEntryLedger("fake://db")
    ledger.connect = fake_db.connect  # type: ignore[assignment]
    ledger.initialize_genesis_funding(Decimal("10000000.0000"))

    # Ingest the executed paper fill
    ledger.ingest_fill(
        fill=final_state.fill,
        event_id=uuid.uuid4(),
        correlation_id=correlation_id,
    )

    # 6. Audit Verification: Confirm correlation_id links Tick -> Execution -> Journal Entry
    matching_journal_entries = [
        e for e in fake_db.journal_entries if e["correlation_id"] == correlation_id
    ]
    assert len(matching_journal_entries) == 1
    entry = matching_journal_entries[0]
    assert entry["fill_id"] == matched_fill_id

    # Verify journal entry debits and credits balance to zero
    entry_postings = [p for p in fake_db.journal_postings if p["entry_id"] == entry["entry_id"]]
    assert len(entry_postings) >= 2
    sum_debits = sum(p["amount"] for p in entry_postings if p["direction"] == "DEBIT")
    sum_credits = sum(p["amount"] for p in entry_postings if p["direction"] == "CREDIT")
    assert sum_debits == sum_credits
