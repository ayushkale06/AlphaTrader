"""Integration tests for Market Data Streamer MCP Server pipeline.

Tests:
1. End-to-end ingestion: Simulated provider ticks publish to Redpanda `market.ticks` topic.
2. Candle generation: Minute boundary crossing publishes to `market.candles` topic.
3. DLQ routing: Malformed tick publishes to `market.ticks.dlq`.
4. Redis caching: Latest quote, candles, and feed health cached properly.
5. MCP tools verification: All 5 read-only tools return expected payloads against live cache.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.common.config import get_settings
from src.common.contracts import TopicName, validate_topic_message
from src.common.schemas.market import (
    CandleInterval,
    DataProvenance,
    FeedMode,
    Tick,
)
from src.mcp_servers.market_data_streamer.aggregator import CandleAggregator
from src.mcp_servers.market_data_streamer.cache import MarketDataCache
from src.mcp_servers.market_data_streamer.normalizer import TickNormalizer
from src.mcp_servers.market_data_streamer.publisher import StreamerPublisher
from src.mcp_servers.market_data_streamer.server import (
    get_candles,
    get_feed_health,
    get_latest_quote,
    get_market_status,
    list_instruments,
)

pytestmark = pytest.mark.integration


def test_market_data_streamer_pipeline_integration(
    require_redpanda: None,
    require_redis: None,
) -> None:
    """Ingest ticks, aggregate candles, route DLQ, populate Redis, and query MCP tools."""
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_run = uuid.uuid4().hex[:8]
    test_symbol = "TCS.NS"

    # Set up pipeline components
    cache = MarketDataCache(redis_url=settings.redis_url)
    publisher = StreamerPublisher()
    normalizer = TickNormalizer(default_provider="test_integration", feed_mode=FeedMode.SIMULATED)
    aggregator = CandleAggregator(intervals=(CandleInterval.ONE_MINUTE,))

    # 1. Ingest valid ticks
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=datetime.now(UTC),
    )

    t1 = Tick(
        symbol=test_symbol,
        timestamp=datetime(2026, 10, 13, 9, 15, 10, tzinfo=UTC),
        last_price=Decimal("4100.50"),
        volume=Decimal("150"),
        sequence_number=1,
        provenance=prov,
    )
    t2 = Tick(
        symbol=test_symbol,
        timestamp=datetime(2026, 10, 13, 9, 16, 10, tzinfo=UTC),  # Crosses 1-minute bucket boundary
        last_price=Decimal("4105.00"),
        volume=Decimal("200"),
        sequence_number=2,
        provenance=prov,
    )

    # Publish t1
    publisher.publish_tick(t1)
    cache.cache_quote(t1)
    completed_candles_1 = aggregator.process_tick(t1)
    assert len(completed_candles_1) == 0

    # Publish t2 -> triggers completed 1m candle for t1
    publisher.publish_tick(t2)
    cache.cache_quote(t2)
    completed_candles_2 = aggregator.process_tick(t2)
    assert len(completed_candles_2) == 1
    candle = completed_candles_2[0]
    publisher.publish_candle(candle)
    cache.cache_candle(candle)

    # 2. Inject malformed tick to test DLQ routing
    bad_raw = {
        "symbol": test_symbol,
        "timestamp": "2026-10-13T09:16:11Z",
        "last_price": "-10.00",  # Non-positive price
        "volume": "100",
    }
    norm_res = normalizer.normalize(bad_raw)
    assert not norm_res.is_valid
    publisher.publish_dlq(
        raw_payload=bad_raw,
        error_code=norm_res.error_code or "VALIDATION_ERROR",
        error_message=norm_res.error_message or "Invalid payload",
    )

    # Update feed health in cache
    cache.update_feed_health(
        status="RUNNING",
        symbols_count=50,
        ticks_ingested=2,
        candles_emitted=1,
        dlq_count=1,
    )

    publisher.flush(timeout=5.0)

    # 3. Verify Redis cache retrieval
    cached_quote = cache.get_latest_quote(test_symbol)
    assert cached_quote is not None
    assert cached_quote.last_price == Decimal("4105.00")

    cached_candles = cache.get_candles(test_symbol, CandleInterval.ONE_MINUTE, limit=5)
    assert len(cached_candles) >= 1
    assert cached_candles[0].open == Decimal("4100.50")

    health = cache.get_feed_health()
    assert health["status"] == "RUNNING"
    assert health["ticks_ingested"] == 2
    assert health["candles_emitted"] == 1
    assert health["dlq_count"] == 1

    # 4. Verify Redpanda consumption
    conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "group.id": f"integration-streamer-test-{unique_run}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
    consumer = Consumer(conf)
    consumer.subscribe(
        [
            TopicName.MARKET_TICKS.value,
            TopicName.MARKET_CANDLES.value,
            "market.ticks.dlq",
        ]
    )

    ticks_received = 0
    candles_received = 0
    dlq_received = 0
    start_poll = time.time()

    try:
        while time.time() - start_poll < 10.0:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                continue

            topic = msg.topic()
            payload = json.loads(msg.value().decode("utf-8"))

            if topic == TopicName.MARKET_TICKS.value:
                envelope = validate_topic_message(topic, payload)
                if envelope.payload.symbol == test_symbol:
                    ticks_received += 1
            elif topic == TopicName.MARKET_CANDLES.value:
                envelope = validate_topic_message(topic, payload)
                if envelope.payload.symbol == test_symbol:
                    candles_received += 1
            elif topic == "market.ticks.dlq":
                if payload.get("raw_payload", {}).get("symbol") == test_symbol:
                    dlq_received += 1

            if ticks_received >= 2 and candles_received >= 1 and dlq_received >= 1:
                break
    finally:
        consumer.close()

    assert ticks_received >= 2, f"Expected >=2 ticks, got {ticks_received}"
    assert candles_received >= 1, f"Expected >=1 candle, got {candles_received}"
    assert dlq_received >= 1, f"Expected >=1 dlq message, got {dlq_received}"

    # 5. Verify MCP tools against live Redis
    quote_res = get_latest_quote("TCS")
    assert quote_res["symbol"] == "TCS.NS"
    assert quote_res["last_price"] == Decimal("4105.00")

    candles_res = get_candles("TCS.NS", interval="1m")
    assert len(candles_res) >= 1
    assert candles_res[0]["open"] == Decimal("4100.50")

    status_res = get_market_status()
    assert "session" in status_res
    assert "is_open" in status_res

    instruments_res = list_instruments()
    assert "symbols" in instruments_res
    assert len(instruments_res["symbols"]) == 50

    feed_health_res = get_feed_health()
    assert feed_health_res["status"] == "RUNNING"
    assert feed_health_res["dlq_count"] == 1
