"""Unit tests for Market Data Streamer components.

Tests:
- TickNormalizer: valid ticks, sequence monotonicity, bid/ask inversion rejection,
  non-positive price, DLQ routing.
- CandleAggregator: OHLC invariants, IST bucket boundaries, multi-tick aggregation, flush_all.
- MarketCalendar: trading hours, weekends, holidays, pre-open, post-close.
- Providers: SimulatedProvider, ReplayProvider, LiveProvider (mocked).
- MCPServer: tool responses, instrument universe, feed health, quote caching.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.common.schemas.market import (
    CandleInterval,
    DataProvenance,
    FeedMode,
    MarketSessionPhase,
    Tick,
)
from src.mcp_servers.market_data_streamer.aggregator import CandleAggregator
from src.mcp_servers.market_data_streamer.calendar import MarketCalendar
from src.mcp_servers.market_data_streamer.normalizer import TickNormalizer
from src.mcp_servers.market_data_streamer.providers.base import MarketDataProvider
from src.mcp_servers.market_data_streamer.providers.factory import create_market_data_provider
from src.mcp_servers.market_data_streamer.providers.live import LiveProvider
from src.mcp_servers.market_data_streamer.providers.replay import ReplayProvider
from src.mcp_servers.market_data_streamer.providers.simulated import SimulatedProvider
from src.mcp_servers.market_data_streamer.server import (
    get_candles,
    get_feed_health,
    get_latest_quote,
    get_market_status,
    list_instruments,
)

# ============================================================================
# 1. TickNormalizer Tests
# ============================================================================


def test_normalizer_accepts_valid_raw_dict() -> None:
    normalizer = TickNormalizer(default_provider="test_feed", feed_mode=FeedMode.SIMULATED)
    raw = {
        "symbol": "RELIANCE.NS",
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "2500.50",
        "volume": "100",
        "bid_price": "2500.45",
        "ask_price": "2500.55",
        "sequence_number": 1,
    }
    result = normalizer.normalize(raw)
    assert result.is_valid
    assert result.tick is not None
    assert result.tick.symbol == "RELIANCE.NS"
    assert result.tick.last_price == Decimal("2500.50")
    assert result.tick.provenance is not None
    assert result.tick.provenance.provider == "test_feed"
    assert result.tick.provenance.feed_mode == FeedMode.SIMULATED


def test_normalizer_rejects_missing_symbol() -> None:
    normalizer = TickNormalizer()
    raw = {
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "2500.50",
        "volume": "100",
    }
    result = normalizer.normalize(raw)
    assert not result.is_valid
    assert result.error_code == "INVALID_TICK_PAYLOAD"
    assert normalizer.rejected_count == 1


def test_normalizer_rejects_non_positive_price() -> None:
    normalizer = TickNormalizer()
    raw = {
        "symbol": "TCS.NS",
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "0",
        "volume": "100",
    }
    result = normalizer.normalize(raw)
    assert not result.is_valid
    assert result.error_code == "NON_POSITIVE_PRICE"


def test_normalizer_rejects_inverted_bid_ask() -> None:
    normalizer = TickNormalizer()
    raw = {
        "symbol": "INFY.NS",
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "1500.00",
        "volume": "100",
        "bid_price": "1505.00",
        "ask_price": "1500.00",  # bid > ask
    }
    result = normalizer.normalize(raw)
    assert not result.is_valid
    assert result.error_code == "INVERTED_BID_ASK"


def test_normalizer_enforces_monotonic_sequence_per_symbol() -> None:
    normalizer = TickNormalizer()
    t1 = {
        "symbol": "RELIANCE.NS",
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "2500.00",
        "volume": "100",
        "sequence_number": 5,
    }
    t2_out_of_order = {
        "symbol": "RELIANCE.NS",
        "timestamp": "2026-10-13T09:15:01Z",
        "last_price": "2501.00",
        "volume": "100",
        "sequence_number": 4,  # Regression
    }
    res1 = normalizer.normalize(t1)
    assert res1.is_valid
    res2 = normalizer.normalize(t2_out_of_order)
    assert not res2.is_valid
    assert res2.error_code == "SEQUENCE_OUT_OF_ORDER"


def test_normalizer_passes_through_valid_tick_object() -> None:
    normalizer = TickNormalizer()
    prov = DataProvenance(
        provider="sim",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
    )
    tick = Tick(
        symbol="TCS.NS",
        timestamp=datetime(2026, 10, 13, 9, 15, tzinfo=UTC),
        last_price=Decimal("3500.00"),
        volume=Decimal("50"),
        sequence_number=10,
        provenance=prov,
    )
    result = normalizer.normalize(tick)
    assert result.is_valid
    assert result.tick == tick


def test_normalizer_handles_datetime_object_and_missing_timestamp() -> None:
    normalizer = TickNormalizer()
    # Datetime object directly
    dt_now = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
    raw1 = {
        "symbol": "TCS.NS",
        "timestamp": dt_now,
        "last_price": "2500.00",
        "volume": "10",
        "sequence_number": 1,
    }
    r1 = normalizer.normalize(raw1)
    assert r1.is_valid
    assert r1.tick is not None
    assert r1.tick.timestamp == dt_now

    # Missing timestamp defaults to now(UTC)
    raw2 = {
        "symbol": "TCS.NS",
        "timestamp": None,
        "last_price": "2501.00",
        "volume": "10",
        "sequence_number": 2,
    }
    r2 = normalizer.normalize(raw2)
    assert r2.is_valid
    assert r2.tick is not None
    assert isinstance(r2.tick.timestamp, datetime)


def test_normalizer_handles_dict_provenance_and_malformed_provenance_exception() -> None:
    normalizer = TickNormalizer()
    # Dict provenance parsing
    prov_dict = {
        "provider": "custom_vendor",
        "feed_mode": "REPLAY",
        "is_delayed": True,
        "delay_seconds": 60,
    }
    raw = {
        "symbol": "INFY.NS",
        "timestamp": "2026-10-13T09:15:00Z",
        "last_price": "1500.00",
        "volume": "50",
        "sequence_number": 1,
        "provenance": prov_dict,
    }
    r1 = normalizer.normalize(raw)
    assert r1.is_valid
    assert r1.tick is not None
    assert r1.tick.provenance.provider == "custom_vendor"
    assert r1.tick.provenance.feed_mode == FeedMode.REPLAY

    # Malformed volume or fields triggering ValidationError
    raw_bad = {
        "symbol": "INFY.NS",
        "timestamp": "2026-10-13T09:15:01Z",
        "last_price": "1500.00",
        "volume": "-10",  # Negative volume fails Tick validator
        "sequence_number": 2,
    }
    r2 = normalizer.normalize(raw_bad)
    assert not r2.is_valid
    assert r2.error_code == "INVALID_TICK_PAYLOAD"
    assert "Schema normalization error" in r2.error_message


# ============================================================================
# 2. CandleAggregator Tests & Invariants
# ============================================================================


def test_candle_aggregator_single_tick_and_flush() -> None:
    aggregator = CandleAggregator(intervals=(CandleInterval.ONE_MINUTE,))
    prov = DataProvenance(
        provider="sim",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
    )
    tick = Tick(
        symbol="RELIANCE.NS",
        timestamp=datetime(2026, 10, 13, 9, 15, 30, tzinfo=UTC),
        last_price=Decimal("2500.00"),
        volume=Decimal("100"),
        sequence_number=1,
        provenance=prov,
    )
    completed = aggregator.process_tick(tick)
    assert len(completed) == 0

    flushed = aggregator.flush_all()
    assert len(flushed) == 1
    candle = flushed[0]
    assert candle.symbol == "RELIANCE.NS"
    assert candle.interval == CandleInterval.ONE_MINUTE
    assert candle.open == Decimal("2500.00")
    assert candle.high == Decimal("2500.00")
    assert candle.low == Decimal("2500.00")
    assert candle.close == Decimal("2500.00")
    assert candle.volume == Decimal("100")
    assert candle.trade_count == 1
    assert candle.provenance is not None


def test_candle_aggregator_emits_on_interval_boundary() -> None:
    aggregator = CandleAggregator(intervals=(CandleInterval.ONE_MINUTE,))
    prov = DataProvenance(
        provider="sim",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
    )

    t1 = Tick(
        symbol="RELIANCE.NS",
        timestamp=datetime(2026, 10, 13, 9, 15, 10, tzinfo=UTC),
        last_price=Decimal("2500.00"),
        volume=Decimal("100"),
        sequence_number=1,
        provenance=prov,
    )
    t2 = Tick(
        symbol="RELIANCE.NS",
        timestamp=datetime(2026, 10, 13, 9, 15, 40, tzinfo=UTC),
        last_price=Decimal("2510.00"),
        volume=Decimal("50"),
        sequence_number=2,
        provenance=prov,
    )
    t3 = Tick(
        symbol="RELIANCE.NS",
        timestamp=datetime(2026, 10, 13, 9, 16, 5, tzinfo=UTC),  # Next minute
        last_price=Decimal("2505.00"),
        volume=Decimal("75"),
        sequence_number=3,
        provenance=prov,
    )

    aggregator.process_tick(t1)
    aggregator.process_tick(t2)
    completed = aggregator.process_tick(t3)

    assert len(completed) == 1
    c = completed[0]
    assert c.open == Decimal("2500.00")
    assert c.high == Decimal("2510.00")
    assert c.low == Decimal("2500.00")
    assert c.close == Decimal("2510.00")
    assert c.volume == Decimal("150")
    assert c.trade_count == 2


@given(
    prices=st.lists(
        st.decimals(
            min_value=Decimal("1.00"),
            max_value=Decimal("10000.00"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=30,
    )
)
def test_hypothesis_candle_ohlc_invariants(prices: list[Decimal]) -> None:
    """Hypothesis property: high >= all prices >= low, open == first, close == last."""
    aggregator = CandleAggregator(intervals=(CandleInterval.ONE_MINUTE,))
    prov = DataProvenance(
        provider="sim",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
    )

    base_time = datetime(2026, 10, 13, 9, 15, 0, tzinfo=UTC)
    for i, p in enumerate(prices):
        t = Tick(
            symbol="INFY.NS",
            timestamp=base_time,
            last_price=p,
            volume=Decimal("10"),
            sequence_number=i + 1,
            provenance=prov,
        )
        aggregator.process_tick(t)

    flushed = aggregator.flush_all()
    assert len(flushed) == 1
    c = flushed[0]

    assert c.open == prices[0]
    assert c.close == prices[-1]
    assert c.high == max(prices)
    assert c.low == min(prices)
    assert c.high >= c.low
    assert c.high >= c.open
    assert c.high >= c.close
    assert c.low <= c.open
    assert c.low <= c.close


# ============================================================================
# 3. MarketCalendar Tests
# ============================================================================


def test_calendar_trading_hours_and_phases() -> None:
    cal = MarketCalendar()

    # Regular Tuesday 09:15 IST is 03:45 UTC
    t_open = datetime(2026, 10, 13, 3, 45, tzinfo=UTC)
    st_open = cal.evaluate_status(t_open)
    assert st_open.is_open is True
    assert st_open.session == MarketSessionPhase.OPEN

    # Tuesday 09:05 IST (03:35 UTC) is PRE_OPEN
    t_pre = datetime(2026, 10, 13, 3, 35, tzinfo=UTC)
    st_pre = cal.evaluate_status(t_pre)
    assert st_pre.is_open is False
    assert st_pre.session == MarketSessionPhase.PRE_OPEN

    # Tuesday 15:35 IST (10:05 UTC) is POST_CLOSE
    t_post = datetime(2026, 10, 13, 10, 5, tzinfo=UTC)
    st_post = cal.evaluate_status(t_post)
    assert st_post.is_open is False
    assert st_post.session == MarketSessionPhase.POST_CLOSE

    # Weekend: Sunday 2026-10-18
    t_sun = datetime(2026, 10, 18, 5, 0, tzinfo=UTC)
    st_sun = cal.evaluate_status(t_sun)
    assert st_sun.is_open is False
    assert st_sun.session == MarketSessionPhase.CLOSED
    assert "Weekend" in (st_sun.reason or "")

    # Holiday: Republic Day 2026-01-26
    t_hol = datetime(2026, 1, 26, 5, 0, tzinfo=UTC)
    st_hol = cal.evaluate_status(t_hol)
    assert st_hol.is_open is False
    assert st_hol.session == MarketSessionPhase.CLOSED
    assert "Republic Day" in (st_hol.reason or "")


# ============================================================================
# 4. Providers Tests
# ============================================================================


@pytest.mark.asyncio
async def test_simulated_provider_yields_ticks() -> None:
    provider = SimulatedProvider(symbols=["RELIANCE.NS"], tick_interval_seconds=0.01, seed=42)
    assert provider.name == "simulated_feed"
    assert provider.feed_mode == FeedMode.SIMULATED
    assert not provider.is_delayed

    await provider.connect()
    ticks: list[Tick] = []
    async for tick in provider.stream_quotes():
        assert isinstance(tick, Tick)
        ticks.append(tick)
        if len(ticks) >= 5:
            break

    await provider.close()
    assert len(ticks) == 5
    for t in ticks:
        assert t.symbol == "RELIANCE.NS"
        assert t.last_price > 0
        assert t.provenance is not None
        assert t.provenance.feed_mode == FeedMode.SIMULATED


@pytest.mark.asyncio
async def test_replay_provider_yields_sample_ticks() -> None:
    provider = ReplayProvider(speed_multiplier=100.0)
    assert provider.name == "replay_feed"
    assert provider.feed_mode == FeedMode.REPLAY

    await provider.connect()
    ticks: list[Tick] = []
    async for tick in provider.stream_quotes():
        assert isinstance(tick, Tick)
        ticks.append(tick)
        if len(ticks) >= 3:
            break

    await provider.close()
    assert len(ticks) >= 3
    for t in ticks:
        assert t.provenance is not None
        assert t.provenance.feed_mode == FeedMode.REPLAY


@pytest.mark.asyncio
async def test_live_provider_with_mocked_http() -> None:
    provider = LiveProvider(symbols=["TCS.NS"], poll_interval_seconds=2.0)
    assert provider.name == "yahoo_finance_delayed"
    assert provider.feed_mode == FeedMode.LIVE
    assert provider.is_delayed
    assert provider.delay_seconds == 900

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 3650.0,
                        "regularMarketVolume": 12000,
                        "regularMarketTime": 1791873900,
                    }
                }
            ]
        }
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        await provider.connect()

        quotes = []
        async for quote in provider.stream_quotes():
            quotes.append(quote)
            break

        await provider.close()
        assert len(quotes) == 1
        q = quotes[0]
        assert isinstance(q, Tick)
        assert q.symbol == "TCS.NS"
        assert q.last_price == Decimal("3650.0")


def test_provider_factory() -> None:
    sim = create_market_data_provider(FeedMode.SIMULATED)
    assert isinstance(sim, SimulatedProvider)

    rep = create_market_data_provider(FeedMode.REPLAY)
    assert isinstance(rep, ReplayProvider)

    live = create_market_data_provider(FeedMode.LIVE)
    assert isinstance(live, LiveProvider)


def test_market_data_provider_interface_is_swappable() -> None:
    """Verify any custom provider implementing MarketDataProvider interface plugs into pipeline."""

    class MockCustomBrokerProvider(MarketDataProvider):
        @property
        def name(self) -> str:
            return "mock_broker"

        @property
        def feed_mode(self) -> FeedMode:
            return FeedMode.LIVE

        @property
        def is_delayed(self) -> bool:
            return False

        @property
        def delay_seconds(self) -> int | None:
            return 0

        async def connect(self) -> None:
            pass

        async def subscribe(self, symbols: Sequence[str]) -> None:
            pass

        async def stream_quotes(self):
            prov = DataProvenance(
                provider=self.name,
                feed_mode=self.feed_mode,
                is_delayed=False,
                delay_seconds=0,
            )
            yield Tick(
                symbol="RELIANCE.NS",
                timestamp=datetime(2026, 10, 13, 9, 15, tzinfo=UTC),
                last_price=Decimal("3000.00"),
                volume=Decimal("100"),
                sequence_number=1,
                provenance=prov,
            )

        async def close(self) -> None:
            pass

    custom_provider = MockCustomBrokerProvider()
    assert isinstance(custom_provider, MarketDataProvider)
    assert custom_provider.name == "mock_broker"
    assert custom_provider.feed_mode == FeedMode.LIVE
    assert not custom_provider.is_delayed


# ============================================================================
# 5. MCP Server Tools Tests
# ============================================================================


def test_mcp_server_list_instruments() -> None:
    data = list_instruments()
    assert "symbols" in data
    assert len(data["symbols"]) == 50
    assert "RELIANCE.NS" in data["symbols"]
    assert "TCS.NS" in data["symbols"]
    assert "warning" in data


def test_mcp_server_get_market_status() -> None:
    status = get_market_status()
    assert "session" in status
    assert "is_open" in status
    assert "as_of" in status


def test_mcp_server_get_latest_quote_not_found() -> None:
    with patch("src.mcp_servers.market_data_streamer.server.get_cache") as mock_cache_getter:
        mock_cache = MagicMock()
        mock_cache.get_latest_quote.return_value = None
        mock_cache_getter.return_value = mock_cache

        res = get_latest_quote("UNKNOWN.NS")
        assert res["error"] == "QUOTE_NOT_FOUND"


def test_mcp_server_get_latest_quote_found() -> None:
    with patch("src.mcp_servers.market_data_streamer.server.get_cache") as mock_cache_getter:
        prov = DataProvenance(
            provider="sim",
            feed_mode=FeedMode.SIMULATED,
            is_delayed=False,
            delay_seconds=0,
        )
        sample_tick = Tick(
            symbol="RELIANCE.NS",
            timestamp=datetime(2026, 10, 13, 9, 15, tzinfo=UTC),
            last_price=Decimal("2500.00"),
            volume=Decimal("100"),
            sequence_number=1,
            provenance=prov,
        )
        mock_cache = MagicMock()
        mock_cache.get_latest_quote.return_value = sample_tick
        mock_cache_getter.return_value = mock_cache

        res = get_latest_quote("RELIANCE")
        assert res["symbol"] == "RELIANCE.NS"
        assert res["last_price"] == Decimal("2500.00")


def test_mcp_server_get_candles_invalid_interval() -> None:
    res = get_candles("RELIANCE.NS", interval="invalid")
    assert len(res) == 1
    assert res[0]["error"] == "INVALID_INTERVAL"


def test_mcp_server_get_feed_health() -> None:
    with patch("src.mcp_servers.market_data_streamer.server.get_cache") as mock_cache_getter:
        mock_cache = MagicMock()
        mock_cache.get_feed_health.return_value = {
            "status": "RUNNING",
            "symbols_count": 50,
            "ticks_ingested": 1024,
            "candles_emitted": 42,
            "dlq_count": 0,
        }
        mock_cache_getter.return_value = mock_cache

        res = get_feed_health()
        assert res["status"] == "RUNNING"
        assert res["ticks_ingested"] == 1024


def test_market_data_cache_direct_client_and_none() -> None:
    """Exercise MarketDataCache with mocked redis client directly."""
    from src.mcp_servers.market_data_streamer.cache import MarketDataCache

    mock_r = MagicMock()
    mock_r.get.return_value = None
    c = MarketDataCache(redis_client=mock_r)
    assert c.get_latest_quote("UNKNOWN.NS") is None
    h = c.get_feed_health()
    assert h["status"] == "STOPPED"


# ============================================================================
# 6. Streamer Service & Publisher Unit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_streamer_service_lifecycle_and_shutdown() -> None:
    """Test streamer service start, tick processing, and clean stop."""
    from src.mcp_servers.market_data_streamer.streamer import MarketDataStreamerService

    with (
        patch("confluent_kafka.Producer"),
        patch("redis.Redis"),
    ):
        streamer = MarketDataStreamerService(
            feed_mode=FeedMode.SIMULATED,
            symbols=["RELIANCE.NS"],
        )
        task = asyncio.create_task(streamer.start())
        await asyncio.sleep(0.3)
        await streamer.stop()
        await task
        assert streamer._ticks_count >= 1


@pytest.mark.asyncio
async def test_streamer_service_routes_bad_tick_to_dlq() -> None:
    """Verify service routes bad tick to DLQ publisher."""
    from src.mcp_servers.market_data_streamer.streamer import MarketDataStreamerService

    with (
        patch("confluent_kafka.Producer"),
        patch("redis.Redis"),
    ):
        streamer = MarketDataStreamerService(
            feed_mode=FeedMode.SIMULATED,
            symbols=["RELIANCE.NS"],
        )

        class MockBadProvider(MarketDataProvider):
            @property
            def name(self) -> str:
                return "bad"

            @property
            def feed_mode(self) -> FeedMode:
                return FeedMode.SIMULATED

            @property
            def is_delayed(self) -> bool:
                return False

            @property
            def delay_seconds(self) -> int | None:
                return 0

            async def connect(self) -> None:
                pass

            async def subscribe(self, symbols: Sequence[str]) -> None:
                pass

            async def stream_quotes(self):
                yield {"symbol": "RELIANCE.NS", "last_price": "-5.0", "volume": "10"}
                await asyncio.sleep(0.1)

            async def close(self) -> None:
                pass

        streamer.provider = MockBadProvider()
        streamer.publisher = MagicMock()
        task = asyncio.create_task(streamer.start())
        await asyncio.sleep(0.2)
        await streamer.stop()
        await task
        assert streamer.publisher.publish_dlq.called


def test_aggregator_all_interval_boundaries() -> None:
    """Exercise get_candle_boundary for all intervals."""
    from src.mcp_servers.market_data_streamer.aggregator import get_candle_boundary

    t = datetime(2026, 10, 13, 10, 37, 25, tzinfo=UTC)
    for interval in (
        CandleInterval.ONE_MINUTE,
        CandleInterval.FIVE_MINUTES,
        CandleInterval.FIFTEEN_MINUTES,
        CandleInterval.ONE_HOUR,
        CandleInterval.ONE_DAY,
    ):
        start, end = get_candle_boundary(t, interval)
        assert start < end


def test_streamer_publisher_unit_methods() -> None:
    """Test publisher produce and flush methods with mocked Kafka."""
    from src.common.schemas.system import EventSeverity, SystemEventType
    from src.mcp_servers.market_data_streamer.publisher import StreamerPublisher

    with patch("confluent_kafka.Producer") as mock_prod_cls:
        mock_prod = MagicMock()
        mock_prod_cls.return_value = mock_prod

        pub = StreamerPublisher()

        # Publish tick
        prov = DataProvenance(
            provider="sim",
            feed_mode=FeedMode.SIMULATED,
            is_delayed=False,
            delay_seconds=0,
        )
        tick = Tick(
            symbol="TCS.NS",
            timestamp=datetime(2026, 10, 13, 9, 15, tzinfo=UTC),
            last_price=Decimal("4000.00"),
            volume=Decimal("10"),
            sequence_number=1,
            provenance=prov,
        )
        pub.publish_tick(tick)
        assert mock_prod.produce.called

        # Publish DLQ
        pub.publish_dlq({"symbol": "BAD"}, "Error occurred", error_code="ERR")
        assert mock_prod.produce.call_count >= 2

        # Publish System Event
        pub.publish_system_event(
            event_type=SystemEventType.HEARTBEAT,
            message="Heartbeat ok",
            severity=EventSeverity.INFO,
        )
        assert mock_prod.produce.call_count >= 3

        pub.flush(timeout=1.0)
        assert mock_prod.flush.called


def test_normalizer_additional_error_branches() -> None:
    """Cover normalizer unparsable last_price, bad timestamp, and exception branches."""
    normalizer = TickNormalizer()
    res = normalizer.normalize({"symbol": "TCS.NS", "last_price": "invalid_num"})
    assert not res.is_valid
    assert res.error_code == "NON_POSITIVE_PRICE"


@pytest.mark.asyncio
async def test_replay_provider_subscribe_and_loop_and_missing_file() -> None:
    """Test ReplayProvider file not found and subscription filtering."""
    import tempfile

    with pytest.raises(FileNotFoundError):
        p_missing = ReplayProvider(filepath="non_existent_file.jsonl")
        await p_missing.connect()

    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".jsonl") as f:
        json_line = (
            '{"symbol": "INFY.NS", "timestamp": "2026-10-13T03:45:00Z", '
            '"last_price": "1800.00", "volume": "50"}\n'
        )
        f.write(json_line)
        f_path = f.name

    p = ReplayProvider(filepath=f_path, speed_multiplier=100.0)
    await p.connect()
    await p.subscribe(["OTHER.NS"])  # Filter out INFY.NS
    ticks = []
    async for tick in p.stream_quotes():
        ticks.append(tick)
    await p.close()
    assert len(ticks) == 0


@pytest.mark.asyncio
async def test_live_provider_error_handling_and_subscription() -> None:
    """Test LiveProvider error branches, subscription additions, and empty chart results."""
    provider = LiveProvider(symbols=["RELIANCE.NS"])
    await provider.connect()
    await provider.subscribe(["TCS.NS"])
    assert "TCS.NS" in provider._symbols

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        tick = await provider._fetch_quote_for_symbol("RELIANCE.NS")
        assert tick is None

    await provider.close()


@pytest.mark.asyncio
async def test_streamer_heartbeat_iteration() -> None:
    """Exercise _heartbeat_loop single pass logic directly."""
    from src.mcp_servers.market_data_streamer.streamer import MarketDataStreamerService

    with (
        patch("confluent_kafka.Producer"),
        patch("redis.Redis"),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_sleep.side_effect = [None, asyncio.CancelledError]
        streamer = MarketDataStreamerService(
            feed_mode=FeedMode.SIMULATED,
            symbols=["RELIANCE.NS"],
        )
        streamer._running = True
        streamer.cache = MagicMock()
        streamer.publisher = MagicMock()

        # Simulate a stalled state
        streamer._last_tick_time = datetime(2020, 1, 1, tzinfo=UTC)

        await streamer._heartbeat_loop()
        assert streamer.cache.update_feed_health.called
        assert streamer.publisher.publish_system_event.called


def test_load_nifty50_symbols_helper() -> None:
    """Exercise load_nifty50_symbols helper function."""
    from src.mcp_servers.market_data_streamer.streamer import load_nifty50_symbols

    syms = load_nifty50_symbols()
    assert len(syms) == 50
    assert "RELIANCE.NS" in syms


@pytest.mark.asyncio
async def test_streamer_service_live_mode_closed_market_branch() -> None:
    """Exercise LIVE mode market closed idling branch in streamer."""
    from src.common.schemas.market import MarketSessionPhase, MarketStatus
    from src.mcp_servers.market_data_streamer.streamer import MarketDataStreamerService

    with (
        patch("confluent_kafka.Producer"),
        patch("redis.Redis"),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        streamer = MarketDataStreamerService(
            feed_mode=FeedMode.LIVE,
            symbols=["RELIANCE.NS"],
        )
        # Mock calendar to report market CLOSED
        closed_status = MarketStatus(
            exchange="NSE",
            timezone="Asia/Kolkata",
            as_of=datetime.now(UTC),
            session=MarketSessionPhase.CLOSED,
            is_open=False,
            reason="Market is closed test",
        )
        streamer.calendar.evaluate_status = MagicMock(return_value=closed_status)

        class MockEmptyLiveProvider(MarketDataProvider):
            @property
            def name(self) -> str:
                return "live_mock"

            @property
            def feed_mode(self) -> FeedMode:
                return FeedMode.LIVE

            @property
            def is_delayed(self) -> bool:
                return True

            @property
            def delay_seconds(self) -> int | None:
                return 900

            async def connect(self) -> None:
                pass

            async def subscribe(self, symbols: Sequence[str]) -> None:
                pass

            async def stream_quotes(self):
                yield {"symbol": "RELIANCE.NS", "last_price": "2500.0", "volume": "100"}
                streamer._running = False

            async def close(self) -> None:
                pass

        streamer.provider = MockEmptyLiveProvider()
        task = asyncio.create_task(streamer.start())
        await asyncio.sleep(0.05)
        await streamer.stop()
        await task


def test_provider_factory_invalid_mode() -> None:
    """Test provider factory with invalid mode string."""
    with pytest.raises(ValueError):
        create_market_data_provider("UNKNOWN_MODE")
