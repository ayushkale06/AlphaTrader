"""Shared foundational utilities: typed configuration, JSON logging, and domain schemas."""

from src.common.config import AlphaTraderSettings, get_settings
from src.common.logging import (
    CorrelationIdFilter,
    StructuredJsonFormatter,
    configure_logging,
    correlation_context,
    generate_correlation_id,
    get_correlation_id,
    get_logger,
    set_correlation_id,
)

__all__ = [
    "AlphaTraderSettings",
    "CorrelationIdFilter",
    "StructuredJsonFormatter",
    "configure_logging",
    "correlation_context",
    "generate_correlation_id",
    "get_correlation_id",
    "get_logger",
    "get_settings",
    "set_correlation_id",
]
