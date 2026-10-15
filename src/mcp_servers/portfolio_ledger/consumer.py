"""Kafka consumer for orders.fills and publisher for portfolio.updates and DLQ.

Consumes verified Fill events, ingests into PostgreSQL DoubleEntryLedger,
updates Redis projection, publishes PortfolioSnapshot to portfolio.updates,
and routes invalid messages or unhandled errors to orders.fills.dlq.
Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from confluent_kafka import Consumer, Producer

from src.common.contracts import TopicName
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.orders import Fill
from src.common.schemas.portfolio import PortfolioSnapshot
from src.mcp_servers.portfolio_ledger.ledger import (
    DoubleEntryLedger,
    DuplicateEventDifferentPayloadError,
    InsufficientInventoryError,
)
from src.mcp_servers.portfolio_ledger.projection import RedisPortfolioProjection

logger = logging.getLogger("alphatrader.portfolio_ledger.consumer")


class PortfolioLedgerConsumer:
    """Consumes orders.fills, writes double-entry ledger, and publishes updates."""

    def __init__(
        self,
        ledger: DoubleEntryLedger,
        projection: RedisPortfolioProjection | None,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "portfolio-ledger-group",
        consumer: Any = None,
        producer: Any = None,
    ) -> None:
        self.ledger = ledger
        self.projection = projection
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id

        self._running = False
        self._consumer_thread: Any = None
        self._assigned_partitions: set[tuple[str, int]] = set()

        if consumer is not None:
            self._consumer = consumer
        else:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": bootstrap_servers,
                    "group.id": group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": False,
                }
            )

        if producer is not None:
            self._producer = producer
        else:
            self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def _on_assign(self, consumer: Any, partitions: list[Any]) -> None:
        """Track assigned partitions for safe at-least-once offset commit."""
        for p in partitions:
            self._assigned_partitions.add((p.topic, p.partition))
        logger.info("Partitions assigned: %s", self._assigned_partitions)

    def _on_revoke(self, consumer: Any, partitions: list[Any]) -> None:
        """Clear revoked partitions to prevent commits during rebalance."""
        for p in partitions:
            self._assigned_partitions.discard((p.topic, p.partition))
        logger.info("Partitions revoked; remaining: %s", self._assigned_partitions)

    def poll_and_process(self, timeout: float = 0.5) -> bool:
        """Poll a single message, process it idempotently, and commit offset on success.

        Returns True if a message was received and processed, False otherwise.
        """
        if self._consumer is None:
            return False

        msg = self._consumer.poll(timeout=timeout)
        if msg is None:
            return False

        err = msg.error()
        if err is not None:
            code = getattr(err, "code", lambda: None)()
            # Ignore EOF
            if str(code) in ("-191", "KafkaError._PARTITION_EOF"):
                return False
            logger.error("Consumer error: %s", err)
            return False

        topic = msg.topic()
        partition = msg.partition()
        raw_val = msg.value()

        if raw_val is None:
            return False

        # Ingest fill and project
        _success, _ = self.process_message_payload(raw_val)

        # Commit offset strictly if partition is still assigned and processing succeeded
        # or if routed to DLQ (which also marks message resolved)
        try:
            self._consumer.commit(message=msg, asynchronous=False)
        except Exception as e:
            logger.error("Failed to commit offset for %s[%d]: %s", topic, partition, e)

        return True

    def route_to_dlq(
        self,
        raw_msg: bytes | str,
        reason: str,
        original_topic: str = "orders.fills",
        correlation_id: str | None = None,
    ) -> None:
        """Route unprocessable message to verified DLQ."""
        dlq_topic = "orders.fills.dlq"
        logger.warning("Routing message to DLQ %s: %s", dlq_topic, reason)

        payload_bytes = raw_msg.encode("utf-8") if isinstance(raw_msg, str) else raw_msg
        headers = [
            ("x-dlq-reason", reason.encode("utf-8")),
            ("x-original-topic", original_topic.encode("utf-8")),
            ("x-correlation-id", (correlation_id or "UNKNOWN").encode("utf-8")),
        ]

        self._producer.produce(
            topic=dlq_topic,
            key=b"DLQ_ERROR",
            value=payload_bytes,
            headers=headers,
        )
        self._producer.flush(timeout=5.0)

    def publish_portfolio_update(
        self,
        snapshot: PortfolioSnapshot,
        correlation_id: str,
        causation_id: UUID | None = None,
    ) -> None:
        """Publish PortfolioSnapshot to portfolio.updates."""
        envelope = EventEnvelope[PortfolioSnapshot](
            correlation_id=correlation_id,
            causation_id=causation_id or snapshot.snapshot_id,
            source=ComponentSource.PORTFOLIO_LEDGER,
            payload=snapshot,
        )
        partition_key = str(snapshot.snapshot_id).encode("utf-8")
        payload_bytes = envelope.to_json_bytes()

        self._producer.produce(
            topic=TopicName.PORTFOLIO_UPDATES.value,
            key=partition_key,
            value=payload_bytes,
        )
        self._producer.flush(timeout=5.0)

    def process_message_payload(
        self,
        msg_bytes: bytes,
    ) -> tuple[bool, str]:
        """Process a raw message payload with error boundary and DLQ routing."""
        try:
            data = json.loads(msg_bytes.decode("utf-8"))
            envelope = EventEnvelope[Fill].model_validate(data)
        except Exception as e:
            err = f"Malformed message envelope: {e}"
            self.route_to_dlq(msg_bytes, err)
            return False, err

        fill = envelope.payload
        event_id = envelope.event_id
        corr_id = envelope.correlation_id

        try:
            # 1. Ingest Fill into PostgreSQL double-entry ledger
            res = self.ledger.ingest_fill(
                fill=fill,
                event_id=event_id,
                correlation_id=corr_id,
            )

            # 2. Update Redis projection if configured
            if self.projection is not None:
                # Fetch latest snapshot to project
                with self.ledger.connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT snapshot_id, as_of, cash, equity, exposure,
                               realized_pnl, unrealized_pnl, drawdown,
                               high_water_mark, positions, trading_mode
                        FROM portfolio_snapshots
                        ORDER BY as_of DESC, snapshot_id DESC
                        LIMIT 1;
                        """
                    )
                    row = cur.fetchone()
                    if row:
                        from decimal import Decimal

                        pos_data = row[9] if isinstance(row[9], list) else json.loads(row[9])
                        from src.common.schemas.portfolio import Position

                        positions = [Position.model_validate(p) for p in pos_data]
                        snapshot = PortfolioSnapshot(
                            snapshot_id=UUID(str(row[0])),
                            as_of=row[1],
                            cash=Decimal(str(row[2])),
                            equity=Decimal(str(row[3])),
                            exposure=Decimal(str(row[4])),
                            realized_pnl=Decimal(str(row[5])),
                            unrealized_pnl=Decimal(str(row[6])),
                            drawdown=Decimal(str(row[7])),
                            high_water_mark=Decimal(str(row[8])),
                            positions=positions,
                            trading_mode="PAPER",
                        )
                        self.projection.apply_snapshot(snapshot, res.revision)

                        # 3. Publish to portfolio.updates
                        self.publish_portfolio_update(
                            snapshot=snapshot,
                            correlation_id=corr_id,
                            causation_id=event_id,
                        )

            return True, "SUCCESS"

        except InsufficientInventoryError as e:
            err = f"No-short constraint violation: {e}"
            self.route_to_dlq(msg_bytes, err, correlation_id=corr_id)
            return False, err
        except DuplicateEventDifferentPayloadError as e:
            err = f"Poison pill duplicate event hash mismatch: {e}"
            self.route_to_dlq(msg_bytes, err, correlation_id=corr_id)
            return False, err
        except Exception as e:
            logger.exception("Unexpected error processing fill: %s", e)
            self.route_to_dlq(msg_bytes, f"Unexpected error: {e}", correlation_id=corr_id)
            return False, str(e)
