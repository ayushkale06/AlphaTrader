"""Structured JSON logging with automatic ``correlation_id`` propagation.

Uses ``contextvars.ContextVar`` so asynchronous LangGraph agent workflows and
MCP server handlers automatically attach the active ``correlation_id`` to every
emitted JSON log record across the tick-to-fill lifecycle.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from src.common.config import AlphaTraderSettings, get_settings

_DEFAULT_CORRELATION_ID = "00000000-0000-0000-0000-000000000000"

_correlation_id_ctx: ContextVar[str] = ContextVar(
    "alphatrader_correlation_id",
    default=_DEFAULT_CORRELATION_ID,
)

_STANDARD_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "correlation_id",
    }
)


def generate_correlation_id() -> str:
    """Generate a new UUIDv4 correlation identifier."""
    return str(uuid.uuid4())


def get_correlation_id() -> str:
    """Return the active correlation ID for the current execution context."""
    return _correlation_id_ctx.get()


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Set the correlation ID in the current context, generating one if omitted."""
    resolved = (
        correlation_id.strip()
        if correlation_id and correlation_id.strip()
        else generate_correlation_id()
    )
    _correlation_id_ctx.set(resolved)
    return resolved


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    """Context manager that binds a ``correlation_id`` for the duration of a block."""
    resolved = (
        correlation_id.strip()
        if correlation_id and correlation_id.strip()
        else generate_correlation_id()
    )
    token = _correlation_id_ctx.set(resolved)
    try:
        yield resolved
    finally:
        _correlation_id_ctx.reset(token)


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects the active ``correlation_id`` into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        existing = getattr(record, "correlation_id", None)
        if not isinstance(existing, str) or not existing.strip():
            record.correlation_id = get_correlation_id()
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects with correlation and safety metadata."""

    def __init__(
        self,
        service_name: str = "alphatrader-core",
        environment: str = "development",
        trading_mode: str = "PAPER",
    ) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment
        self.trading_mode = trading_mode

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = getattr(record, "correlation_id", None)
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            correlation_id = get_correlation_id()

        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "correlation_id": correlation_id,
            "service": self.service_name,
            "environment": self.environment,
            "trading_mode": self.trading_mode,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        extra_fields: dict[str, Any] = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_")
        }
        if extra_fields:
            payload["extra"] = extra_fields

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    settings: AlphaTraderSettings | None = None,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure the ``alphatrader`` root logger with structured JSON formatting."""
    resolved_settings = settings if settings is not None else get_settings()
    target_stream = stream if stream is not None else sys.stdout

    logger = logging.getLogger("alphatrader")
    logger.setLevel(getattr(logging, resolved_settings.log_level))
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.StreamHandler(target_stream)
    handler.setLevel(getattr(logging, resolved_settings.log_level))
    handler.addFilter(CorrelationIdFilter())

    if resolved_settings.log_json:
        formatter: logging.Formatter = StructuredJsonFormatter(
            service_name=resolved_settings.service_name,
            environment=resolved_settings.app_env,
            trading_mode=resolved_settings.trading_mode,
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | cid=%(correlation_id)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``alphatrader`` namespace."""
    qualified = name if name.startswith("alphatrader") else f"alphatrader.{name}"
    logger = logging.getLogger(qualified)
    if not any(isinstance(f, CorrelationIdFilter) for f in logger.filters):
        logger.addFilter(CorrelationIdFilter())
    return logger
