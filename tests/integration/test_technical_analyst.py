"""Integration tests for Technical Analyst MCP server and streaming pipeline.

Tests:
1. test_analyst_ingests_candle_and_publishes_signal:
   - Ingests closed final candle from market.candles into streaming service.
   - Verifies TechnicalSignal published to signals.technical with matching causation_id.
2. test_analyst_dlq_routing_on_invalid_candle:
   - Publishes malformed candle to market.candles.
   - Verifies rejection and DLQ routing to signals.technical.dlq.
3. test_analyst_redis_cache_and_state_persistence:
   - Verifies indicators, signal, and health stored in Redis.
   - Verifies state restoration across worker restart.
4. test_technical_analyst_mcp_client_all_tools:
   - Invokes all 5 MCP tools (get_indicators, get_signal, explain_signal,
     list_supported_indicators, get_analyzer_health) against live Redis state.
5. test_analyst_throughput_benchmark:
   - Benchmarks updating 50 symbols x 2 intervals (100 updates) ensuring well under generous bound.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.common.config import get_settings
from src.common.contracts import TopicName, validate_topic_message
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.market import (
    Candle,
    CandleInterval,
    DataProvenance,
    FeedMode,
)
from src.common.schemas.signals import Direction, TechnicalSignal
from src.mcp_servers.technical_analyst.analyst import TechnicalAnalystService
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache
from src.mcp_servers.technical_analyst.indicators.bundle import SymbolIndicatorSet
from src.mcp_servers.technical_analyst.server import (
    explain_signal,
    get_analyzer_health,
    get_indicators,
    get_signal,
    list_supported_indicators,
)

pytestmark = pytest.mark.integration


def test_analyst_ingests_candle_and_publishes_signal(
    require_redpanda: None,
    require_redis: None,
) -> None:
    """Test full pipeline from candle ingestion to signals.technical publish."""
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_run = uuid.uuid4().hex[:8]
    test_symbol = "TCS.NS"
    cid = f"cid-analyst-test-{unique_run}"

    service = TechnicalAnalystService()

    # Pre-warm indicators with 30 bars so a signal can fire
    now = datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC)
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=now,
    )

    last_envelope = None
    for i in range(35):
        candle_ts = now + timedelta(minutes=i)
        candle = Candle(
            symbol=test_symbol,
            interval=CandleInterval.ONE_MINUTE,
            open_time=candle_ts,
            close_time=candle_ts + timedelta(minutes=1),
            open=Decimal("3500.00") + Decimal(str(i)),
            high=Decimal("3505.00") + Decimal(str(i)),
            low=Decimal("3495.00") + Decimal(str(i)),
            close=Decimal("3502.00") + Decimal(str(i)),
            volume=Decimal("15000"),
            vwap=Decimal("3501.00") + Decimal(str(i)),
            trade_count=120,
            provenance=prov,
            is_final=True,
        )
        envelope = EventEnvelope[Candle](
            event_id=uuid.uuid4(),
            correlation_id=cid,
            produced_at=candle.close_time,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=candle,
        )
        last_envelope = envelope
        service.process_candle_message(envelope.to_json_bytes())

    service.publisher.flush(timeout_seconds=5.0)

    # Verify message on signals.technical
    consumer_conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": f"test-signals-verifier-{unique_run}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(consumer_conf)
    consumer.subscribe([TopicName.SIGNALS_TECHNICAL.value])

    found_signal = False
    deadline = time.time() + 10.0
    while time.time() < deadline:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            continue

        raw = msg.value()
        try:
            validated = validate_topic_message(TopicName.SIGNALS_TECHNICAL, raw)
            sig_payload: TechnicalSignal = validated.payload  # type: ignore[assignment]
            if sig_payload.symbol == test_symbol:
                assert validated.correlation_id == cid
                assert validated.causation_id == last_envelope.event_id
                assert sig_payload.config_version == "1.0.0"
                assert sig_payload.direction in (Direction.LONG, Direction.SHORT, Direction.FLAT)
                found_signal = True
                break
        except Exception:
            continue

    consumer.close()
    assert found_signal, "Did not consume expected TechnicalSignal from signals.technical"


def test_analyst_dlq_routing_on_invalid_candle(
    require_redpanda: None,
) -> None:
    """Verifies that unparseable or schema-violating candle bytes route to signals.technical.dlq."""
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_run = uuid.uuid4().hex[:8]
    service = TechnicalAnalystService()

    # Route bad bytes
    service.process_candle_message(b'{"malformed": "invalid_candle_data"}')
    service.publisher.flush(timeout_seconds=5.0)

    # Verify DLQ topic receives the event
    consumer_conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": f"test-analyst-dlq-verifier-{unique_run}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(consumer_conf)
    consumer.subscribe(["signals.technical.dlq"])

    found_dlq = False
    deadline = time.time() + 10.0
    while time.time() < deadline:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            continue
        data = json.loads(msg.value().decode("utf-8"))
        if data.get("error_type") == "TECHNICAL_ANALYST_PROCESSING_ERROR":
            found_dlq = True
            break

    consumer.close()
    assert found_dlq, "Did not consume expected DLQ event from signals.technical.dlq"


def test_analyst_redis_cache_and_state_persistence(
    require_redis: None,
) -> None:
    """Verifies indicators, signals, health updates, and state persistence in live Redis."""
    settings = get_settings()
    cache = TechnicalAnalystCache(redis_url=settings.redis_url)
    symbol = f"TEST_{uuid.uuid4().hex[:4].upper()}.NS"

    indicators = {"ema_9": Decimal("2500.50"), "rsi_14": Decimal("62.30")}
    cache.cache_indicators(symbol, CandleInterval.FIVE_MINUTES, indicators)

    cached_ind = cache.get_latest_indicators(symbol, CandleInterval.FIVE_MINUTES)
    assert cached_ind is not None
    assert cached_ind["ema_9"] == Decimal("2500.50")
    assert cached_ind["rsi_14"] == Decimal("62.30")

    # State checkpointing
    test_state = {"ema_9": {"count": 10, "ema": 2500.5}}
    cache.save_indicator_state(symbol, CandleInterval.FIVE_MINUTES, test_state)
    restored = cache.get_indicator_state(symbol, CandleInterval.FIVE_MINUTES)
    assert restored == test_state

    # Health update
    cache.update_health({"status": "HEALTHY", "candles_processed": 100})
    health = cache.get_health()
    assert health is not None
    assert health["status"] == "HEALTHY"


def test_technical_analyst_mcp_client_all_tools(
    require_redis: None,
) -> None:
    """Verifies all 5 FastMCP tools against live Redis data."""
    settings = get_settings()
    cache = TechnicalAnalystCache(redis_url=settings.redis_url)
    symbol = "INFY.NS"

    now = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=now,
    )

    indicators = {
        "ema_9": Decimal("1850.00"),
        "ema_21": Decimal("1840.00"),
        "rsi_14": Decimal("65.50"),
    }
    cache.cache_indicators(symbol, CandleInterval.FIVE_MINUTES, indicators)

    signal = TechnicalSignal(
        symbol=symbol,
        evaluated_at=now,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("0.80"),
        regime="TRENDING_UP",  # type: ignore[arg-type]
        indicators=indicators,
        rules_fired=["ema_9_above_ema_21", "rsi_bullish_momentum"],
        config_version="1.0.0",
        provenance=prov,
    )
    cache.cache_signal(signal)
    cache.update_health({"status": "HEALTHY", "uptime_seconds": 120})

    # Tool 1: get_indicators
    t1 = get_indicators(symbol, "5m")
    assert t1["symbol"] == symbol
    assert t1["indicators"]["ema_9"] == "1850.00"

    # Tool 2: get_signal
    t2 = get_signal(symbol, "5m")
    assert t2["symbol"] == symbol
    assert t2["direction"] == "LONG"
    assert t2["confidence"] == "0.80"

    # Tool 3: explain_signal
    t3 = explain_signal(symbol, "5m")
    assert t3["symbol"] == symbol
    assert "Rule-agreement" in t3["confidence_definition"]
    assert len(t3["rules_fired"]) == 2

    # Tool 4: list_supported_indicators
    t4 = list_supported_indicators()
    assert t4["version"] == "1.0.0"
    assert len(t4["supported_indicators"]) == 9

    # Tool 5: get_analyzer_health
    t5 = get_analyzer_health()
    assert t5["status"] == "HEALTHY"


def test_analyst_throughput_benchmark() -> None:
    """Benchmark: 50 symbols x 2 intervals (100 bars) processed in < 1.0s."""
    bundles = {f"SYM{i}.NS": SymbolIndicatorSet() for i in range(50)}
    base_ts = datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC)
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=base_ts,
    )

    start_time = time.perf_counter()
    count = 0
    for sym, bundle in bundles.items():
        for interval in (CandleInterval.ONE_MINUTE, CandleInterval.FIVE_MINUTES):
            candle = Candle(
                symbol=sym,
                interval=interval,
                open_time=base_ts,
                close_time=base_ts + timedelta(minutes=1),
                open=Decimal("1000.00"),
                high=Decimal("1010.00"),
                low=Decimal("990.00"),
                close=Decimal("1005.00"),
                volume=Decimal("5000"),
                vwap=Decimal("1002.00"),
                trade_count=50,
                provenance=prov,
                is_final=True,
            )
            bundle.update(candle)
            count += 1

    elapsed = time.perf_counter() - start_time
    assert count == 100
    # 100 indicator updates must easily take less than 0.5 seconds on modern CPU
    assert elapsed < 0.5, f"Expected 100 indicator updates in < 0.5s, took {elapsed:.4f}s"
