"""Integration tests for Redpanda (Kafka API) broker and topic contracts."""

from __future__ import annotations

import time
import uuid

import pytest

from src.common.config import get_settings
from src.common.contracts import (
    TOPIC_REGISTRY,
    TopicName,
    build_sample_topic_messages,
    validate_topic_message,
)

pytestmark = pytest.mark.integration


def test_all_contract_topics_exist_in_cluster(require_redpanda: None) -> None:
    """Verify that every topic registered in TOPIC_REGISTRY exists in cluster metadata."""
    from confluent_kafka.admin import AdminClient

    settings = get_settings()
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    metadata = admin.list_topics(timeout=5.0)

    cluster_topics = set(metadata.topics.keys())
    for topic_name, contract in TOPIC_REGISTRY.items():
        assert topic_name.value in cluster_topics, f"Topic {topic_name.value} missing from cluster"
        if contract.needs_dlq and contract.dlq_topic:
            assert contract.dlq_topic in cluster_topics, (
                f"DLQ topic {contract.dlq_topic} missing from cluster"
            )


@pytest.mark.parametrize("topic_name", list(TopicName))
def test_topic_envelope_round_trip(topic_name: TopicName, require_redpanda: None) -> None:
    """Publish a real EventEnvelope to topic, consume it back, and validate against contract."""
    from confluent_kafka import Consumer, Producer

    settings = get_settings()
    contract = TOPIC_REGISTRY[topic_name]
    sample_envelope = build_sample_topic_messages()[topic_name]
    key = contract.extract_message_key(sample_envelope)
    payload_bytes = sample_envelope.to_json_bytes()

    # Produce
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers, "acks": "all"})
    delivery_report: list[object] = []

    def ack(err: object, msg: object) -> None:
        if err:
            delivery_report.append(err)

    producer.produce(
        topic=topic_name.value,
        key=key.encode("utf-8"),
        value=payload_bytes,
        callback=ack,
    )
    producer.flush(timeout=10.0)
    assert not delivery_report, f"Produce failed on {topic_name.value}: {delivery_report}"

    # Consume
    group_id = f"test-cg-{topic_name.value}-{uuid.uuid4().hex[:8]}"
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic_name.value])

    received_envelope = None
    start = time.time()
    while time.time() - start < 12.0:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            continue

        raw_val = msg.value()
        msg_key = msg.key().decode("utf-8") if msg.key() else None

        validated = validate_topic_message(
            topic=topic_name,
            message=raw_val,
            message_key=msg_key,
        )
        if validated.event_id == sample_envelope.event_id:
            received_envelope = validated
            break

    consumer.close()
    assert received_envelope is not None, f"Failed to consume back message from {topic_name.value}"
    assert received_envelope.event_id == sample_envelope.event_id
    assert received_envelope.correlation_id == sample_envelope.correlation_id
