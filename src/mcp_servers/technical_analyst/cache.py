"""Redis cache layer for technical indicators, signals, and analyst health."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import redis

from src.common.schemas.envelope import CURRENT_SCHEMA_VERSION
from src.common.schemas.market import CandleInterval
from src.common.schemas.signals import TechnicalSignal

logger = logging.getLogger("alphatrader.analyst.cache")

INDICATOR_KEY_PREFIX = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:indicator:"
SIGNAL_KEY_PREFIX = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:signal:"
STATE_KEY_PREFIX = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:state:analyst:"
HEALTH_KEY = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:feed_health:technical_analyst"

DEFAULT_TTL = 86400  # 24 hours


class TechnicalAnalystCache:
    """Manages Redis storage and retrieval for technical indicator states and signals."""

    def __init__(
        self,
        redis_client: redis.Redis[str] | None = None,
        redis_url: str = "redis://127.0.0.1:6379/0",
    ) -> None:
        if redis_client is not None:
            self._client = redis_client
        else:
            self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def cache_indicators(
        self,
        symbol: str,
        interval: CandleInterval | str,
        indicators: dict[str, Decimal],
        ttl: int = DEFAULT_TTL,
    ) -> None:
        """Cache latest computed indicators dictionary for (symbol, interval)."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{INDICATOR_KEY_PREFIX}{symbol}:{inv_str}"
        data = {k: str(v) for k, v in indicators.items()}
        self._client.set(key, json.dumps(data), ex=ttl)

    def get_latest_indicators(
        self, symbol: str, interval: CandleInterval | str
    ) -> dict[str, Decimal] | None:
        """Retrieve latest computed indicators dictionary for (symbol, interval)."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{INDICATOR_KEY_PREFIX}{symbol}:{inv_str}"
        raw = self._client.get(key)
        if raw and isinstance(raw, str):
            data = json.loads(raw)
            return {k: Decimal(v) for k, v in data.items()}
        return None

    def cache_signal(self, signal: TechnicalSignal, ttl: int = DEFAULT_TTL) -> None:
        """Cache latest TechnicalSignal for (symbol, interval)."""
        key = f"{SIGNAL_KEY_PREFIX}{signal.symbol}:{signal.interval.value}"
        payload = signal.model_dump_json()
        self._client.set(key, payload, ex=ttl)

    def get_latest_signal(
        self, symbol: str, interval: CandleInterval | str
    ) -> TechnicalSignal | None:
        """Retrieve latest TechnicalSignal for (symbol, interval)."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{SIGNAL_KEY_PREFIX}{symbol}:{inv_str}"
        raw = self._client.get(key)
        if raw and isinstance(raw, str):
            try:
                return TechnicalSignal.model_validate_json(raw)
            except Exception as e:
                logger.warning(
                    "Failed to parse cached TechnicalSignal at %s (discarding stale): %s",
                    key,
                    e,
                )
                return None
        return None

    def save_indicator_state(
        self, symbol: str, interval: CandleInterval | str, state: dict[str, Any]
    ) -> None:
        """Save streaming indicator state for cold-start restore."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{STATE_KEY_PREFIX}{symbol}:{inv_str}"
        self._client.set(key, json.dumps(state), ex=DEFAULT_TTL)

    def get_indicator_state(
        self, symbol: str, interval: CandleInterval | str
    ) -> dict[str, Any] | None:
        """Retrieve streaming indicator state for cold-start restore."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{STATE_KEY_PREFIX}{symbol}:{inv_str}"
        raw = self._client.get(key)
        if raw and isinstance(raw, str):
            res: Any = json.loads(raw)
            return dict(res) if isinstance(res, dict) else None
        return None

    def update_health(self, status: dict[str, Any], ttl: int = 60) -> None:
        """Update analyst worker heartbeat and status."""
        self._client.set(HEALTH_KEY, json.dumps(status), ex=ttl)

    def get_health(self) -> dict[str, Any] | None:
        """Get analyst worker heartbeat status."""
        raw = self._client.get(HEALTH_KEY)
        if raw and isinstance(raw, str):
            res: Any = json.loads(raw)
            return dict(res) if isinstance(res, dict) else None
        return None
