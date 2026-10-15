"""CLI runner to replay a sequence of NIFTY 50 ticks through the live stack.

Connects:
1. Market data ingestion & candle aggregation.
2. Multi-agent stateful orchestrator traversal.
3. Portfolio ledger double-entry ingestion.

Governance: ADR-001, ADR-002, ADR-004, ADR-011, ADR-012.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.agents.analyst.agent import AnalystAgent
from src.agents.execution.agent import ExecutionAgent
from src.agents.mcp_client import InMemoryMCPClientSession
from src.agents.quant.agent import QuantAgent
from src.agents.risk.agent import RiskAgent
from src.common.config import get_settings
from src.common.logging import configure_logging
from src.common.schemas.market import (
    CandleInterval,
    DataProvenance,
    FeedMode,
    Tick,
)
from src.mcp_servers.market_data_streamer.aggregator import CandleAggregator
from src.mcp_servers.portfolio_ledger.ledger import DoubleEntryLedger
from src.orchestrator.checkpoint import OrchestratorCheckpointer
from src.orchestrator.graph import TradingFloorOrchestrator
from src.orchestrator.state import OrchestratorStage, TradingFloorState

logger = logging.getLogger("alphatrader.scripts.replay_pipeline")


def simulate_market_session(
    symbol: str = "RELIANCE.NS",
    ticks_count: int = 20,
    base_price: Decimal = Decimal("2500.00"),
    step_seconds: int = 60,
) -> list[Tick]:
    """Generate a realistic sequence of simulated ticks crossing interval boundaries."""
    ticks: list[Tick] = []
    start_time = datetime(2026, 10, 15, 9, 15, 0, tzinfo=UTC)
    current_p = base_price

    prov = DataProvenance(
        provider="simulated_replay",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=datetime.now(UTC),
    )

    for i in range(ticks_count):
        # Deterministic gentle upward drift with slight fluctuation
        drift = Decimal("2.50") if i % 3 != 0 else Decimal("-1.00")
        current_p += drift
        tick = Tick(
            symbol=symbol,
            timestamp=start_time + timedelta(seconds=i * step_seconds),
            last_price=current_p,
            volume=Decimal("100"),
            sequence_number=i + 1,
            provenance=prov,
        )
        ticks.append(tick)

    return ticks


def run_pipeline(
    symbol: str = "RELIANCE.NS",
    ticks_count: int = 15,
) -> int:
    """Replay tick sequence through the orchestrator and portfolio ledger."""
    settings = get_settings()
    if settings.trading_mode != "PAPER":
        logger.error("Replay pipeline requires TRADING_MODE='PAPER'")
        return 1

    logger.info(
        "Starting replay pipeline for %s (%d ticks) in %s mode",
        symbol,
        ticks_count,
        settings.trading_mode,
    )

    # 1. Setup in-memory mock MCP harness and ledger
    mcp_session = InMemoryMCPClientSession()
    ledger = DoubleEntryLedger("memory://alphatrader")
    # Initialize genesis funding if in memory
    try:
        ledger.initialize_genesis_funding(Decimal("10000000.0000"))
    except Exception as e:
        logger.debug("Ledger already funded or uninitialized: %s", e)

    aggregator = CandleAggregator(intervals=(CandleInterval.FIVE_MINUTES,))
    checkpointer = OrchestratorCheckpointer(redis_client=None)

    # Wire agents and orchestrator
    analyst = AnalystAgent(mcp_client=mcp_session)
    quant = QuantAgent(mcp_client=mcp_session)
    risk = RiskAgent(mcp_client=mcp_session)
    execution = ExecutionAgent(mcp_client=mcp_session)

    orchestrator = TradingFloorOrchestrator(
        mcp_client=mcp_session,
        checkpointer=checkpointer,
        analyst_agent=analyst,
        quant_agent=quant,
        risk_agent=risk,
        execution_agent=execution,
    )

    ticks = simulate_market_session(symbol=symbol, ticks_count=ticks_count)
    processed_count = 0
    orders_filled = 0

    for tick in ticks:
        correlation_id = f"replay-tick-{tick.sequence_number}-{uuid.uuid4().hex[:6]}"
        trigger_id = uuid.uuid4()

        # Update aggregator
        _ = aggregator.process_tick(tick)

        # Dispatch orchestrator state
        state = TradingFloorState(
            correlation_id=correlation_id,
            trigger_event_id=trigger_id,
            causation_id=trigger_id,
            symbol=tick.symbol,
            interval=CandleInterval.FIVE_MINUTES,
            current_market_price=tick.last_price,
            tick=tick,
        )

        final_state = orchestrator.run(state)
        processed_count += 1

        if final_state.stage == OrchestratorStage.COMPLETED and final_state.fill:
            orders_filled += 1
            logger.info(
                "Tick %d resulted in paper Fill %s for %s @ %s",
                tick.sequence_number,
                final_state.fill.fill_id,
                final_state.fill.symbol,
                final_state.fill.fill_price,
            )

    logger.info(
        "Replay finished: %d ticks evaluated, %d paper orders filled.",
        processed_count,
        orders_filled,
    )
    return 0


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Replay simulated NIFTY 50 ticks through trading pipeline"
    )
    parser.add_argument("--symbol", default="RELIANCE.NS", help="NSE instrument symbol")
    parser.add_argument("--ticks", type=int, default=10, help="Number of ticks to replay")
    args = parser.parse_args()

    sys.exit(run_pipeline(symbol=args.symbol, ticks_count=args.ticks))


if __name__ == "__main__":
    main()
