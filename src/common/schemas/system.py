"""System lifecycle, heartbeat, and operational event schemas."""

from __future__ import annotations

import uuid
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.common.schemas.base import (
    AlphaTraderBaseModel,
    DecimalNumber,
    NonEmptyStr,
    UtcDatetime,
)
from src.common.schemas.envelope import ComponentSource


class SystemEventType(StrEnum):
    """Canonical lifecycle and operational event types emitted on system.events."""

    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"
    HALT = "HALT"
    RESUME = "RESUME"
    HEARTBEAT = "HEARTBEAT"
    ERROR = "ERROR"


class EventSeverity(StrEnum):
    """Severity levels for system events."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SystemEvent(AlphaTraderBaseModel):
    """Operational telemetry event for service startup, shutdown, halt, resume, or heartbeat."""

    system_event_id: UUID = Field(default_factory=uuid.uuid4)
    event_type: SystemEventType
    severity: EventSeverity
    component: ComponentSource
    occurred_at: UtcDatetime
    message: NonEmptyStr
    metadata: dict[str, str | int | bool | DecimalNumber] = Field(default_factory=dict)
