"""Unit tests for the Kafka topic contract registry and JSON Schema exporter."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.export_schemas import EXPORTED_MODELS, export_all_schemas
from scripts.export_schemas import main as export_main
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
from src.common.schemas import ComponentSource, EventEnvelope, Tick


def test_every_topic_has_registered_contract_and_valid_sample() -> None:
    """Every TopicName enum value must exist in TOPIC_REGISTRY and validate its sample message."""
    all_topics = set(TopicName)
    assert set(TOPIC_REGISTRY.keys()) == all_topics
    assert len(all_topics) == 9

    samples = build_sample_topic_messages()
    assert set(samples.keys()) == all_topics

    for topic in TopicName:
        contract = get_topic_contract(topic)
        envelope = samples[topic]
        key = contract.extract_message_key(envelope)

        # Validate as EventEnvelope instance
        validated_env = validate_topic_message(topic, envelope, message_key=key)
        assert validated_env == envelope

        # Validate as raw JSON bytes
        raw_bytes = envelope.to_json_bytes()
        validated_bytes = validate_topic_message(topic.value, raw_bytes, message_key=key)
        assert validated_bytes == envelope

        # Validate as JSON string
        validated_str = validate_topic_message(topic, raw_bytes.decode("utf-8"), message_key=key)
        assert validated_str == envelope

        # Validate as dict
        validated_dict = validate_topic_message(topic, envelope.model_dump(), message_key=key)
        assert validated_dict == envelope


def test_contract_validation_rejects_violations() -> None:
    """validate_topic_message must reject unknown topics, unauthorized producers, and bad keys."""
    samples = build_sample_topic_messages()
    tick_env = samples[TopicName.MARKET_TICKS]

    # 1. Unregistered topic
    with pytest.raises(TopicContractViolationError, match="Unregistered Kafka topic"):
        get_topic_contract("nonexistent.topic")

    # 2. Unauthorized producer for market.ticks
    unauthorized_env = EventEnvelope[Tick](
        correlation_id="cid-unauth",
        produced_at=datetime(2026, 10, 13, 4, 30, 0, tzinfo=UTC),
        source=ComponentSource.ANALYST_AGENT,
        payload=Tick(
            symbol="RELIANCE.NS",
            timestamp=datetime(2026, 10, 13, 4, 30, 0, tzinfo=UTC),
            last_price=Decimal("2950.00"),
            volume=Decimal("100"),
        ),
    )
    with pytest.raises(TopicContractViolationError, match="not authorized to publish"):
        validate_topic_message(TopicName.MARKET_TICKS, unauthorized_env)

    # 3. Mismatched partition key
    with pytest.raises(TopicContractViolationError, match="Invalid message key"):
        validate_topic_message(
            TopicName.MARKET_TICKS,
            tick_env,
            message_key="WRONG_SYMBOL.NS",
        )

    # 4. Payload mismatch (passing market.ticks envelope to orders.fills)
    with pytest.raises(TopicContractViolationError, match="Payload validation failed"):
        validate_topic_message(TopicName.ORDERS_FILLS, tick_env)

    # 5. Unsupported message container type
    with pytest.raises(TopicContractViolationError, match="Unsupported message container"):
        validate_topic_message(TopicName.MARKET_TICKS, 12345)  # type: ignore[arg-type]


def test_topic_contract_post_init_guards() -> None:
    """TopicContract dataclass must validate model types, partitions, and retention hours."""
    with pytest.raises(TypeError, match="payload_model must be a subclass"):
        TopicContract(
            topic=TopicName.MARKET_TICKS,
            payload_model=dict,  # type: ignore[arg-type]
            envelope_model=EventEnvelope[Tick],
            key_rule=MessageKeyRule.SYMBOL,
            partitions=1,
            retention_hours=24,
            needs_dlq=False,
            dlq_topic=None,
            allowed_producers=frozenset({ComponentSource.SYSTEM}),
            allowed_consumers=frozenset({ComponentSource.SYSTEM}),
            description="Invalid payload model",
        )

    with pytest.raises(TypeError, match="envelope_model must be a subclass"):
        TopicContract(
            topic=TopicName.MARKET_TICKS,
            payload_model=Tick,
            envelope_model=Tick,  # type: ignore[arg-type]
            key_rule=MessageKeyRule.SYMBOL,
            partitions=1,
            retention_hours=24,
            needs_dlq=False,
            dlq_topic=None,
            allowed_producers=frozenset({ComponentSource.SYSTEM}),
            allowed_consumers=frozenset({ComponentSource.SYSTEM}),
            description="Invalid envelope model",
        )

    with pytest.raises(ValueError, match="partitions must be in"):
        TopicContract(
            topic=TopicName.MARKET_TICKS,
            payload_model=Tick,
            envelope_model=EventEnvelope[Tick],
            key_rule=MessageKeyRule.SYMBOL,
            partitions=0,
            retention_hours=24,
            needs_dlq=False,
            dlq_topic=None,
            allowed_producers=frozenset({ComponentSource.SYSTEM}),
            allowed_consumers=frozenset({ComponentSource.SYSTEM}),
            description="Invalid partitions",
        )

    with pytest.raises(ValueError, match="retention_hours must be >= 1"):
        TopicContract(
            topic=TopicName.MARKET_TICKS,
            payload_model=Tick,
            envelope_model=EventEnvelope[Tick],
            key_rule=MessageKeyRule.SYMBOL,
            partitions=1,
            retention_hours=0,
            needs_dlq=False,
            dlq_topic=None,
            allowed_producers=frozenset({ComponentSource.SYSTEM}),
            allowed_consumers=frozenset({ComponentSource.SYSTEM}),
            description="Invalid retention",
        )


def test_export_schemas_check_passes_and_detects_drift(tmp_path: Path) -> None:
    """export_schemas --check must pass on current repo and fail if artifacts drift."""
    assert export_main(["--check"]) == 0

    temp_contracts = tmp_path / "contracts"
    temp_doc = tmp_path / "contracts.md"

    # Initially missing -> check_only returns 1
    assert (
        export_all_schemas(
            contracts_dir=temp_contracts,
            contracts_doc_path=temp_doc,
            check_only=True,
        )
        == 1
    )

    # Export to temp -> returns 0
    assert (
        export_all_schemas(
            contracts_dir=temp_contracts,
            contracts_doc_path=temp_doc,
            check_only=False,
        )
        == 0
    )
    assert len(list(temp_contracts.glob("*.schema.json"))) == len(EXPORTED_MODELS)

    # Check after export -> returns 0
    assert (
        export_all_schemas(
            contracts_dir=temp_contracts,
            contracts_doc_path=temp_doc,
            check_only=True,
        )
        == 0
    )

    # Corrupt one schema file -> check_only returns 1
    tick_schema = temp_contracts / "Tick.schema.json"
    tick_schema.write_text('{"corrupted": true}\n', encoding="utf-8")
    assert (
        export_all_schemas(
            contracts_dir=temp_contracts,
            contracts_doc_path=temp_doc,
            check_only=True,
        )
        == 1
    )


def test_init_topics_sh_matches_topic_registry() -> None:
    """deploy/docker/init_topics.sh must match generate_rpk_init_script_content() exactly."""
    from scripts.create_topics import DEFAULT_INIT_SH_PATH, generate_rpk_init_script_content

    assert DEFAULT_INIT_SH_PATH.exists(), f"{DEFAULT_INIT_SH_PATH} does not exist"
    expected = generate_rpk_init_script_content()
    actual = DEFAULT_INIT_SH_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "deploy/docker/init_topics.sh has diverged from TOPIC_REGISTRY! "
        "Run `python -m scripts.create_topics --generate-init-script` to synchronize."
    )
