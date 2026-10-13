"""Contract-validating Redpanda/Kafka publisher for ticks, candles, DLQs, and system events."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import confluent_kafka

from src.common.contracts import (
    TOPIC_REGISTRY,
    TopicName,
    validate_topic_message,
)
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.market import Candle, Tick
from src.common.schemas.system import EventSeverity, SystemEvent, SystemEventType

logger = logging.getLogger("alphatrader.publisher")


class StreamerPublisher:
    """Wraps confluent_kafka.Producer with contract validation and DLQ routing.

    Invariants:
    - Every message is wrapped in EventEnvelope[T] with a valid correlation_id.
    - Every message is validated against its topic contract before publishing.
    - Partition key is set according to the registered key rule (symbol for market data).
    - Invalid or malformed quotes are routed to the .dlq topic, never dropped silently.
    """

    def __init__(self, bootstrap_servers: str = "127.0.0.1:9092") -> None:
        self._bootstrap_servers = bootstrap_servers
        conf = {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "alphatrader-market-data-streamer",
            "acks": "all",
            "retries": 3,
            "linger.ms": 5,
        }
        self._producer = confluent_kafka.Producer(conf)

    def publish_tick(
        self,
        tick: Tick,
        correlation_id: str | None = None,
    ) -> EventEnvelope[Tick]:
        """Publish a validated Tick envelope to market.ticks."""
        cid = correlation_id or f"cid-tick-{uuid.uuid4()}"
        envelope = EventEnvelope[Tick](
            correlation_id=cid,
            produced_at=tick.timestamp,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=tick,
        )

        # Validate against topic contract
        contract = TOPIC_REGISTRY[TopicName.MARKET_TICKS]
        key = contract.extract_message_key(envelope)
        validate_topic_message(TopicName.MARKET_TICKS, envelope, message_key=key)

        self._producer.produce(
            topic=TopicName.MARKET_TICKS.value,
            key=key.encode("utf-8"),
            value=envelope.to_json_bytes(),
        )
        self._producer.poll(0)
        return envelope

    def publish_candle(
        self,
        candle: Candle,
        correlation_id: str | None = None,
    ) -> EventEnvelope[Candle]:
        """Publish a validated Candle envelope to market.candles."""
        cid = correlation_id or f"cid-candle-{uuid.uuid4()}"
        envelope = EventEnvelope[Candle](
            correlation_id=cid,
            produced_at=candle.close_time,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=candle,
        )

        contract = TOPIC_REGISTRY[TopicName.MARKET_CANDLES]
        key = contract.extract_message_key(envelope)
        validate_topic_message(TopicName.MARKET_CANDLES, envelope, message_key=key)

        self._producer.produce(
            topic=TopicName.MARKET_CANDLES.value,
            key=key.encode("utf-8"),
            value=envelope.to_json_bytes(),
        )
        self._producer.poll(0)
        return envelope

    def publish_dlq(
        self,
        raw_payload: dict[str, Any],
        error_message: str,
        topic: TopicName = TopicName.MARKET_TICKS,
        error_code: str | None = None,
    ) -> None:
        """Route unparsable or out-of-order raw market data to dead-letter queue."""
        contract = TOPIC_REGISTRY[topic]
        dlq_topic = contract.dlq_topic
        if not dlq_topic:
            logger.error("No DLQ configured for topic %s", topic.value)
            return

        dlq_message = {
            "error": error_message,
            "error_code": error_code,
            "original_topic": topic.value,
            "raw_payload": raw_payload,
        }
        symbol = str(raw_payload.get("symbol", "UNKNOWN"))
        self._producer.produce(
            topic=dlq_topic,
            key=symbol.encode("utf-8"),
            value=json.dumps(dlq_message, default=str).encode("utf-8"),
        )
        self._producer.poll(0)

    def publish_system_event(
        self,
        event_type: SystemEventType,
        message: str,
        severity: EventSeverity = EventSeverity.INFO,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Publish an operational status event (startup, halt, heartbeat, degraded)."""
        import datetime

        now = datetime.datetime.now(datetime.UTC)
        sys_event = SystemEvent(
            event_type=event_type,
            severity=severity,
            component=ComponentSource.MARKET_DATA_STREAMER,
            message=message,
            metadata=details or {},
            occurred_at=now,
        )
        envelope = EventEnvelope[SystemEvent](
            correlation_id=f"cid-sys-{uuid.uuid4()}",
            produced_at=now,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=sys_event,
        )
        contract = TOPIC_REGISTRY[TopicName.SYSTEM_EVENTS]
        key = contract.extract_message_key(envelope)
        validate_topic_message(TopicName.SYSTEM_EVENTS, envelope, message_key=key)

        self._producer.produce(
            topic=TopicName.SYSTEM_EVENTS.value,
            key=key.encode("utf-8"),
            value=envelope.to_json_bytes(),
        )
        self._producer.poll(0)

    def flush(self, timeout: float = 5.0) -> None:
        """Flush in-flight messages."""
        self._producer.flush(timeout)
