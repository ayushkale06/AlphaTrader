"""Redis caching layer for latest quotes and recent candles."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from src.common.schemas.market import Candle, CandleInterval, Tick

logger = logging.getLogger("alphatrader.cache")

# Redis key templates
QUOTE_KEY_PREFIX = "alphatrader:quote:"
CANDLE_KEY_PREFIX = "alphatrader:candle:"
FEED_HEALTH_KEY = "alphatrader:feed_health:market_data_streamer"

DEFAULT_QUOTE_TTL = 3600  # 1 hour
DEFAULT_CANDLE_TTL = 86400  # 24 hours


class MarketDataCache:
    """Provides high-speed caching and query access for quotes, candles, and feed health."""

    def __init__(
        self, redis_client: redis.Redis | None = None, redis_url: str = "redis://127.0.0.1:6379/0"
    ) -> None:
        if redis_client is not None:
            self._client = redis_client
        else:
            self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def cache_quote(self, tick: Tick, ttl: int = DEFAULT_QUOTE_TTL) -> None:
        """Cache latest quote for a symbol."""
        key = f"{QUOTE_KEY_PREFIX}{tick.symbol}"
        payload = tick.model_dump_json()
        self._client.set(key, payload, ex=ttl)

    def get_latest_quote(self, symbol: str) -> Tick | None:
        """Retrieve latest quote for a symbol."""
        key = f"{QUOTE_KEY_PREFIX}{symbol}"
        raw = self._client.get(key)
        if raw and isinstance(raw, str):
            return Tick.model_validate_json(raw)
        return None

    def cache_candle(self, candle: Candle, ttl: int = DEFAULT_CANDLE_TTL) -> None:
        """Store completed candle in a sorted set or key by symbol and interval."""
        key = f"{CANDLE_KEY_PREFIX}{candle.symbol}:{candle.interval.value}"
        score = candle.close_time.timestamp()
        payload = candle.model_dump_json()
        self._client.zadd(key, {payload: score})
        self._client.expire(key, ttl)

    def get_candles(
        self,
        symbol: str,
        interval: CandleInterval | str,
        limit: int = 50,
    ) -> list[Candle]:
        """Fetch the most recent N candles for a symbol and interval."""
        interval_str = interval.value if isinstance(interval, CandleInterval) else str(interval)
        key = f"{CANDLE_KEY_PREFIX}{symbol}:{interval_str}"
        raw_items: Any = self._client.zrevrange(key, 0, max(0, limit - 1))
        candles: list[Candle] = []
        for raw in raw_items:
            if isinstance(raw, (str, bytes)):
                candles.append(Candle.model_validate_json(raw))
        # Return in chronological order
        return sorted(candles, key=lambda c: c.open_time)

    def update_feed_health(
        self,
        status: str,
        symbols_count: int,
        ticks_ingested: int,
        candles_emitted: int,
        dlq_count: int,
    ) -> None:
        """Record heartbeats and health metrics in Redis."""
        data = {
            "status": status,
            "symbols_count": symbols_count,
            "ticks_ingested": ticks_ingested,
            "candles_emitted": candles_emitted,
            "dlq_count": dlq_count,
        }
        self._client.set(FEED_HEALTH_KEY, json.dumps(data), ex=60)

    def get_feed_health(self) -> dict[str, Any]:
        """Fetch current feed health status from Redis."""
        raw = self._client.get(FEED_HEALTH_KEY)
        if raw and isinstance(raw, str):
            parsed: dict[str, Any] = json.loads(raw)
            return parsed
        return {
            "status": "STOPPED",
            "symbols_count": 0,
            "ticks_ingested": 0,
            "candles_emitted": 0,
            "dlq_count": 0,
        }
