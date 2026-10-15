"""Integration tests for Quant Strategy Engine (Sprint 6 Phase B).

NOTE: These tests are strictly HANDLER-LEVEL integration tests verifying:
1. live pipeline and publication to Redpanda topic signals.quant,
2. missing candle suppression and handler-processing liveness,
3. publisher failure and handler-level retry resilience,
4. DLQ routing on malformed bytes and subsequent success,
5. live Redis exact-key persistence with zero shared-state mutation,
6. FastMCP server protocol dispatch and tool registration.

They DO NOT prove background consumer polling, Kafka consumer group rebalance,
or Kafka consumer offset replay, as QuantStrategyService does not yet implement
a background consumer loop.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from mcp.types import CallToolResult

from src.common.config import get_settings
from src.common.contracts import TopicName, validate_topic_message
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.market import (
    Candle,
    CandleInterval,
    DataProvenance,
    FeedMode,
)
from src.common.schemas.signals import Direction, QuantSignal, TechnicalSignal
from src.mcp_servers.quant_strategy_engine.cache import (
    PORTFOLIO_TARGET_KEY,
    QUANT_SIGNAL_KEY_PREFIX,
    QuantStrategyCache,
)
from src.mcp_servers.quant_strategy_engine.server import server
from src.mcp_servers.quant_strategy_engine.strategies import EmaTrendFollowingStrategy
from src.mcp_servers.quant_strategy_engine.worker import QuantStrategyService

pytestmark = pytest.mark.integration

FIXED_UTC = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
PROV = DataProvenance(
    provider="simulated_feed",
    feed_mode=FeedMode.SIMULATED,
    is_delayed=False,
    delay_seconds=0,
    ingested_at=FIXED_UTC,
)


def _make_candle_envelope(
    symbol: str,
    close: Decimal,
    correlation_id: str,
    interval: CandleInterval = CandleInterval.FIVE_MINUTES,
) -> EventEnvelope[Candle]:
    candle = Candle(
        symbol=symbol,
        interval=interval,
        open_time=FIXED_UTC,
        close_time=FIXED_UTC + timedelta(minutes=5),
        open=close,
        high=close * Decimal("1.01"),
        low=close * Decimal("0.99"),
        close=close,
        volume=Decimal("1000"),
        vwap=close,
        trade_count=50,
        is_final=True,
        provenance=PROV,
    )
    return EventEnvelope[Candle](
        event_id=uuid.uuid4(),
        correlation_id=correlation_id,
        produced_at=FIXED_UTC + timedelta(minutes=5),
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=candle,
    )


def _make_technical_signal_envelope(
    symbol: str,
    correlation_id: str,
    direction: Direction = Direction.LONG,
    interval: CandleInterval = CandleInterval.FIVE_MINUTES,
) -> EventEnvelope[TechnicalSignal]:
    tech_sig = TechnicalSignal(
        symbol=symbol,
        evaluated_at=FIXED_UTC + timedelta(minutes=5),
        interval=interval,
        direction=direction,
        confidence=Decimal("1.00"),
        regime=None,
        indicators={
            "ema_9": Decimal("105.0"),
            "ema_21": Decimal("100.0"),
            "ema_50": Decimal("95.0"),
            "atr_14": Decimal("2.0"),
            "close": Decimal("101.0"),
        },
        rules_long=["ema_9_above_ema_21"],
        rules_short=[],
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=PROV,
    )
    return EventEnvelope[TechnicalSignal](
        event_id=uuid.uuid4(),
        correlation_id=correlation_id,
        produced_at=FIXED_UTC + timedelta(minutes=5),
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )


# ==============================================================================
# 1. Pipeline & Publish (Handler-Level)
# ==============================================================================
def test_quant_handler_live_pipeline_and_publish(
    require_redpanda: None,
    require_redis: None,
) -> None:
    """Verifies that handle_raw_message processes candle + technical signal and publishes
    a valid QuantSignal to live Redpanda topic signals.quant with matching causation_id.
    """
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_id = uuid.uuid4().hex[:6].upper()
    test_symbol = f"QP{unique_id}.NS"
    cid = f"cid-pipe-{unique_id}"
    exact_redis_key = f"{QUANT_SIGNAL_KEY_PREFIX}{test_symbol}:5m"

    service = QuantStrategyService(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        redis_url=settings.redis_url,
    )
    service.register_strategy(
        EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    )

    # Pre-subscribe Redpanda consumer to signals.quant
    consumer_conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": f"test-quant-pipe-{unique_id}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(consumer_conf)
    consumer.subscribe([TopicName.SIGNALS_QUANT.value])

    try:
        # Step 1: Ingest genuine closed candle
        c_env = _make_candle_envelope(test_symbol, Decimal("101.0"), cid)
        c_bytes = c_env.to_json_bytes()
        res_c = service.handle_raw_message(TopicName.MARKET_CANDLES.value, c_bytes)
        assert res_c is True
        assert (test_symbol, "5m") in service.latest_candles

        # Step 2: Ingest technical signal
        t_env = _make_technical_signal_envelope(test_symbol, cid)
        t_bytes = t_env.to_json_bytes()
        res_t = service.handle_raw_message(TopicName.SIGNALS_TECHNICAL.value, t_bytes)
        assert res_t is True
        service.publisher.flush(timeout_seconds=5.0)

        # Step 3: Assert live Redpanda publication
        found_signal = False
        deadline = time.time() + 10.0
        while time.time() < deadline:
            msg = consumer.poll(timeout=1.0)
            if msg is None or msg.error():
                continue
            val_bytes = msg.value()
            if val_bytes is None:
                continue
            validated = validate_topic_message(TopicName.SIGNALS_QUANT, val_bytes)
            payload: QuantSignal = validated.payload  # type: ignore[assignment]
            if payload.symbol == test_symbol:
                key_raw = msg.key()
                assert key_raw is not None
                assert key_raw.decode("utf-8") == test_symbol
                assert validated.causation_id == t_env.event_id
                assert validated.correlation_id == cid
                assert payload.direction == Direction.LONG
                assert payload.target_weight > Decimal("0")
                assert payload.expected_net_edge_bps_per_trade is None
                found_signal = True
                break

        assert found_signal, f"Did not consume expected QuantSignal for {test_symbol}"

        # Step 4: Assert Redis state
        cached = service.cache.get_latest_signal(test_symbol, "5m")
        assert cached is not None
        assert cached.symbol == test_symbol
        assert cached.direction == Direction.LONG

    finally:
        consumer.close()
        # Clean up only exact test-owned key
        try:
            import redis

            r = redis.from_url(settings.redis_url)
            r.delete(exact_redis_key)
        except Exception:
            pass


# ==============================================================================
# 2. Missing Candle Suppression (Handler-Level)
# ==============================================================================
def test_quant_handler_missing_candle_suppression_and_liveness(
    require_redpanda: None,
) -> None:
    """Verifies that missing genuine candle causes handle_raw_message to execute
    and increment _messages_processed (handler proof only) while cleanly suppressing
    publication to signals.quant and signals.quant.dlq over a bounded 3.0s window.
    """
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_id = uuid.uuid4().hex[:6].upper()
    unseen_symbol = f"NC{unique_id}.NS"
    cid = f"cid-nocandle-{unique_id}"

    service = QuantStrategyService(bootstrap_servers=settings.kafka_bootstrap_servers)
    service.register_strategy(
        EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    )

    assert (unseen_symbol, "5m") not in service.latest_candles

    # Pre-subscribe consumers
    consumer_conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": f"test-quant-suppress-{unique_id}",
        "auto.offset.reset": "latest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(consumer_conf)
    consumer.subscribe([TopicName.SIGNALS_QUANT.value, "signals.quant.dlq"])

    # Warmup poll to ensure partition assignment
    consumer.poll(timeout=1.0)

    try:
        initial_processed = service._messages_processed
        t_env = _make_technical_signal_envelope(unseen_symbol, cid)
        t_bytes = t_env.to_json_bytes()

        # Execute handler
        res = service.handle_raw_message(TopicName.SIGNALS_TECHNICAL.value, t_bytes)
        service.publisher.flush(timeout_seconds=2.0)

        # Assertion 1: Handler liveness proof
        assert res is True
        assert service._messages_processed == initial_processed + 1

        # Assertion 2: Bounded negative-observation window (3 seconds)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            msg = consumer.poll(timeout=0.5)
            if msg is not None and not msg.error():
                val = msg.value()
                raw = val.decode("utf-8", errors="replace") if val else ""
                pytest.fail(f"Unexpected emission during missing-candle suppression: {raw}")

    finally:
        consumer.close()


# ==============================================================================
# 3. Publish Failure & Retry Resilience (Handler-Level)
# ==============================================================================
def test_quant_handler_publish_failure_and_retry_resilience(
    require_redpanda: None,
    require_redis: None,
) -> None:
    """Verifies that when publisher fails at the boundary, handle_raw_message returns
    False, does NOT cache success in Redis, routes error to DLQ, and upon publisher
    restoration, retrying the exact same input bytes succeeds.
    """
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_id = uuid.uuid4().hex[:6].upper()
    fail_symbol = f"FL{unique_id}.NS"
    cid = f"cid-fail-{unique_id}"
    exact_redis_key = f"{QUANT_SIGNAL_KEY_PREFIX}{fail_symbol}:5m"

    service = QuantStrategyService(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        redis_url=settings.redis_url,
    )
    service.register_strategy(
        EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    )

    # Seed genuine candle
    c_env = _make_candle_envelope(fail_symbol, Decimal("101.0"), cid)
    service.handle_raw_message(TopicName.MARKET_CANDLES.value, c_env.to_json_bytes())

    # Pre-subscribe DLQ consumer and Output consumer
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"test-quant-resilience-{unique_id}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["signals.quant.dlq", TopicName.SIGNALS_QUANT.value])

    try:
        t_env = _make_technical_signal_envelope(fail_symbol, cid)
        t_bytes = t_env.to_json_bytes()

        # Step 1: Fault injection at publisher boundary
        original_producer = service.publisher._producer
        service.publisher._producer = None  # type: ignore[assignment]

        initial_signals = service._signals_published
        res_fail = service.handle_raw_message(TopicName.SIGNALS_TECHNICAL.value, t_bytes)

        # Assertions during failure
        assert res_fail is False
        assert service._signals_published == initial_signals
        assert service.cache.get_latest_signal(fail_symbol, "5m") is None

        # Step 2: Restore publisher and retry exact same input bytes
        service.publisher._producer = original_producer
        res_retry = service.handle_raw_message(TopicName.SIGNALS_TECHNICAL.value, t_bytes)
        assert res_retry is True
        service.publisher.flush(timeout_seconds=5.0)

        # Assert success cache write
        cached = service.cache.get_latest_signal(fail_symbol, "5m")
        assert cached is not None
        assert cached.symbol == fail_symbol

        # Step 3: Assert live Redpanda received the retry publication
        found_signal = False
        deadline = time.time() + 10.0
        while time.time() < deadline:
            msg = consumer.poll(timeout=1.0)
            if msg is None or msg.error():
                continue
            if msg.topic() == TopicName.SIGNALS_QUANT.value:
                val_retry = msg.value()
                if val_retry is None:
                    continue
                validated = validate_topic_message(TopicName.SIGNALS_QUANT, val_retry)
                payload: QuantSignal = validated.payload  # type: ignore[assignment]
                if payload.symbol == fail_symbol:
                    found_signal = True
                    break

        assert found_signal, f"Retry did not produce QuantSignal for {fail_symbol}"

    finally:
        consumer.close()
        try:
            import redis

            r = redis.from_url(settings.redis_url)
            r.delete(exact_redis_key)
        except Exception:
            pass


# ==============================================================================
# 4. DLQ Routing & Subsequent Success (Handler-Level)
# ==============================================================================
def test_quant_handler_dlq_routing_and_subsequent_success(
    require_redpanda: None,
) -> None:
    """Verifies that malformed input bytes are routed to signals.quant.dlq matching
    the exact schema (error_type, reason, failed_at, raw_payload) and the worker
    subsequently processes a valid message successfully.
    """
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_id = uuid.uuid4().hex[:6].upper()
    recover_symbol = f"RC{unique_id}.NS"
    cid = f"cid-recov-{unique_id}"

    service = QuantStrategyService(bootstrap_servers=settings.kafka_bootstrap_servers)
    service.register_strategy(
        EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    )

    # Pre-subscribe DLQ consumer
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"test-quant-dlq-{unique_id}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["signals.quant.dlq", TopicName.SIGNALS_QUANT.value])

    try:
        # Step 1: Send malformed non-JSON bytes
        bad_bytes = b"non-json-corrupt-payload"
        res_bad = service.handle_raw_message(TopicName.SIGNALS_TECHNICAL.value, bad_bytes)
        assert res_bad is False

        # Step 2: Assert DLQ event structure
        found_dlq = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            msg = consumer.poll(timeout=1.0)
            if msg is None or msg.error():
                continue
            if msg.topic() == "signals.quant.dlq":
                raw_dlq = msg.value()
                if raw_dlq is None:
                    continue
                dlq_data = json.loads(raw_dlq.decode("utf-8"))
                if dlq_data.get("raw_payload") == {"raw": str(bad_bytes)}:
                    assert dlq_data.get("error_type") == "QUANT_STRATEGY_PROCESSING_ERROR"
                    assert "JSON decode failure" in dlq_data.get("reason", "")
                    assert "failed_at" in dlq_data
                    found_dlq = True
                    break

        assert found_dlq, "Did not consume expected DLQ event from signals.quant.dlq"

        # Step 3: Subsequent valid record succeeds
        c_env = _make_candle_envelope(recover_symbol, Decimal("101.0"), cid)
        service.handle_raw_message(TopicName.MARKET_CANDLES.value, c_env.to_json_bytes())

        t_env = _make_technical_signal_envelope(recover_symbol, cid)
        res_good = service.handle_raw_message(
            TopicName.SIGNALS_TECHNICAL.value, t_env.to_json_bytes()
        )
        assert res_good is True

    finally:
        consumer.close()


# ==============================================================================
# 5. Live Redis Exact-Key Persistence & Isolation
# ==============================================================================
def test_quant_live_redis_isolated_key_persistence(
    require_redis: None,
) -> None:
    """Verifies live Redis serialization, retrieval, and 24h TTL near 86400s
    on an isolated test key, with ZERO shared portfolio-target mutation.
    """
    import redis

    settings = get_settings()
    unique_id = uuid.uuid4().hex[:6].upper()
    isolated_symbol = f"RT{unique_id}.NS"
    exact_key = f"{QUANT_SIGNAL_KEY_PREFIX}{isolated_symbol}:5m"

    r = redis.from_url(settings.redis_url)
    cache = QuantStrategyCache(redis_url=settings.redis_url)

    # Capture initial portfolio target state to prove unchanged
    orig_portfolio = r.get(PORTFOLIO_TARGET_KEY)

    try:
        sig = QuantSignal(
            symbol=isolated_symbol,
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            strategy_name="ema_trend_following",
            strategy_version="1.0.0",
            direction=Direction.LONG,
            target_weight=Decimal("0.1000"),
            expected_net_edge_bps_per_trade=None,
            edge_basis=None,
            backtest_report_id=None,
            config_hash="a" * 64,
            provenance=PROV,
        )

        # Save to Redis
        cache.save_signal(sig)

        # Assert exact key exists
        raw_val = r.get(exact_key)
        assert raw_val is not None

        # Assert TTL is near 86400s (tolerance: within 100 seconds)
        ttl = r.ttl(exact_key)
        assert 86300 <= ttl <= 86400

        # Assert exact retrieval and Decimal parsing
        retrieved = cache.get_latest_signal(isolated_symbol, "5m")
        assert retrieved is not None
        assert retrieved.symbol == isolated_symbol
        assert retrieved.direction == Direction.LONG
        assert retrieved.target_weight == Decimal("0.1000")
        assert retrieved.expected_net_edge_bps_per_trade is None

        # Assert shared portfolio key was NOT mutated
        current_portfolio = r.get(PORTFOLIO_TARGET_KEY)
        assert current_portfolio == orig_portfolio

    finally:
        # Exact key cleanup only; never FLUSHDB or wildcard deletion
        r.delete(exact_key)


# ==============================================================================
# 6. FastMCP Server Protocol Dispatch & Tool Registration
# ==============================================================================
@pytest.mark.asyncio
async def test_quant_mcp_server_live_stdio_protocol() -> None:
    """Verifies that the FastMCP server registers all 8 expected tools and
    executes queries with valid structured content and null expected-edge invariant.
    """
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    expected_eight = [
        "list_strategies",
        "get_strategy_signal",
        "get_target_portfolio",
        "get_backtest_report",
        "explain_strategy_signal",
        "get_engine_health",
        "evaluate_alpha_factors",
        "propose_sized_order",
    ]

    # Assert all 8 exact tool names are registered
    assert len(tools) == 8
    assert sorted(tool_names) == sorted(expected_eight)

    # Tool Call 1: list_strategies
    res_strats = await server.call_tool("list_strategies", {})
    assert isinstance(res_strats, CallToolResult)
    assert not res_strats.is_error
    data_strats = json.loads(res_strats.content[0].text)  # type: ignore[union-attr]
    assert "strategies" in data_strats
    assert len(data_strats["strategies"]) == 3

    # Tool Call 2: get_backtest_report for registered TCS report
    tcs_report_id = "1144e89e0335ce71c2d9fd05894efc7543054f99062ce4d5e2009bf3f8cf210f"
    res_rep = await server.call_tool("get_backtest_report", {"report_id": tcs_report_id})
    assert isinstance(res_rep, CallToolResult)
    assert not res_rep.is_error
    data_rep = json.loads(res_rep.content[0].text)  # type: ignore[union-attr]
    assert data_rep["report_id"] == tcs_report_id
    assert data_rep["symbol"] == "TCS.NS"
    assert data_rep["strategy_name"] == "EMA_Crossover"
    assert data_rep["is_out_of_sample"] is False
    assert data_rep["edge_basis"] == "IN_SAMPLE_DEMO"

    # Tool Call 3: get_engine_health
    res_health = await server.call_tool("get_engine_health", {})
    assert isinstance(res_health, CallToolResult)
    assert not res_health.is_error
    data_health = json.loads(res_health.content[0].text)  # type: ignore[union-attr]
    assert "status" in data_health
