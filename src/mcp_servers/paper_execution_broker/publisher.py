"""Synchronous verified Kafka publisher for paper execution Fills.

Guarantees:
1. Conforms to TopicContract for TopicName.ORDERS_FILLS (topic: 'orders.fills').
2. Uses partition key = str(symbol).
3. Synchronously verifies delivery callback and producer flush == 0.
4. Fails closed: raises RuntimeError on any delivery unconfirmation.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from confluent_kafka import Producer

from src.common.contracts import TOPIC_REGISTRY, TopicName
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.orders import Fill

logger = logging.getLogger("alphatrader.paper_execution_broker.publisher")


class PaperExecutionPublisher:
    """Publishes validated paper trade execution fills to orders.fills topic."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        producer: Any = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        if producer is not None:
            self._producer = producer
        else:
            self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish_fill(
        self,
        fill: Fill,
        correlation_id: str | UUID | None = None,
        causation_id: UUID | None = None,
        source: ComponentSource = ComponentSource.PAPER_EXECUTION_BROKER,
        timeout_seconds: float = 5.0,
    ) -> EventEnvelope[Fill]:
        """Publish a Fill to the orders.fills topic with delivery confirmation."""
        topic_info = TOPIC_REGISTRY[TopicName.ORDERS_FILLS]
        envelope = EventEnvelope[Fill](
            correlation_id=str(correlation_id or fill.order_request_id),
            causation_id=causation_id or fill.order_id,
            source=source,
            payload=fill,
        )

        partition_key = fill.symbol.encode("utf-8")
        payload_bytes = envelope.to_json_bytes()

        ack_fired = False
        ack_error: Exception | None = None

        def delivery_callback(err: Any, _msg: Any) -> None:
            nonlocal ack_fired, ack_error
            ack_fired = True
            ack_error = err

        self._producer.produce(
            topic=topic_info.topic.value,
            key=partition_key,
            value=payload_bytes,
            on_delivery=delivery_callback,
        )
        self._producer.poll(0)
        unflushed = self._producer.flush(timeout=timeout_seconds)

        if unflushed > 0 or not ack_fired or ack_error is not None:
            msg = (
                f"Failed to confirm delivery of Fill {fill.fill_id} to {topic_info.topic.value}: "
                f"unflushed={unflushed}, ack_fired={ack_fired}, ack_error={ack_error}"
            )
            logger.error(msg)
            raise RuntimeError(msg)

        logger.info(
            "Successfully published Fill %s for order %s to %s",
            fill.fill_id,
            fill.order_id,
            topic_info.topic.value,
        )
        return envelope

    def flush(self, timeout_seconds: float = 5.0) -> int:
        """Flush any pending messages."""
        return int(self._producer.flush(timeout=timeout_seconds))
