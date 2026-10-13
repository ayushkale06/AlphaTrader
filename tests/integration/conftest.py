"""Pytest configuration and fixtures for integration tests."""

from __future__ import annotations

import socket
from collections.abc import Generator
from typing import Any

import pytest

from src.common.config import get_settings


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a TCP socket connection can be established."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _check_postgres_live() -> bool:
    settings = get_settings()
    if not _is_port_open(settings.postgres_host, settings.postgres_port):
        return False
    try:
        import psycopg

        with (
            psycopg.connect(settings.postgres_sync_dsn, connect_timeout=2) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT 1;")
            return bool(cur.fetchone())
    except Exception:
        return False


def _check_redis_live() -> bool:
    settings = get_settings()
    if not _is_port_open(settings.redis_host, settings.redis_port):
        return False
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_timeout=2)
        return bool(r.ping())
    except Exception:
        return False


def _check_redpanda_live() -> bool:
    settings = get_settings()
    host_port = settings.kafka_bootstrap_servers.split(",")[0].strip()
    host, port_str = host_port.split(":")
    if not _is_port_open(host, int(port_str)):
        return False
    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
        meta = admin.list_topics(timeout=3.0)
        return meta is not None
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    """Return True if local PostgreSQL service is reachable."""
    return _check_postgres_live()


@pytest.fixture(scope="session")
def redis_available() -> bool:
    """Return True if local Redis service is reachable."""
    return _check_redis_live()


@pytest.fixture(scope="session")
def redpanda_available() -> bool:
    """Return True if local Redpanda broker is reachable."""
    return _check_redpanda_live()


@pytest.fixture
def require_postgres(postgres_available: bool) -> None:
    """Skip test if PostgreSQL is not reachable."""
    if not postgres_available:
        pytest.skip("Local PostgreSQL service is not running or unreachable.")


@pytest.fixture
def require_redis(redis_available: bool) -> None:
    """Skip test if Redis is not reachable."""
    if not redis_available:
        pytest.skip("Local Redis service is not running or unreachable.")


@pytest.fixture
def require_redpanda(redpanda_available: bool) -> None:
    """Skip test if Redpanda is not reachable."""
    if not redpanda_available:
        pytest.skip("Local Redpanda service is not running or unreachable.")


@pytest.fixture
def pg_connection(require_postgres: None) -> Generator[Any, None, None]:
    """Provide an autocommit=False connection to PostgreSQL, rolling back on teardown."""
    import psycopg

    settings = get_settings()
    with psycopg.connect(settings.postgres_sync_dsn, autocommit=False) as conn:
        yield conn
        conn.rollback()
