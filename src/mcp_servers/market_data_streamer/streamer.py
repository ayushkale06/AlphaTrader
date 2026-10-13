"""Standalone Market Data Streaming Worker.

Ingests NIFTY 50 ticks from the configured provider (SIMULATED | REPLAY | LIVE),
normalizes quotes, aggregates 1m/5m candles, publishes to Redpanda, populates Redis,
monitors NSE market hours, and manages health heartbeats and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.config import get_settings
from src.common.schemas.market import FeedMode
from src.common.schemas.system import EventSeverity, SystemEventType
from src.mcp_servers.market_data_streamer.aggregator import CandleAggregator
from src.mcp_servers.market_data_streamer.cache import MarketDataCache
from src.mcp_servers.market_data_streamer.calendar import MarketCalendar
from src.mcp_servers.market_data_streamer.normalizer import TickNormalizer
from src.mcp_servers.market_data_streamer.providers import create_market_data_provider
from src.mcp_servers.market_data_streamer.publisher import StreamerPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("alphatrader.streamer")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_UNIVERSE_PATH = REPO_ROOT / "config" / "nifty50_universe.json"


def load_nifty50_symbols() -> list[str]:
    """Load canonical NIFTY 50 universe from configuration."""
    if DEFAULT_UNIVERSE_PATH.exists():
        data = json.loads(DEFAULT_UNIVERSE_PATH.read_text(encoding="utf-8"))
        symbols_raw: list[Any] = data.get("symbols", ["RELIANCE.NS", "TCS.NS", "INFY.NS"])
        return [str(s) for s in symbols_raw]
    return ["RELIANCE.NS", "TCS.NS", "INFY.NS"]


class MarketDataStreamerService:
    """Core orchestration service for market data ingestion, aggregation, and caching."""

    def __init__(
        self,
        feed_mode: FeedMode | str | None = None,
        symbols: list[str] | None = None,
    ) -> None:
        self.settings = get_settings()
        self.feed_mode = FeedMode(feed_mode) if feed_mode else self.settings.nse_market_feed_mode
        self.symbols = symbols or load_nifty50_symbols()

        self.calendar = MarketCalendar()
        self.normalizer = TickNormalizer(default_provider="streamer", feed_mode=self.feed_mode)
        self.aggregator = CandleAggregator()
        self.cache = MarketDataCache(redis_url=self.settings.redis_url)
        self.publisher = StreamerPublisher(bootstrap_servers=self.settings.kafka_bootstrap_servers)

        self.provider = create_market_data_provider(
            mode=self.feed_mode,
            symbols=self.symbols,
        )

        self._running = False
        self._ticks_count = 0
        self._candles_count = 0
        self._last_tick_time = datetime.now(UTC)

    async def start(self) -> None:
        """Run the streaming pipeline."""
        self._running = True
        logger.info(
            "Starting MarketDataStreamerService [mode=%s, symbols=%d]...",
            self.feed_mode.value,
            len(self.symbols),
        )

        # Emit startup event
        self.publisher.publish_system_event(
            event_type=SystemEventType.STARTUP,
            message=f"MarketDataStreamerService started in {self.feed_mode.value} mode",
            severity=EventSeverity.INFO,
            details={"symbols_count": len(self.symbols), "mode": self.feed_mode.value},
        )

        await self.provider.connect()
        await self.provider.subscribe(self.symbols)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            async for raw in self.provider.stream_quotes():
                if not self._running:
                    break

                # Market hours check for LIVE mode
                if self.feed_mode == FeedMode.LIVE:
                    status = self.calendar.evaluate_status()
                    if not status.is_open:
                        logger.info("Market is closed (%s). Streamer idling.", status.reason)
                        self.cache.update_feed_health(
                            status="IDLE_MARKET_CLOSED",
                            symbols_count=len(self.symbols),
                            ticks_ingested=self._ticks_count,
                            candles_emitted=self._candles_count,
                            dlq_count=self.normalizer.rejected_count,
                        )
                        await asyncio.sleep(15.0)
                        continue

                # Normalization
                norm_res = self.normalizer.normalize(raw)
                if not norm_res.is_valid or norm_res.tick is None:
                    # Route to DLQ
                    logger.warning("Normalizer rejected quote: %s", norm_res.error_message)
                    self.publisher.publish_dlq(
                        raw_payload=norm_res.raw_payload,
                        error_message=norm_res.error_message or "Unknown normalization error",
                    )
                    continue

                tick = norm_res.tick
                self._ticks_count += 1
                self._last_tick_time = datetime.now(UTC)

                # 1. Cache latest quote in Redis
                self.cache.cache_quote(tick)

                # 2. Publish tick to Redpanda market.ticks
                self.publisher.publish_tick(tick)

                # 3. Aggregate candles
                candles = self.aggregator.process_tick(tick)
                for candle in candles:
                    self._candles_count += 1
                    # Cache completed candle in Redis
                    self.cache.cache_candle(candle)
                    # Publish completed candle to Redpanda market.candles
                    self.publisher.publish_candle(candle)

        except asyncio.CancelledError:
            logger.info("MarketDataStreamerService cancelled.")
        finally:
            heartbeat_task.cancel()
            await self.stop()

    async def _heartbeat_loop(self) -> None:
        """Periodically report health and detect stalls."""
        while self._running:
            try:
                await asyncio.sleep(5.0)
                time_since_last_tick = (datetime.now(UTC) - self._last_tick_time).total_seconds()
                is_stalled = (time_since_last_tick > 30.0) and (self.feed_mode != FeedMode.LIVE)
                health_status = "DEGRADED_FEED_STALL" if is_stalled else "HEALTHY"

                self.cache.update_feed_health(
                    status=health_status,
                    symbols_count=len(self.symbols),
                    ticks_ingested=self._ticks_count,
                    candles_emitted=self._candles_count,
                    dlq_count=self.normalizer.rejected_count,
                )

                if is_stalled:
                    msg = f"Feed stall detected: no ticks received for {time_since_last_tick:.1f}s"
                    self.publisher.publish_system_event(
                        event_type=SystemEventType.ERROR,
                        message=msg,
                        severity=EventSeverity.WARNING,
                        details={"stall_seconds": time_since_last_tick},
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in heartbeat loop: %s", exc)

    async def stop(self) -> None:
        """Gracefully flush and close all resources."""
        if not self._running:
            return
        self._running = False
        logger.info("Flushing in-flight candles and closing connections...")

        # Flush active candles
        remaining_candles = self.aggregator.flush_all()
        for candle in remaining_candles:
            self._candles_count += 1
            self.cache.cache_candle(candle)
            self.publisher.publish_candle(candle)

        # Flush Kafka publisher
        self.publisher.flush(timeout=5.0)

        # Close provider
        await self.provider.close()

        # Update cache health
        self.cache.update_feed_health(
            status="STOPPED",
            symbols_count=len(self.symbols),
            ticks_ingested=self._ticks_count,
            candles_emitted=self._candles_count,
            dlq_count=self.normalizer.rejected_count,
        )

        logger.info(
            "MarketDataStreamerService stopped cleanly. (Ticks: %d, Candles: %d, DLQ: %d)",
            self._ticks_count,
            self._candles_count,
            self.normalizer.rejected_count,
        )


async def main() -> None:  # pragma: no cover
    """CLI runner entry point for the streamer worker."""
    streamer = MarketDataStreamerService()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _sig_handler() -> None:
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _sig_handler)

    streamer_task = asyncio.create_task(streamer.start())

    try:
        # Wait until stopped or cancelled
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
            if streamer_task.done():
                break
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        await streamer.stop()
        if not streamer_task.done():
            streamer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await streamer_task


if __name__ == "__main__":
    asyncio.run(main())
