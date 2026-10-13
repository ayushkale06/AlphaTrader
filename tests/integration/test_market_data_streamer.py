"""Integration tests for Market Data Streamer MCP Server pipeline.

Tests:
1. test_streamer_ingestion_and_candle_pipeline_to_redpanda:
   - Ingests simulated ticks, crosses minute boundary, produces completed candle.
2. test_streamer_dlq_routing_on_malformed_data:
   - Injects bad payload (non-positive price), verifies publisher routes to DLQ.
3. test_streamer_redis_cache_population:
   - Verifies quotes, candles (sorted set), and feed health cached in live Redis.
4. test_mcp_client_all_tools_execution:
   - Queries all 5 MCP tools against live Redis cache.
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


def test_streamer_ingestion_and_candle_pipeline_to_redpanda(
    require_redpanda: None,
) -> None:
    """Simulated ticks publish to Redpanda and completed candles emit on bucket boundary."""
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_run = uuid.uuid4().hex[:8]
    test_symbol = "RELIANCE.NS"

    publisher = StreamerPublisher()
    aggregator = CandleAggregator(intervals=(CandleInterval.ONE_MINUTE,))

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
        last_price=Decimal("2950.00"),
        volume=Decimal("100"),
        sequence_number=1,
        provenance=prov,
    )
    t2 = Tick(
        symbol=test_symbol,
        timestamp=datetime(2026, 10, 13, 9, 16, 10, tzinfo=UTC),
        last_price=Decimal("2955.00"),
        volume=Decimal("150"),
        sequence_number=2,
        provenance=prov,
    )

    publisher.publish_tick(t1)
    c1 = aggregator.process_tick(t1)
    assert len(c1) == 0

    publisher.publish_tick(t2)
    c2 = aggregator.process_tick(t2)
    assert len(c2) == 1
    candle = c2[0]
    publisher.publish_candle(candle)
    publisher.flush(timeout=5.0)

    # Consume and verify from Redpanda
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"test-streamer-pipe-{unique_run}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([TopicName.MARKET_TICKS.value, TopicName.MARKET_CANDLES.value])

    ticks_got = 0
    candles_got = 0
    start = time.time()
    try:
        while time.time() - start < 10.0:
            msg = consumer.poll(timeout=1.0)
            if not msg or msg.error():
                continue
            payload = json.loads(msg.value().decode("utf-8"))
            if msg.topic() == TopicName.MARKET_TICKS.value:
                env = validate_topic_message(msg.topic(), payload)
                if env.payload.symbol == test_symbol:
                    ticks_got += 1
            elif msg.topic() == TopicName.MARKET_CANDLES.value:
                env = validate_topic_message(msg.topic(), payload)
                if env.payload.symbol == test_symbol:
                    candles_got += 1
            if ticks_got >= 2 and candles_got >= 1:
                break
    finally:
        consumer.close()

    assert ticks_got >= 2
    assert candles_got >= 1


def test_streamer_dlq_routing_on_malformed_data(
    require_redpanda: None,
) -> None:
    """Malformed quote tick (negative price) is routed to market.ticks.dlq."""
    from confluent_kafka import Consumer

    settings = get_settings()
    unique_run = uuid.uuid4().hex[:8]
    test_symbol = "INFY.NS"

    publisher = StreamerPublisher()
    normalizer = TickNormalizer(default_provider="test_dlq", feed_mode=FeedMode.SIMULATED)

    bad_raw = {
        "symbol": test_symbol,
        "timestamp": "2026-10-13T09:16:11Z",
        "last_price": "-25.00",
        "volume": "50",
    }
    norm_res = normalizer.normalize(bad_raw)
    assert not norm_res.is_valid
    assert norm_res.error_code == "NON_POSITIVE_PRICE"

    publisher.publish_dlq(
        raw_payload=bad_raw,
        error_message=norm_res.error_message or "Error",
        error_code=norm_res.error_code,
    )
    publisher.flush(timeout=5.0)

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"test-streamer-dlq-{unique_run}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["market.ticks.dlq"])

    dlq_received = False
    start = time.time()
    try:
        while time.time() - start < 10.0:
            msg = consumer.poll(timeout=1.0)
            if not msg or msg.error():
                continue
            payload = json.loads(msg.value().decode("utf-8"))
            if payload.get("raw_payload", {}).get("symbol") == test_symbol:
                assert payload["error_code"] == "NON_POSITIVE_PRICE"
                dlq_received = True
                break
    finally:
        consumer.close()

    assert dlq_received, "Failed to receive DLQ message from market.ticks.dlq"


def test_streamer_redis_cache_population(
    require_redis: None,
) -> None:
    """Quotes, candles, and feed health populate Redis and query back correctly."""
    settings = get_settings()
    cache = MarketDataCache(redis_url=settings.redis_url)
    test_symbol = "HDFCBANK.NS"

    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=datetime.now(UTC),
    )
    tick = Tick(
        symbol=test_symbol,
        timestamp=datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC),
        last_price=Decimal("1650.00"),
        volume=Decimal("500"),
        sequence_number=1,
        provenance=prov,
    )
    cache.cache_quote(tick)

    # Verify quote retrieved
    retrieved = cache.get_latest_quote(test_symbol)
    assert retrieved is not None
    assert retrieved.symbol == test_symbol
    assert retrieved.last_price == Decimal("1650.00")

    # Cache candle and verify sorted set
    from src.common.schemas.market import Candle

    candle = Candle(
        symbol=test_symbol,
        interval=CandleInterval.ONE_MINUTE,
        open_time=datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC),
        close_time=datetime(2026, 10, 13, 9, 16, 0, tzinfo=UTC),
        open=Decimal("1650.00"),
        high=Decimal("1655.00"),
        low=Decimal("1648.00"),
        close=Decimal("1652.00"),
        volume=Decimal("1200"),
        trade_count=10,
        provenance=prov,
    )
    cache.cache_candle(candle)

    candles = cache.get_candles(test_symbol, CandleInterval.ONE_MINUTE, limit=5)
    assert len(candles) >= 1
    assert candles[0].close == Decimal("1652.00")

    # Update feed health
    cache.update_feed_health(
        status="RUNNING",
        symbols_count=50,
        ticks_ingested=5000,
        candles_emitted=120,
        dlq_count=2,
    )
    health = cache.get_feed_health()
    assert health["status"] == "RUNNING"
    assert health["ticks_ingested"] == 5000


def test_mcp_client_all_tools_execution(
    require_redis: None,
) -> None:
    """MCP client successfully queries all 5 registered tools against live cache."""
    settings = get_settings()
    cache = MarketDataCache(redis_url=settings.redis_url)

    # Populate a known test quote
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=datetime.now(UTC),
    )
    tick = Tick(
        symbol="ICICIBANK.NS",
        timestamp=datetime(2026, 10, 13, 9, 20, 0, tzinfo=UTC),
        last_price=Decimal("1240.50"),
        volume=Decimal("300"),
        sequence_number=1,
        provenance=prov,
    )
    cache.cache_quote(tick)

    # 1. get_latest_quote
    q = get_latest_quote("ICICIBANK")
    assert q["symbol"] == "ICICIBANK.NS"
    assert q["last_price"] == Decimal("1240.50")

    # 2. get_candles
    c = get_candles("ICICIBANK.NS", interval="1m")
    assert isinstance(c, list)

    # 3. get_market_status
    st = get_market_status()
    assert st["exchange"] == "NSE"
    assert "session" in st
    assert "is_open" in st

    # 4. list_instruments
    inst = list_instruments()
    assert inst["index_name"] == "NIFTY 50"
    assert len(inst["symbols"]) == 50

    # 5. get_feed_health
    h = get_feed_health()
    assert "status" in h
    assert "ticks_ingested" in h
