"""Unit tests for structured JSON logging and correlation_id propagation."""

from __future__ import annotations

import io
import json

from src.common.config import AlphaTraderSettings
from src.common.logging import (
    configure_logging,
    correlation_context,
    generate_correlation_id,
    get_correlation_id,
    get_logger,
    set_correlation_id,
)


def test_correlation_context_propagates_and_restores() -> None:
    """correlation_context must bind the ID inside the block and restore prior state on exit."""
    outer_id = set_correlation_id("cid-outer-001")
    assert get_correlation_id() == "cid-outer-001"
    assert outer_id == "cid-outer-001"

    with correlation_context("cid-inner-002") as active_id:
        assert active_id == "cid-inner-002"
        assert get_correlation_id() == "cid-inner-002"

    assert get_correlation_id() == "cid-outer-001"


def test_set_correlation_id_generates_uuid_when_none() -> None:
    """Calling set_correlation_id() without arguments generates a non-empty UUIDv4."""
    generated = set_correlation_id()
    assert len(generated) == 36
    assert get_correlation_id() == generated
    assert len(generate_correlation_id()) == 36


def test_structured_json_logging_carries_correlation_id_across_lines() -> None:
    """Every JSON log line inside a correlation context must include the exact correlation_id."""
    stream = io.StringIO()
    settings = AlphaTraderSettings(
        service_name="mcp-risk-guardrail",
        app_env="test",
        log_level="DEBUG",
        log_json=True,
    )
    configure_logging(settings=settings, stream=stream)
    logger = get_logger("risk.evaluator")

    with correlation_context("req-nifty-tick-99412"):
        logger.info("Evaluating candidate order", extra={"symbol": "INFY.NS", "qty": 50})
        logger.warning("Position approaching concentration band", extra={"utilization": 0.85})

    lines = [line for line in stream.getvalue().strip().splitlines() if line.strip()]
    assert len(lines) == 2

    first_record = json.loads(lines[0])
    second_record = json.loads(lines[1])

    for record in (first_record, second_record):
        assert record["correlation_id"] == "req-nifty-tick-99412"
        assert record["service"] == "mcp-risk-guardrail"
        assert record["environment"] == "test"
        assert record["trading_mode"] == "PAPER"
        assert record["logger"] == "alphatrader.risk.evaluator"
        assert "timestamp" in record

    assert first_record["level"] == "INFO"
    assert first_record["message"] == "Evaluating candidate order"
    assert first_record["extra"]["symbol"] == "INFY.NS"
    assert first_record["extra"]["qty"] == 50

    assert second_record["level"] == "WARNING"
    assert second_record["extra"]["utilization"] == 0.85


def test_structured_json_logging_captures_exceptions() -> None:
    """Exceptions logged via logger.exception must serialize traceback into 'exception' key."""
    stream = io.StringIO()
    settings = AlphaTraderSettings(log_json=True)
    configure_logging(settings=settings, stream=stream)
    logger = get_logger("alphatrader.test_exc")

    with correlation_context("cid-err-500"):
        try:
            raise RuntimeError("Simulated feed disconnect")
        except RuntimeError:
            logger.exception("Feed interrupted")

    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] == "ERROR"
    assert payload["correlation_id"] == "cid-err-500"
    assert "RuntimeError: Simulated feed disconnect" in payload["exception"]


def test_text_logging_fallback_includes_correlation_id() -> None:
    """When log_json=False, human-readable logs still carry the correlation_id."""
    stream = io.StringIO()
    settings = AlphaTraderSettings(log_json=False, log_level="INFO")
    configure_logging(settings=settings, stream=stream)
    logger = get_logger("text_mode")

    with correlation_context("cid-text-777"):
        logger.info("Human readable log check")

    output = stream.getvalue()
    assert "cid=cid-text-777" in output
    assert "Human readable log check" in output
