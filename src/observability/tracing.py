"""OpenTelemetry distributed tracing and correlation ID bridge.

Provides span creation, trace propagation, and contextual metadata injection
for MCP tool calls, LangGraph agent nodes, Kafka message producers/consumers,
and database transactions.

Governance: ADR-002, ADR-011, ADR-012.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.trace import (
    Span,
    StatusCode,
    Tracer,
)

from src.common.logging import get_correlation_id

logger = logging.getLogger("alphatrader.observability.tracing")

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(slots=True)
class RecordedSpan:
    """In-memory representation of an executed OpenTelemetry span for testing and auditing."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    status_code: str = "UNSET"
    status_message: str | None = None
    start_time_ns: int = 0
    end_time_ns: int = 0
    duration_ms: float = 0.0


class InMemorySpanRecorder:
    """In-memory span buffer recording trace spans for local inspection and assertions."""

    def __init__(self) -> None:
        self._spans: list[RecordedSpan] = []

    def record(self, span: RecordedSpan) -> None:
        self._spans.append(span)

    def get_spans(self) -> list[RecordedSpan]:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()


_span_recorder = InMemorySpanRecorder()


def get_span_recorder() -> InMemorySpanRecorder:
    """Access global in-memory span recorder for test verification."""
    return _span_recorder


class AlphaTraderTracer:
    """Central OpenTelemetry tracer bridge for AlphaTrader services."""

    def __init__(self, service_name: str = "alphatrader-core") -> None:
        self.service_name = service_name
        self._tracer: Tracer = trace.get_tracer(service_name)

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> Iterator[Span]:
        """Create a tracing span enriched with correlation_id and paper trading invariants."""
        corr_id = correlation_id or get_correlation_id()
        attrs: dict[str, Any] = {
            "service.name": self.service_name,
            "correlation_id": corr_id,
            "trading_mode": "PAPER",
        }
        if attributes:
            attrs.update(attributes)

        # Record start timestamp
        start_ns = time.time_ns()
        rec_span = RecordedSpan(name=name, attributes=attrs, start_time_ns=start_ns)

        with self._tracer.start_as_current_span(name, attributes=attrs) as span:
            try:
                yield span
                rec_span.status_code = "OK"
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
                rec_span.status_code = "ERROR"
                rec_span.status_message = str(exc)
                raise
            finally:
                end_ns = time.time_ns()
                rec_span.end_time_ns = end_ns
                rec_span.duration_ms = (end_ns - start_ns) / 1_000_000.0
                _span_recorder.record(rec_span)


_global_tracer = AlphaTraderTracer()


def get_tracer(service_name: str = "alphatrader-core") -> AlphaTraderTracer:
    """Get or create an AlphaTraderTracer instance."""
    if service_name == "alphatrader-core":
        return _global_tracer
    return AlphaTraderTracer(service_name)


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> Iterator[Span]:
    """Convenience context manager for creating an instrumented trace span."""
    with _global_tracer.span(
        name=name,
        attributes=attributes,
        correlation_id=correlation_id,
    ) as span:
        yield span


def traced_tool(server_name: str, tool_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to instrument MCP tool call executions with OpenTelemetry spans."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span_name = f"mcp.{server_name}.{tool_name}"
            attrs: dict[str, Any] = {
                "mcp.server": server_name,
                "mcp.tool": tool_name,
            }
            if "symbol" in kwargs:
                attrs["symbol"] = str(kwargs["symbol"])

            with trace_span(span_name, attributes=attrs):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def traced_node(node_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to instrument LangGraph agent workflow node execution."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span_name = f"orchestrator.node.{node_name}"
            attrs: dict[str, Any] = {
                "orchestrator.node": node_name,
            }
            with trace_span(span_name, attributes=attrs):
                return func(*args, **kwargs)

        return wrapper

    return decorator
