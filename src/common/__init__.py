"""Shared foundational utilities: typed configuration, JSON logging, and domain schemas."""

from src.common.config import AlphaTraderSettings, get_settings
from src.common.contracts import (
    TOPIC_REGISTRY,
    MessageKeyRule,
    TopicContract,
    TopicContractViolationError,
    TopicName,
    build_sample_topic_messages,
    get_topic_contract,
    validate_topic_message,
)
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
    "TOPIC_REGISTRY",
    "AlphaTraderSettings",
    "CorrelationIdFilter",
    "MessageKeyRule",
    "StructuredJsonFormatter",
    "TopicContract",
    "TopicContractViolationError",
    "TopicName",
    "build_sample_topic_messages",
    "configure_logging",
    "correlation_context",
    "generate_correlation_id",
    "get_correlation_id",
    "get_logger",
    "get_settings",
    "get_topic_contract",
    "set_correlation_id",
    "validate_topic_message",
]
