"""Verified Kafka publisher for Risk decisions and System Halt events.

Guarantees:
1. Conforms to TopicContract for RISK_DECISIONS and SYSTEM_EVENTS.
2. Synchronous flush ensuring message delivery verification.
3. Fails closed: raises on serialization or broker produce failure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from confluent_kafka import Producer

from src.common.contracts import TOPIC_REGISTRY, TopicName
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.risk import RiskDecision
from src.common.schemas.system import EventSeverity, SystemEvent, SystemEventType

logger = logging.getLogger("alphatrader.risk_guardrail.publisher")


class RiskPublisher:
    """Publishes validated RiskDecisions and System Halt events to Redpanda/Kafka."""

    def __init__(self, bootstrap_servers: str = "localhost:9092", producer: Any = None) -> None:
        self.bootstrap_servers = bootstrap_servers
        if producer is not None:
            self._producer = producer
        else:
            self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish_risk_decision(
        self,
        decision: RiskDecision,
        correlation_id: str | UUID | None = None,
        source: ComponentSource = ComponentSource.RISK_GUARDRAIL,
    ) -> EventEnvelope[RiskDecision]:
        """Publish a RiskDecision to risk.decisions topic."""
        topic_info = TOPIC_REGISTRY[TopicName.RISK_DECISIONS]
        envelope = EventEnvelope[RiskDecision](
            correlation_id=str(correlation_id or decision.order_request_id),
            source=source,
            payload=decision,
        )

        partition_key = str(decision.order_request_id).encode("utf-8")
        payload_bytes = envelope.to_json_bytes()

        self._producer.produce(
            topic=topic_info.topic.value,
            key=partition_key,
            value=payload_bytes,
        )
        unflushed = self._producer.flush(timeout=5.0)
        if unflushed > 0:
            msg = (
                f"Failed to deliver RiskDecision for order {decision.order_request_id}: "
                f"{unflushed} messages timed out"
            )
            raise RuntimeError(msg)

        logger.info(
            "Published RiskDecision for order %s: verdict=%s reason=%s",
            decision.order_request_id,
            decision.decision.value,
            decision.reason_code.value,
        )
        return envelope

    def publish_floor_halt(
        self,
        reason: str,
        operator_id: str,
        correlation_id: str | UUID | None = None,
        source: ComponentSource = ComponentSource.RISK_GUARDRAIL,
    ) -> EventEnvelope[SystemEvent]:
        """Publish a critical floor halt event to system.events topic."""
        topic_info = TOPIC_REGISTRY[TopicName.SYSTEM_EVENTS]
        now = datetime.now(UTC)
        payload = SystemEvent(
            event_type=SystemEventType.HALT,
            severity=EventSeverity.CRITICAL,
            component=ComponentSource.RISK_GUARDRAIL,
            occurred_at=now,
            message=f"TRADING FLOOR HALT triggered by {operator_id}: {reason}",
            metadata={"operator_id": operator_id, "reason": reason},
        )
        envelope = EventEnvelope[SystemEvent](
            correlation_id=str(correlation_id or payload.system_event_id),
            source=source,
            payload=payload,
        )

        partition_key = b"risk_guardrail"
        payload_bytes = envelope.to_json_bytes()

        self._producer.produce(
            topic=topic_info.topic.value,
            key=partition_key,
            value=payload_bytes,
        )
        unflushed = self._producer.flush(timeout=5.0)
        if unflushed > 0:
            msg = (
                f"Failed to deliver SystemEvent HALT to {topic_info.topic.value}: "
                f"{unflushed} messages timed out"
            )
            raise RuntimeError(msg)

        logger.critical("Published FLOOR HALT to %s: %s", topic_info.topic.value, reason)
        return envelope
