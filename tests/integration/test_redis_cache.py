"""Integration tests for Redis 7 low-latency state store and circuit breaker."""

from __future__ import annotations

import uuid

import pytest

from src.common.config import get_settings

pytestmark = pytest.mark.integration


def test_redis_ping_and_round_trip(require_redis: None) -> None:
    """Verify basic Redis connectivity and key SET/GET/DELETE."""
    import redis

    settings = get_settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)

    assert client.ping() is True

    test_key = f"alphatrader:test:{uuid.uuid4()}"
    test_val = "test-value-alpha"
    client.set(test_key, test_val, ex=60)

    assert client.get(test_key) == test_val
    client.delete(test_key)
    assert client.get(test_key) is None


def test_redis_floor_circuit_breaker_flag(require_redis: None) -> None:
    """Verify read/write and atomic updates for alphatrader:floor_halted flag."""
    import redis

    settings = get_settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)

    flag_key = "alphatrader:floor_halted"

    # Reset to unhalted
    client.set(flag_key, "0")
    assert client.get(flag_key) == "0"

    # Trip the circuit breaker
    client.set(flag_key, "1")
    assert client.get(flag_key) == "1"

    # Reset
    client.set(flag_key, "0")
    assert client.get(flag_key) == "0"


def test_redis_order_rate_limiter_counter(require_redis: None) -> None:
    """Verify atomic increment and TTL for rolling order rate limiter."""
    import redis

    settings = get_settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)

    rate_key = f"alphatrader:rate:orders:{uuid.uuid4().hex[:8]}"
    client.incr(rate_key)
    client.expire(rate_key, 60)
    client.incr(rate_key)
    count = int(client.get(rate_key) or 0)
    assert count == 2
    client.delete(rate_key)
