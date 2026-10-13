"""Contract-validating Redpanda/Kafka publisher for technical signals and DLQs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import confluent_kafka

from src.common.contracts import (
    TopicName,
    validate_topic_message,
)
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.signals import TechnicalSignal

logger = logging.getLogger("alphatrader.analyst.publisher")


class TechnicalAnalystPublisher:
    """Publishes validated TechnicalSignal envelopes to signals.technical."""

    def __init__(self, bootstrap_servers: str = "127.0.0.1:9092") -> None:
        self._bootstrap_servers = bootstrap_servers
        conf = {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "alphatrader-technical-analyst",
            "acks": "all",
            "retries": 3,
            "linger.ms": 5,
        }
        self._producer = confluent_kafka.Producer(conf)

    def publish_signal(
        self,
        signal: TechnicalSignal,
        correlation_id: str,
        causation_id: UUID | None = None,
    ) -> EventEnvelope[TechnicalSignal]:
        """Publish a validated TechnicalSignal envelope to signals.technical."""
        envelope = EventEnvelope[TechnicalSignal](
            correlation_id=correlation_id,
            causation_id=causation_id,
            produced_at=datetime.now(UTC),
            source=ComponentSource.TECHNICAL_ANALYST,
            payload=signal,
        )

        validate_topic_message(
            TopicName.SIGNALS_TECHNICAL,
            envelope,
            message_key=signal.symbol,
        )

        key_bytes = signal.symbol.encode("utf-8")
        val_bytes = envelope.to_json_bytes()

        self._producer.produce(
            topic=TopicName.SIGNALS_TECHNICAL.value,
            key=key_bytes,
            value=val_bytes,
        )
        self._producer.poll(0)
        return envelope

    def publish_dlq(
        self, payload: dict[str, Any], reason: str, topic: str = "signals.technical.dlq"
    ) -> None:
        """Route unparseable candle or evaluation failure to DLQ."""
        dlq_event = {
            "error_type": "TECHNICAL_ANALYST_PROCESSING_ERROR",
            "reason": reason,
            "failed_at": datetime.now(UTC).isoformat(),
            "raw_payload": payload,
        }
        val_bytes = json.dumps(dlq_event, default=str).encode("utf-8")
        self._producer.produce(topic=topic, value=val_bytes)
        self._producer.poll(0)
        logger.warning("Routed invalid message to DLQ %s: %s", topic, reason)

    def flush(self, timeout_seconds: float = 5.0) -> None:
        """Flush pending Kafka producer buffer."""
        self._producer.flush(timeout=timeout_seconds)
