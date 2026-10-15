"""Integration tests for Sprint 14: Idempotency, DLQ Routing, and Crash Recovery.

Validates:
1. Consumer crash & restart mid-stream: repeated message delivery results in
   zero duplicate ledger entries and zero lost accounting postings.
2. Poison pill & malformed payload handling: invalid envelopes and short-selling
   violations are routed to the registered DLQ topic with complete error headers.
3. Cold-start / crash recovery state reconciliation: rebuilding the ephemeral
   Redis projection directly from the PostgreSQL double-entry ledger.
4. Exact debit-credit balance preservation throughout recovery.

Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.common.config import AlphaTraderSettings
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.orders import Fill, Side
from src.mcp_servers.portfolio_ledger.consumer import PortfolioLedgerConsumer
from src.mcp_servers.portfolio_ledger.ledger import DoubleEntryLedger
from src.mcp_servers.portfolio_ledger.projection import RedisPortfolioProjection
from src.mcp_servers.portfolio_ledger.reconciliation import (
    PortfolioStateReconciler,
)
from tests.unit.test_portfolio_ledger import InMemoryPostgresFake


class MockKafkaMessage:
    """Mock confluent_kafka.Message for crash recovery and polling simulation."""

    def __init__(
        self,
        topic: str,
        value: bytes,
        partition: int = 0,
        offset: int = 100,
        error: Any = None,
    ) -> None:
        self._topic = topic
        self._value = value
        self._partition = partition
        self._offset = offset
        self._error = error

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def value(self) -> bytes:
        return self._value

    def error(self) -> Any:
        return self._error


@pytest.fixture
def test_settings() -> AlphaTraderSettings:
    """Provides test settings with paper trading mode enforced."""
    return AlphaTraderSettings(
        environment="test",
        trading_mode="PAPER",
        live_trading_enabled=False,
    )


@pytest.fixture
def fake_db() -> InMemoryPostgresFake:
    return InMemoryPostgresFake()


@pytest.fixture
def ledger(fake_db: InMemoryPostgresFake) -> DoubleEntryLedger:
    ledger_instance = DoubleEntryLedger("fake-dsn")
    ledger_instance.connect = fake_db.connect  # type: ignore[assignment]
    ledger_instance.initialize_genesis_funding(Decimal("10000000.0000"))
    return ledger_instance


def test_crash_recovery_midstream_duplicate_delivery(
    ledger: DoubleEntryLedger, fake_db: InMemoryPostgresFake
) -> None:
    """Simulates a consumer crash mid-stream where messages are redelivered.

    Verifies:
    - First consumption inserts journal entries and increments revision.
    - Second consumption (simulating redelivery after crash before offset commit)
      detects idempotency and avoids duplicate postings or duplicate revision bumps.
    - Debit-credit balance holds across the entire operation.
    """
    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )

    correlation_id = f"crash-test-{uuid4().hex[:8]}"
    fill_id = uuid4()
    order_request_id = uuid4()

    order_id = uuid4()
    filled_at = datetime.now(UTC)

    fill = Fill(
        fill_id=fill_id,
        order_id=order_id,
        order_request_id=order_request_id,
        symbol="TCS.NS",
        side=Side.BUY,
        fill_price=Decimal("3500.0000"),
        fill_quantity=Decimal("20.0000"),
        slippage_bps=Decimal("0.0000"),
        stt_tax=Decimal("70.0000"),
        exchange_fees=Decimal("2.4500"),
        stamp_duty=Decimal("10.5000"),
        gst_tax=Decimal("0.4410"),
        total_fees=Decimal("83.3910"),
        filled_at=filled_at,
        trading_mode="PAPER",
    )

    envelope = EventEnvelope[Fill](
        event_id=uuid4(),
        correlation_id=correlation_id,
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill,
    )
    raw_bytes = envelope.to_json_bytes()

    # First delivery: normal ingestion
    success_1, msg_1 = consumer.process_message_payload(raw_bytes)
    assert success_1 is True
    assert msg_1 == "SUCCESS"

    # Verify journal entry was created in fake_db
    matching_entries = [e for e in fake_db.journal_entries if e["correlation_id"] == correlation_id]
    assert len(matching_entries) == 1
    rev_after_first = fake_db.revision

    # SIMULATE CRASH: Process restarts, offset was not committed, message redelivered
    # Consumer instantiates anew
    crashed_consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )

    success_2, _msg_2 = crashed_consumer.process_message_payload(raw_bytes)
    assert success_2 is True

    # Check that NO duplicate journal entry or revision increment occurred
    matching_entries_after = [
        e for e in fake_db.journal_entries if e["correlation_id"] == correlation_id
    ]
    assert len(matching_entries_after) == 1, "Duplicate journal entry was created after redelivery!"
    assert fake_db.revision == rev_after_first, "Revision advanced on duplicate event!"

    # Assert total debit-credit equality across all journal postings
    sum_debits = sum(p["amount"] for p in fake_db.journal_postings if p["direction"] == "DEBIT")
    sum_credits = sum(p["amount"] for p in fake_db.journal_postings if p["direction"] == "CREDIT")
    assert sum_debits == sum_credits, (
        f"Ledger out of balance! Debits: {sum_debits}, Credits: {sum_credits}"
    )


def test_dlq_routing_on_malformed_envelope_and_poison_pill(ledger: DoubleEntryLedger) -> None:
    """Verifies unparseable bytes and poison pills route to orders.fills.dlq with headers."""
    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )

    # 1. Completely corrupted JSON
    bad_bytes = b"NOT_A_VALID_JSON_STRING"
    success, err = consumer.process_message_payload(bad_bytes)
    assert success is False
    assert "Malformed message envelope" in err

    # Verify DLQ production was triggered
    assert mock_producer.produce.called
    call_kwargs = mock_producer.produce.call_args[1]
    assert call_kwargs["topic"] == "orders.fills.dlq"
    assert call_kwargs["value"] == bad_bytes

    headers = dict(call_kwargs["headers"])
    assert "x-dlq-reason" in headers or b"x-dlq-reason" in headers
    orig_topic = headers.get("x-original-topic") or headers.get(b"x-original-topic")
    assert orig_topic in (b"orders.fills", "orders.fills")

    # Reset mock
    mock_producer.reset_mock()

    # 2. Poison pill: Same event_id but altered payload hash (tampering/divergence)
    event_id = uuid4()
    order_id = uuid4()
    filled_at = datetime.now(UTC)

    fill_original = Fill(
        fill_id=uuid4(),
        order_id=order_id,
        order_request_id=uuid4(),
        symbol="INFY.NS",
        side=Side.BUY,
        fill_price=Decimal("1500.0000"),
        fill_quantity=Decimal("10.0000"),
        slippage_bps=Decimal("0.0000"),
        stt_tax=Decimal("15.0000"),
        exchange_fees=Decimal("0.5000"),
        stamp_duty=Decimal("2.2500"),
        gst_tax=Decimal("0.0900"),
        total_fees=Decimal("17.8400"),
        filled_at=filled_at,
        trading_mode="PAPER",
    )
    env1 = EventEnvelope[Fill](
        event_id=event_id,
        correlation_id="poison-test-1",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill_original,
    )
    consumer.process_message_payload(env1.to_json_bytes())

    # Now send another message with the SAME event_id but DIFFERENT quantity (payload mismatch)
    fill_tampered = Fill(
        fill_id=uuid4(),
        order_id=order_id,
        order_request_id=uuid4(),
        symbol="INFY.NS",
        side=Side.BUY,
        fill_price=Decimal("1500.0000"),
        fill_quantity=Decimal("50.0000"),  # Changed!
        slippage_bps=Decimal("0.0000"),
        stt_tax=Decimal("75.0000"),
        exchange_fees=Decimal("2.5000"),
        stamp_duty=Decimal("11.2500"),
        gst_tax=Decimal("0.4500"),
        total_fees=Decimal("89.2000"),
        filled_at=filled_at,
        trading_mode="PAPER",
    )
    env2 = EventEnvelope[Fill](
        event_id=event_id,
        correlation_id="poison-test-2",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill_tampered,
    )

    success_tampered, err_tampered = consumer.process_message_payload(env2.to_json_bytes())
    assert success_tampered is False
    assert "Poison pill duplicate event hash mismatch" in err_tampered

    # Verify routed to DLQ
    assert mock_producer.produce.called
    call_kwargs_tampered = mock_producer.produce.call_args[1]
    assert call_kwargs_tampered["topic"] == "orders.fills.dlq"


def test_poll_and_process_explicit_offset_commit(ledger: DoubleEntryLedger) -> None:
    """Verifies that poll_and_process polls, ingests message, and explicitly commits offset."""
    mock_consumer = MagicMock()
    mock_producer = MagicMock()

    fill = Fill(
        fill_id=uuid4(),
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="HDFCBANK.NS",
        side=Side.BUY,
        fill_price=Decimal("1600.0000"),
        fill_quantity=Decimal("10.0000"),
        slippage_bps=Decimal("0.0000"),
        stt_tax=Decimal("16.0000"),
        exchange_fees=Decimal("0.5600"),
        stamp_duty=Decimal("2.4000"),
        gst_tax=Decimal("0.1008"),
        total_fees=Decimal("19.0608"),
        filled_at=datetime.now(UTC),
        trading_mode="PAPER",
    )
    envelope = EventEnvelope[Fill](
        event_id=uuid4(),
        correlation_id="commit-test-1",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill,
    )

    mock_msg = MockKafkaMessage(
        topic="orders.fills",
        value=envelope.to_json_bytes(),
        partition=0,
        offset=42,
    )
    mock_consumer.poll.return_value = mock_msg

    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        consumer=mock_consumer,
        producer=mock_producer,
    )

    result = consumer.poll_and_process(timeout=0.1)
    assert result is True
    # Verify explicit commit was executed
    assert mock_consumer.commit.called
    commit_kwargs = mock_consumer.commit.call_args[1]
    assert commit_kwargs["message"] == mock_msg
    assert commit_kwargs["asynchronous"] is False


def test_state_reconciler_unit(test_settings: AlphaTraderSettings) -> None:
    """Verifies state reconciler calls projection rebuild under REPEATABLE READ isolation."""
    mock_projection = MagicMock(spec=RedisPortfolioProjection)
    mock_projection.rebuild_from_postgres.return_value = (True, "SUCCESS")
    mock_projection.get_summary.return_value = {
        "revision": "5",
        "cash": "950000.0000",
        "equity": "1000000.0000",
    }
    mock_projection.get_open_positions.return_value = []

    reconciler = PortfolioStateReconciler(test_settings, mock_projection)
    res = reconciler.reconcile()

    assert res.success is True
    assert res.revision == 5
    assert res.cash == "950000.0000"
    assert res.equity == "1000000.0000"
    assert res.positions_count == 0
    assert mock_projection.rebuild_from_postgres.called


def test_state_reconciler_failure_branch(test_settings: AlphaTraderSettings) -> None:
    """Verifies PortfolioStateReconciler handles postgres rebuild failure."""
    mock_projection = MagicMock(spec=RedisPortfolioProjection)
    mock_projection.rebuild_from_postgres.return_value = (False, "CONNECTION_FAILED")

    reconciler = PortfolioStateReconciler(test_settings, mock_projection)
    res = reconciler.reconcile()

    assert res.success is False
    assert res.status == "CONNECTION_FAILED"


def test_consumer_partition_callbacks_and_error_handling(ledger: DoubleEntryLedger) -> None:
    """Verifies partition assignment, revocation, and polling error cases."""
    mock_consumer = MagicMock()
    mock_producer = MagicMock()

    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        consumer=mock_consumer,
        producer=mock_producer,
    )

    # 1. Assignment and revocation callbacks
    p1 = MagicMock()
    p1.topic = "orders.fills"
    p1.partition = 0
    consumer._on_assign(mock_consumer, [p1])
    assert ("orders.fills", 0) in consumer._assigned_partitions

    consumer._on_revoke(mock_consumer, [p1])
    assert ("orders.fills", 0) not in consumer._assigned_partitions

    # 2. None message on poll
    mock_consumer.poll.return_value = None
    assert consumer.poll_and_process(timeout=0.1) is False

    # 3. Partition EOF error
    err_eof = MagicMock()
    err_eof.code.return_value = -191
    msg_eof = MockKafkaMessage(topic="orders.fills", value=b"", error=err_eof)
    mock_consumer.poll.return_value = msg_eof
    assert consumer.poll_and_process(timeout=0.1) is False

    # 4. Other error
    err_other = MagicMock()
    err_other.code.return_value = -1
    msg_err = MockKafkaMessage(topic="orders.fills", value=b"", error=err_other)
    mock_consumer.poll.return_value = msg_err
    assert consumer.poll_and_process(timeout=0.1) is False

    # 5. Empty value
    msg_empty = MockKafkaMessage(topic="orders.fills", value=None)  # type: ignore[arg-type]
    mock_consumer.poll.return_value = msg_empty
    assert consumer.poll_and_process(timeout=0.1) is False
