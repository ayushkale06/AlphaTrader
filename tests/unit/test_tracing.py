"""Unit and integration tests for Sprint 15: OpenTelemetry Distributed Tracing.

Validates:
1. Span creation and metadata injection (`service.name`, `correlation_id`, `trading_mode: "PAPER"`).
2. Exception capture and error status recording on failure.
3. Decorator instrumentation for MCP tools (`@traced_tool`) and LangGraph nodes (`@traced_node`).
4. Trace waterfall execution across multiple sequential stages with timing and attributes.
5. In-memory span recording for local test verification and audit reporting.

Governance: ADR-002, ADR-011, ADR-012.
"""

from __future__ import annotations

import pytest

from src.common.logging import correlation_context
from src.observability import (
    get_span_recorder,
    get_tracer,
    trace_span,
    traced_node,
    traced_tool,
)


@pytest.fixture(autouse=True)
def clean_span_recorder() -> None:
    """Clear recorded spans before and after each test."""
    recorder = get_span_recorder()
    recorder.clear()
    yield
    recorder.clear()


def test_span_creation_and_attributes() -> None:
    """Verifies span captures service name, custom attributes, and correlation_id."""
    test_corr_id = "test-trace-12345"

    with (
        correlation_context(test_corr_id),
        trace_span("test.operation", attributes={"symbol": "RELIANCE.NS", "stage": "INGEST"}),
    ):
        pass

    recorder = get_span_recorder()
    spans = recorder.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "test.operation"
    assert span.status_code == "OK"
    assert span.attributes["correlation_id"] == test_corr_id
    assert span.attributes["trading_mode"] == "PAPER"
    assert span.attributes["symbol"] == "RELIANCE.NS"
    assert span.attributes["stage"] == "INGEST"
    assert span.duration_ms >= 0.0


def test_span_records_exception() -> None:
    """Verifies span catches unhandled exceptions and sets status ERROR."""
    test_corr_id = "test-error-trace"

    with (
        correlation_context(test_corr_id),
        pytest.raises(ValueError, match="Engine failure"),
        trace_span("failing.operation"),
    ):
        raise ValueError("Engine failure")

    recorder = get_span_recorder()
    spans = recorder.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "failing.operation"
    assert span.status_code == "ERROR"
    assert span.status_message == "Engine failure"
    assert span.attributes["correlation_id"] == test_corr_id


def test_traced_tool_decorator() -> None:
    """Verifies @traced_tool wraps MCP tool call and records parameters."""
    test_corr_id = "test-tool-trace"

    @traced_tool(server_name="technical-analyst", tool_name="get_indicators")
    def sample_tool(symbol: str, interval: str) -> dict[str, str]:
        return {"symbol": symbol, "status": "calculated"}

    with correlation_context(test_corr_id):
        result = sample_tool(symbol="TCS.NS", interval="5m")

    assert result["status"] == "calculated"

    recorder = get_span_recorder()
    spans = recorder.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "mcp.technical-analyst.get_indicators"
    assert span.attributes["mcp.server"] == "technical-analyst"
    assert span.attributes["mcp.tool"] == "get_indicators"
    assert span.attributes["symbol"] == "TCS.NS"
    assert span.attributes["correlation_id"] == test_corr_id


def test_traced_node_decorator() -> None:
    """Verifies @traced_node wraps orchestrator state transition."""
    test_corr_id = "test-node-trace"

    @traced_node("risk_evaluation")
    def sample_node(state_id: int) -> str:
        return f"evaluated_{state_id}"

    with correlation_context(test_corr_id):
        res = sample_node(42)

    assert res == "evaluated_42"

    recorder = get_span_recorder()
    spans = recorder.get_spans()
    assert len(spans) == 1

    span = spans[0]
    assert span.name == "orchestrator.node.risk_evaluation"
    assert span.attributes["orchestrator.node"] == "risk_evaluation"
    assert span.attributes["correlation_id"] == test_corr_id


def test_waterfall_trace_multi_stage() -> None:
    """Verifies sequential multi-stage trace waterfall simulation."""
    test_corr_id = "waterfall-trace-999"

    with correlation_context(test_corr_id), trace_span("pipeline.tick_to_fill"):
        with trace_span("stage.analyst"):
            pass
        with trace_span("stage.quant"):
            pass
        with trace_span("stage.risk"):
            pass
        with trace_span("stage.execution"):
            pass

    recorder = get_span_recorder()
    spans = recorder.get_spans()
    assert len(spans) == 5

    names = [s.name for s in spans]
    assert names == [
        "stage.analyst",
        "stage.quant",
        "stage.risk",
        "stage.execution",
        "pipeline.tick_to_fill",
    ]
    for s in spans:
        assert s.attributes["correlation_id"] == test_corr_id
        assert s.attributes["trading_mode"] == "PAPER"
        assert s.status_code == "OK"


def test_custom_tracer_service_name() -> None:
    """Verifies get_tracer creates custom tracer instance."""
    custom_tracer = get_tracer("alphatrader-risk-guardrail")
    assert custom_tracer.service_name == "alphatrader-risk-guardrail"

    with custom_tracer.span("risk.preflight_check"):
        pass

    spans = get_span_recorder().get_spans()
    assert len(spans) == 1
    assert spans[0].attributes["service.name"] == "alphatrader-risk-guardrail"
