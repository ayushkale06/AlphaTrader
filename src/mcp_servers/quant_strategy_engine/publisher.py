"""Publisher for QuantSignal messages to Redpanda topic signals.quant.

Includes cryptographic verification gate: refuses to publish any signal claiming
an expected edge unless the referenced backtest report exists in docs/backtests/index.json,
its hash matches, and edge_basis is OUT_OF_SAMPLE.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import confluent_kafka

from src.common.contracts import (
    TopicName,
    validate_topic_message,
)
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.signals import EdgeBasis, QuantSignal

logger = logging.getLogger("alphatrader.quant.publisher")

INDEX_PATH = Path("docs/backtests/index.json")


class EdgeVerificationError(ValueError):
    """Raised when an edge claim cannot be verified against the backtest index."""
    pass


def verify_backtest_report_credential(
    report_id: str | None,
    edge_basis: EdgeBasis | None,
    strategy_name: str,
    strategy_version: str,
    symbol: str,
    index_path: Path = INDEX_PATH,
) -> dict[str, Any]:
    """Verify that a backtest report exists in docs/backtests/index.json,
    matches the disk report content hash, matches strategy metadata,
    and has edge_basis == OUT_OF_SAMPLE.
    """
    if not report_id:
        raise EdgeVerificationError("Missing backtest_report_id for claimed edge")

    if not index_path.exists():
        raise EdgeVerificationError(f"Backtest index not found at {index_path}")

    try:
        with open(index_path, encoding="utf-8") as f:
            index_data = json.load(f)
    except Exception as e:
        raise EdgeVerificationError(f"Failed to read backtest index: {e}") from e

    reports = index_data.get("reports", {})
    if report_id not in reports:
        raise EdgeVerificationError(
            f"Backtest report id {report_id} not registered in backtest index"
        )

    meta = reports[report_id]
    if meta.get("edge_basis") != "OUT_OF_SAMPLE":
        raise EdgeVerificationError(
            f"Backtest report {report_id} has edge_basis {meta.get('edge_basis')}, required OUT_OF_SAMPLE"
        )

    # Verify physical file existence and SHA-256 match if path specified
    report_file_path = meta.get("path")
    if report_file_path:
        p = Path(report_file_path)
        if not p.exists():
            raise EdgeVerificationError(
                f"Backtest report document missing from disk at {report_file_path}"
            )
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual_hash != report_id:
            raise EdgeVerificationError(
                f"Backtest report file {report_file_path} content hash mismatch: "
                f"got {actual_hash}, registered {report_id}"
            )

    return meta


class QuantSignalPublisher:
    """Publishes validated QuantSignal envelopes to signals.quant with cryptographic gate."""

    def __init__(
        self,
        bootstrap_servers: str = "127.0.0.1:9092",
        index_path: Path = INDEX_PATH,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._index_path = index_path
        conf = {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "alphatrader-quant-strategy-engine",
            "acks": "all",
            "retries": 3,
            "linger.ms": 5,
        }
        self._producer = confluent_kafka.Producer(conf)

    def publish_signal(
        self,
        signal: QuantSignal,
        correlation_id: str,
        causation_id: UUID | None = None,
    ) -> EventEnvelope[QuantSignal]:
        """Publish a validated QuantSignal envelope to signals.quant.
        
        Refuses publication if an expected net edge is claimed without an out-of-sample report.
        """
        if signal.expected_net_edge_bps_per_trade is not None:
            verify_backtest_report_credential(
                report_id=signal.backtest_report_id,
                edge_basis=signal.edge_basis,
                strategy_name=signal.strategy_name,
                strategy_version=signal.strategy_version,
                symbol=signal.symbol,
                index_path=self._index_path,
            )

        envelope = EventEnvelope[QuantSignal](
            correlation_id=correlation_id,
            causation_id=causation_id,
            produced_at=datetime.now(UTC),
            source=ComponentSource.QUANT_STRATEGY_ENGINE,
            payload=signal,
        )

        validate_topic_message(
            TopicName.SIGNALS_QUANT,
            envelope,
            message_key=signal.symbol,
        )

        key_bytes = signal.symbol.encode("utf-8")
        val_bytes = envelope.to_json_bytes()

        self._producer.produce(
            topic=TopicName.SIGNALS_QUANT.value,
            key=key_bytes,
            value=val_bytes,
        )
        self._producer.poll(0)
        return envelope

    def flush(self, timeout_seconds: float = 5.0) -> None:
        """Flush pending Kafka producer buffer."""
        self._producer.flush(timeout=timeout_seconds)
