"""Redis state cache for Quant Strategy Engine."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import redis

from src.common.config import get_settings
from src.common.schemas.envelope import CURRENT_SCHEMA_VERSION
from src.common.schemas.market import Candle, CandleInterval
from src.common.schemas.signals import QuantSignal

logger = logging.getLogger("alphatrader.quant.cache")

QUANT_SIGNAL_KEY_PREFIX = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:signal:quant:"
PORTFOLIO_TARGET_KEY = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:portfolio:target"
ENGINE_HEALTH_KEY = f"alphatrader:v{CURRENT_SCHEMA_VERSION}:feed_health:quant_strategy_engine"

DEFAULT_TTL = 86400  # 24 hours


class QuantStrategyCache:
    """Redis cache for QuantStrategyEngine signals, targets, and operational metrics."""

    def __init__(
        self,
        redis_url: str | None = None,
        redis_client: redis.Redis[str] | None = None,
    ) -> None:
        if redis_client is not None:
            self._client = redis_client
        else:
            url = redis_url or get_settings().redis_url
            self._client = redis.Redis.from_url(url, decode_responses=True)

    def save_signal(self, signal: QuantSignal) -> None:
        """Cache latest QuantSignal for (symbol, interval)."""
        key = f"{QUANT_SIGNAL_KEY_PREFIX}{signal.symbol}:{signal.interval.value}"
        try:
            self._client.set(key, signal.model_dump_json(), ex=DEFAULT_TTL)
        except Exception as e:
            logger.warning("Failed to save QuantSignal to cache: %s", e)

    def get_latest_signal(self, symbol: str, interval: CandleInterval | str) -> QuantSignal | None:
        """Retrieve latest QuantSignal for (symbol, interval)."""
        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        key = f"{QUANT_SIGNAL_KEY_PREFIX}{symbol}:{inv_str}"
        try:
            raw = self._client.get(key)
            if raw and isinstance(raw, str):
                return QuantSignal.model_validate_json(raw)
        except Exception as e:
            logger.warning("Failed to parse cached QuantSignal at %s: %s", key, e)
            return None
        return None

    def save_target_portfolio(self, targets: dict[str, Any]) -> None:
        """Cache aggregate target portfolio allocations."""
        try:
            self._client.set(PORTFOLIO_TARGET_KEY, json.dumps(targets, default=str), ex=DEFAULT_TTL)
        except Exception as e:
            logger.warning("Failed to save target portfolio to cache: %s", e)

    def get_target_portfolio(self) -> dict[str, Any] | None:
        """Retrieve aggregate target portfolio allocations."""
        try:
            raw = self._client.get(PORTFOLIO_TARGET_KEY)
            if raw and isinstance(raw, str):
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
        except Exception as e:
            logger.warning("Failed to get target portfolio from cache: %s", e)
            return None
        return None

    def record_heartbeat(self, status: str = "HEALTHY", processed_count: int = 0) -> None:
        """Update operational heartbeat in Redis."""
        payload = {
            "status": status,
            "processed_count": processed_count,
            "schema_version": CURRENT_SCHEMA_VERSION,
        }
        try:
            self._client.set(ENGINE_HEALTH_KEY, json.dumps(payload), ex=300)
        except Exception as e:
            logger.warning("Failed to record heartbeat in cache: %s", e)

    def get_health(self) -> dict[str, Any] | None:
        """Get operational health metadata."""
        try:
            raw = self._client.get(ENGINE_HEALTH_KEY)
            if raw and isinstance(raw, str):
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {str(k): v for k, v in data.items()}
        except Exception as e:
            logger.warning("Failed to get health from cache: %s", e)
            return None
        return None

    def save_candle(self, candle: Candle) -> None:
        """Cache closed candle in time-addressable store (72-hour TTL)."""
        from src.mcp_servers.quant_strategy_engine.manifest import to_canonical_utc_str

        canon_close = to_canonical_utc_str(candle.close_time)
        key = f"quant:candle:{candle.symbol}:{candle.interval.value}:{canon_close}"
        try:
            self._client.set(key, candle.model_dump_json(), ex=259200)
        except Exception as e:
            logger.warning("Failed to save candle to time-addressable cache: %s", e)
            raise

    def get_candle(
        self,
        symbol: str,
        interval: CandleInterval | str,
        close_time: datetime,
    ) -> Candle | None:
        """Retrieve candle from time-addressable store by symbol, interval, and close_time."""
        from src.mcp_servers.quant_strategy_engine.manifest import to_canonical_utc_str

        inv_str = interval.value if isinstance(interval, CandleInterval) else interval
        canon_close = to_canonical_utc_str(close_time)
        key = f"quant:candle:{symbol}:{inv_str}:{canon_close}"
        try:
            raw = self._client.get(key)
            if raw and isinstance(raw, str):
                return Candle.model_validate_json(raw)
        except Exception as e:
            logger.warning("Failed to get candle from %s: %s", key, e)
            return None
        return None
