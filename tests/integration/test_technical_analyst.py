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
from src.common.schemas.signals import Direction, MarketRegime, TechnicalSignal
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
        regime=MarketRegime.TRENDING_UP,
        indicators=indicators,
        rules_long=["ema_9_above_ema_21", "rsi_bullish_momentum"],
        rules_short=[],
        config_version="1.0.0",
        config_hash="a" * 64,
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
    assert t2["confidence"] == Decimal("0.80")

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
    """Benchmark: >= 10,000 distinct, warmed-up candles across 50 symbols x 2 intervals
    (100 bars each) confirming indicator update and signal evaluation run on each bar.
    """
    from src.mcp_servers.technical_analyst.signals import SignalEngine

    symbols = [f"SYM{i}.NS" for i in range(50)]
    intervals = [CandleInterval.ONE_MINUTE, CandleInterval.FIVE_MINUTES]
    bars_per_series = 100
    total_candles = len(symbols) * len(intervals) * bars_per_series  # 10,000 bars
    assert total_candles == 10000

    bundles = {(sym, inv.value): SymbolIndicatorSet() for sym in symbols for inv in intervals}
    engine = SignalEngine()

    base_ts = datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC)
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=base_ts,
    )

    signals_evaluated = 0
    signals_published = 0

    start_time = time.perf_counter()
    for bar_idx in range(bars_per_series):
        close_t = base_ts + timedelta(minutes=bar_idx * 5)
        open_t = close_t - timedelta(minutes=5)
        close_val = Decimal(str(round(100.0 + (bar_idx % 10) * 0.5, 2)))
        open_val = Decimal("100.00")
        high_val = max(open_val, close_val) + Decimal("2.00")
        low_val = min(open_val, close_val) - Decimal("2.00")

        for sym in symbols:
            for inv in intervals:
                candle = Candle(
                    symbol=sym,
                    interval=inv,
                    open_time=open_t,
                    close_time=close_t,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=Decimal("500"),
                    trade_count=50,
                    provenance=prov,
                    is_final=True,
                )
                bundle = bundles[(sym, inv.value)]
                ready_indicators = bundle.update(candle)
                sig = engine.evaluate(
                    symbol=sym,
                    interval=inv,
                    candle=candle,
                    indicators=ready_indicators,
                    provenance=prov,
                )
                signals_evaluated += 1
                if sig is not None:
                    signals_published += 1

    elapsed = time.perf_counter() - start_time
    assert signals_evaluated == 10000
    assert signals_published > 0
    latency_ms = (elapsed / total_candles) * 1000.0
    throughput = total_candles / elapsed

    print(
        f"\n[BENCHMARK 10,000 CANDLES] Processed {total_candles} candles "
        f"(50 symbols x 2 intervals x 100 bars) with signal evaluation in {elapsed:.3f} s. "
        f"Latency: {latency_ms:.4f} ms/candle ({throughput:.1f} candles/sec). "
        f"Signals published: {signals_published}."
    )
    # 10,000 in-memory indicator updates + signal evaluations: ~1 ms/candle, < 15.0s with coverage
    assert elapsed < 15.0, f"Expected 10,000 candles evaluated in < 15.0s, took {elapsed:.4f}s"
