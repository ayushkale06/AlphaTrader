"""Standalone Technical Analyst Streaming Worker.

Consumes closed candles (is_final=True) from `market.candles` (group `technical-analyst-worker`),
maintains O(1) streaming indicators, computes deterministic explainable TechnicalSignals,
publishes to `signals.technical` with causation_id = candle_envelope.event_id,
caches indicators and signals in Redis, and persists streaming state for cold-start restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime

import confluent_kafka

from src.common.config import get_settings
from src.common.contracts import (
    TopicName,
    validate_topic_message,
)
from src.common.schemas.envelope import EventEnvelope
from src.common.schemas.market import Candle
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache
from src.mcp_servers.technical_analyst.indicators.bundle import SymbolIndicatorSet
from src.mcp_servers.technical_analyst.publisher import TechnicalAnalystPublisher
from src.mcp_servers.technical_analyst.signals import SignalEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("alphatrader.analyst")


class TechnicalAnalystService:
    """Core streaming worker service for technical analysis and signal generation."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        redis_url: str | None = None,
        consumer_group: str = "technical-analyst-worker",
    ) -> None:
        self.settings = get_settings()
        self.bootstrap_servers = bootstrap_servers or self.settings.kafka_bootstrap_servers
        self.redis_url = redis_url or self.settings.redis_url
        self.consumer_group = consumer_group

        self.cache = TechnicalAnalystCache(redis_url=self.redis_url)
        self.publisher = TechnicalAnalystPublisher(bootstrap_servers=self.bootstrap_servers)
        self.signal_engine = SignalEngine()

        # Indicator state indexed by (symbol, interval_str)
        self.indicator_sets: dict[tuple[str, str], SymbolIndicatorSet] = {}
        # Seen candle deduplication: (symbol, interval_str, open_time_iso)
        self._seen_candles: set[tuple[str, str, str]] = set()

        self._consumer: confluent_kafka.Consumer | None = None
        self._running = False
        self._candles_processed = 0
        self._signals_published = 0
        self._last_processed_at = datetime.now(UTC)

    def _get_or_create_indicators(
        self, symbol: str, interval: str, candle: Candle | None = None
    ) -> SymbolIndicatorSet:
        key = (symbol, interval)
        if key not in self.indicator_sets:
            bundle = SymbolIndicatorSet()
            # Attempt cold-start state restore from Redis with fingerprint & gap checks
            saved_state = self.cache.get_indicator_state(symbol, interval)
            if saved_state and candle is not None and candle.provenance is not None:
                meta = saved_state.get("_meta", {})
                fp = meta.get("fingerprint", {})
                prov = candle.provenance
                expected_prov = prov.provider
                expected_mode = prov.feed_mode.value

                # Match provider, feed mode, and config hash
                fp_match = (
                    fp.get("provider") == expected_prov
                    and fp.get("feed_mode") == expected_mode
                    and fp.get("config_hash", self.signal_engine.config_hash)
                    == self.signal_engine.config_hash
                )

                # Check gap in close_time: max allowable gap is 50 intervals
                gap_ok = True
                last_close_str = meta.get("last_close_time")
                if last_close_str:
                    try:
                        last_close = datetime.fromisoformat(last_close_str)
                        inv_minutes = {
                            "1m": 1,
                            "5m": 5,
                            "15m": 15,
                            "1d": 1440,
                        }.get(interval, 5)
                        gap = (candle.close_time - last_close).total_seconds() / (inv_minutes * 60)
                        if gap < 0 or gap > 50:
                            gap_ok = False
                            logger.warning(
                                "Indicator state gap too large (%s intervals) for %s:%s; "
                                "discarding stale state.",
                                gap,
                                symbol,
                                interval,
                            )
                    except Exception as err:
                        logger.warning(
                            "Failed to parse last_close_time '%s': %s", last_close_str, err
                        )
                        gap_ok = False

                if fp_match and gap_ok:
                    logger.info(
                        "Restoring indicator state for %s:%s from Redis "
                        "(fingerprint & gap verified)",
                        symbol,
                        interval,
                    )
                    bundle.restore(saved_state)
                else:
                    logger.warning(
                        "Discarded state for %s:%s: fp_match=%s, gap_ok=%s",
                        symbol,
                        interval,
                        fp_match,
                        gap_ok,
                    )
            elif saved_state and candle is None:
                bundle.restore(saved_state)

            self.indicator_sets[key] = bundle
        return self.indicator_sets[key]

    def _init_consumer(self) -> None:
        conf = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.consumer_group,
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,  # Manual commit after processing
        }
        self._consumer = confluent_kafka.Consumer(conf)
        self._consumer.subscribe([TopicName.MARKET_CANDLES.value])
        logger.info(
            "Subscribed consumer group %s to topic %s",
            self.consumer_group,
            TopicName.MARKET_CANDLES.value,
        )

    def process_candle_message(self, raw_bytes: bytes) -> None:
        """Process a single candle message from Redpanda."""
        try:
            envelope: EventEnvelope[Candle] = validate_topic_message(
                TopicName.MARKET_CANDLES, raw_bytes
            )  # type: ignore[assignment]
        except Exception as exc:
            logger.warning("Rejecting invalid message from market.candles: %s", exc)
            self.publisher.publish_dlq(
                payload={"raw": raw_bytes.decode("utf-8", errors="replace")},
                reason=f"Topic validation failed: {exc}",
            )
            return

        candle = envelope.payload

        # Ignore non-final / in-flight candles
        if candle.is_final is not True:
            logger.debug(
                "Skipping non-final candle for %s:%s at %s",
                candle.symbol,
                candle.interval.value,
                candle.close_time,
            )
            return

        # Deduplication check
        dedup_key = (candle.symbol, candle.interval.value, candle.open_time.isoformat())
        if dedup_key in self._seen_candles:
            logger.debug("Skipping duplicate candle %s", dedup_key)
            return
        self._seen_candles.add(dedup_key)
        if len(self._seen_candles) > 10000:
            # Keep dedup buffer bounded
            self._seen_candles.clear()

        # Update indicators
        indicator_bundle = self._get_or_create_indicators(
            candle.symbol, candle.interval.value, candle=candle
        )
        ready_indicators = indicator_bundle.update(candle)

        # Cache indicators and state with fingerprint in Redis
        self.cache.cache_indicators(candle.symbol, candle.interval, ready_indicators)
        snapshot = indicator_bundle.snapshot()
        if candle.provenance:
            snapshot["_meta"] = {
                "fingerprint": {
                    "provider": candle.provenance.provider,
                    "feed_mode": candle.provenance.feed_mode.value,
                    "config_version": self.signal_engine.config_version,
                    "config_hash": self.signal_engine.config_hash,
                },
                "last_close_time": candle.close_time.isoformat(),
            }
        self.cache.save_indicator_state(candle.symbol, candle.interval, snapshot)

        self._candles_processed += 1
        self._last_processed_at = datetime.now(UTC)

        # Evaluate deterministic signal
        if candle.provenance is None:
            logger.warning("Candle for %s missing provenance, skipping signal", candle.symbol)
            return

        signal = self.signal_engine.evaluate(
            symbol=candle.symbol,
            interval=candle.interval,
            candle=candle,
            indicators=ready_indicators,
            provenance=candle.provenance,
        )

        if signal is not None:
            # Publish to signals.technical
            self.publisher.publish_signal(
                signal=signal,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.event_id,
            )
            self.cache.cache_signal(signal)
            self._signals_published += 1
            logger.info(
                "Published TechnicalSignal: %s %s %s conf=%s fired=%s",
                signal.symbol,
                signal.interval.value,
                signal.direction.value,
                signal.confidence,
                signal.rules_fired,
            )

    async def start(self) -> None:
        """Main service loop."""
        self._running = True
        self._init_consumer()
        logger.info("Technical Analyst streaming worker started.")

        try:
            while self._running:
                # Update health in Redis
                self.cache.update_health(
                    {
                        "status": "HEALTHY",
                        "candles_processed": self._candles_processed,
                        "signals_published": self._signals_published,
                        "active_symbols": len(self.indicator_sets),
                        "last_processed_at": self._last_processed_at.isoformat(),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

                if self._consumer is not None:
                    msg = self._consumer.poll(timeout=0.5)
                    if msg is not None:
                        if msg.error():
                            logger.error("Consumer error: %s", msg.error())
                        else:
                            val = msg.value()
                            if val:
                                self.process_candle_message(val)
                                # Manual offset commit AFTER successful publish & cache
                                self._consumer.commit(message=msg, asynchronous=False)

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            logger.info("Technical Analyst worker cancelled, shutting down...")
        finally:
            self.stop()

    def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._consumer is not None:
            with contextlib.suppress(Exception):
                self._consumer.close()
            self._consumer = None
        self.publisher.flush(timeout_seconds=3.0)
        logger.info(
            "Technical Analyst worker stopped. Total candles=%d, signals=%d",
            self._candles_processed,
            self._signals_published,
        )


async def main() -> None:
    service = TechnicalAnalystService()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _sig_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _sig_handler)

    task = asyncio.create_task(service.start())

    await stop_event.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


if __name__ == "__main__":
    asyncio.run(main())
