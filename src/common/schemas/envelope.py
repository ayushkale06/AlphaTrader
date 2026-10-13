"""Generic Kafka/Redpanda event envelope with deterministic JSON byte serialization.

Every message published to an AlphaTrader topic is wrapped in ``EventEnvelope[T]``
so correlation IDs, causation lineage, producer identity, UTC timestamps, and
schema versions are enforced uniformly across all agents and MCP servers.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, Self, TypeVar
from uuid import UUID

from pydantic import Field, field_validator

from src.common.schemas.base import AlphaTraderBaseModel, NonEmptyStr, UtcDatetime

CURRENT_SCHEMA_VERSION = "1.0.0"

PayloadT = TypeVar("PayloadT", bound=AlphaTraderBaseModel, covariant=True)


class SchemaVersionMismatchError(ValueError):
    """Raised when an incoming EventEnvelope carries an incompatible schema_version."""


class ComponentSource(StrEnum):
    """Canonical identifiers for every AlphaTrader producer and consumer component."""

    MARKET_DATA_STREAMER = "mcp.market_data_streamer"
    TECHNICAL_ANALYST = "mcp.technical_analyst"
    QUANT_STRATEGY_ENGINE = "mcp.quant_strategy_engine"
    RISK_GUARDRAIL = "mcp.risk_guardrail"
    PAPER_EXECUTION_BROKER = "mcp.paper_execution_broker"
    PORTFOLIO_LEDGER = "mcp.portfolio_ledger"
    ANALYST_AGENT = "agent.analyst"
    QUANT_AGENT = "agent.quant"
    RISK_AGENT = "agent.risk"
    EXECUTION_AGENT = "agent.execution"
    ORCHESTRATOR = "orchestrator.langgraph"
    SYSTEM = "system.core"


class EventEnvelope(AlphaTraderBaseModel, Generic[PayloadT]):
    """Generic immutable event wrapper for all Redpanda/Kafka topic messages."""

    event_id: UUID = Field(
        default_factory=uuid.uuid4,
        description="Globally unique UUIDv4 identifier for idempotency and deduplication.",
    )
    correlation_id: NonEmptyStr = Field(
        description="End-to-end trace correlation ID carried from the originating tick or trigger.",
    )
    causation_id: UUID | None = Field(
        default=None,
        description="UUID of the upstream event that directly caused this event.",
    )
    schema_version: str = Field(
        default=CURRENT_SCHEMA_VERSION,
        description="Semantic version of the envelope/payload contract.",
    )
    produced_at: UtcDatetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        description="UTC timestamp when the event was emitted.",
    )
    source: ComponentSource = Field(
        description="Canonical AlphaTrader component that produced this event.",
    )
    payload: PayloadT = Field(
        description="Strongly typed domain event payload.",
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_supported_schema_version(cls, value: object) -> str:
        """Reject unsupported or mismatched schema versions."""
        if not isinstance(value, str) or not value.strip():
            msg = "schema_version must be a non-empty semantic version string."
            raise SchemaVersionMismatchError(msg)
        if value != CURRENT_SCHEMA_VERSION:
            msg = (
                f"Schema version mismatch: received schema_version={value!r}, "
                f"expected {CURRENT_SCHEMA_VERSION!r}."
            )
            raise SchemaVersionMismatchError(msg)
        return value

    def to_json_bytes(self) -> bytes:
        """Serialize the envelope deterministically to canonical UTF-8 JSON bytes."""
        raw_dict: dict[str, Any] = json.loads(self.model_dump_json())
        canonical_str = json.dumps(
            raw_dict,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return canonical_str.encode("utf-8")

    @classmethod
    def from_json_bytes(
        cls,
        raw: bytes | str,
        *,
        expected_schema_version: str = CURRENT_SCHEMA_VERSION,
    ) -> Self:
        """Deserialize and validate an EventEnvelope from UTF-8 JSON bytes or string."""
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            incoming_version = parsed.get("schema_version")
            if incoming_version != expected_schema_version:
                msg = (
                    f"Schema version mismatch: received schema_version={incoming_version!r}, "
                    f"expected {expected_schema_version!r}."
                )
                raise SchemaVersionMismatchError(msg)
        return cls.model_validate_json(text)
