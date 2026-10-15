"""Redis-backed persistent state checkpointing for the Multi-Agent Orchestrator.

Governance: ADR-004, ADR-011, ADR-012.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.orchestrator.state import TradingFloorState

logger = logging.getLogger("alphatrader.orchestrator.checkpoint")


class OrchestratorCheckpointer:
    """Checkpoints TradingFloorState to Redis with TTL expiration."""

    def __init__(self, redis_client: Any = None, ttl_seconds: int = 86400) -> None:
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self._memory_checkpoints: dict[str, dict[str, Any]] = {}

    def _make_key(self, correlation_id: str) -> str:
        return f"alphatrader:orchestrator:state:{correlation_id}"

    def save(self, state: TradingFloorState) -> None:
        """Save state snapshot to Redis or in-memory fallback."""
        payload = state.to_dict()
        key = self._make_key(state.correlation_id)

        if self.redis is not None:
            try:
                self.redis.set(key, json.dumps(payload), ex=self.ttl_seconds)
                logger.debug("Checkpointed state for %s to Redis", state.correlation_id)
                return
            except Exception as e:
                logger.warning("Failed to checkpoint state to Redis: %s", e)

        self._memory_checkpoints[key] = payload

    def load(self, correlation_id: str) -> dict[str, Any] | None:
        """Load state snapshot by correlation_id."""
        key = self._make_key(correlation_id)

        if self.redis is not None:
            try:
                raw = self.redis.get(key)
                if raw is not None:
                    raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    parsed = json.loads(raw_str)
                    if isinstance(parsed, dict):
                        return parsed
            except Exception as e:
                logger.warning("Failed to load state from Redis: %s", e)

        return self._memory_checkpoints.get(key)
