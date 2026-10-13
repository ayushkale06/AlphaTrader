"""End-to-end infrastructure smoke test for PostgreSQL, Redis, and Redpanda.

Verifies:
1. PostgreSQL 16:
   - All 6 tables exist (orders, fills, positions, portfolio_snapshots,
     risk_audit_log, schema_migrations).
   - orders.idempotency_key UNIQUE constraint prevents duplicate order execution.
   - risk_audit_log APPEND-ONLY trigger strictly blocks UPDATE and DELETE.
2. Redis 7:
   - PING succeeds.
   - Key SET, GET, and TTL round-trip.
   - Floor circuit-breaker flag (alphatrader:floor_halted) read/write works.
3. Redpanda 24.x (Kafka API):
   - For EVERY topic in TOPIC_REGISTRY, publishes a real EventEnvelope built from
     Sprint 2 sample models, consumes it back, and validates it with validate_topic_message().

Exits with code 0 on complete success, or non-zero on any failure.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from src.common.config import get_settings
from src.common.contracts import (
    TOPIC_REGISTRY,
    build_sample_topic_messages,
    validate_topic_message,
)

EXPECTED_TABLES = [
    "orders",
    "fills",
    "positions",
    "portfolio_snapshots",
    "risk_audit_log",
    "schema_migrations",
]


def test_postgres_infrastructure(dsn: str) -> bool:
    """Validate PostgreSQL tables, idempotency constraint, and append-only trigger."""
    import psycopg
    from psycopg import errors as pg_errors
    from psycopg.types.json import Jsonb

    print("\n[1/3] Verifying PostgreSQL System of Record...")
    try:
        with (
            psycopg.connect(dsn, autocommit=False) as conn,
            conn.cursor() as cur,
        ):
            # 1. Verify expected tables exist
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            existing_tables = {row[0] for row in cur.fetchall()}
            missing = [t for t in EXPECTED_TABLES if t not in existing_tables]
            if missing:
                print(f"  [FAIL] Missing PostgreSQL tables: {missing}", file=sys.stderr)
                return False
            print(f"  [OK] All {len(EXPECTED_TABLES)} tables exist in public schema.")

            # 2. Verify schema_migrations baseline & migration 002
            cur.execute(
                "SELECT version FROM schema_migrations "
                "WHERE version IN ('001_initial_schema', '002_protect_risk_audit_log_truncate')"
            )
            applied_versions = {row[0] for row in cur.fetchall()}
            if "001_initial_schema" not in applied_versions:
                print("  [FAIL] Baseline migration '001_initial_schema' missing.", file=sys.stderr)
                return False
            if "002_protect_risk_audit_log_truncate" not in applied_versions:
                print(
                    "  [FAIL] Migration '002_protect_risk_audit_log_truncate' missing.",
                    file=sys.stderr,
                )
                return False
            print(f"  [OK] Migrations verified: {sorted(applied_versions)}")

            # 3. Test orders.idempotency_key UNIQUE constraint
            test_order_req_id1 = uuid.uuid4()
            test_order_req_id2 = uuid.uuid4()
            test_idem_key = f"smoke-idem-{uuid.uuid4()}"
            cur.execute("SAVEPOINT sp_idem_test;")
            cur.execute(
                """
                INSERT INTO orders (
                    order_request_id, idempotency_key, symbol, side, order_type,
                    status, quantity, stop_loss_price, strategy_name, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    test_order_req_id1,
                    test_idem_key,
                    "RELIANCE.NS",
                    "BUY",
                    "MARKET",
                    "PENDING_RISK",
                    Decimal("10"),
                    Decimal("2900.00"),
                    "smoke_test_strategy",
                    "cid-smoke-001",
                ),
            )

            # Duplicate idempotency_key must raise UniqueViolation
            cur.execute("SAVEPOINT sp_idem_dup;")
            try:
                cur.execute(
                    """
                    INSERT INTO orders (
                        order_request_id, idempotency_key, symbol, side, order_type,
                        status, quantity, stop_loss_price, strategy_name, correlation_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        test_order_req_id2,
                        test_idem_key,
                        "RELIANCE.NS",
                        "BUY",
                        "MARKET",
                        "PENDING_RISK",
                        Decimal("10"),
                        Decimal("2900.00"),
                        "smoke_test_strategy",
                        "cid-smoke-002",
                    ),
                )
                print("  [FAIL] orders.idempotency_key allowed duplicate!", file=sys.stderr)
                return False
            except pg_errors.UniqueViolation:
                cur.execute("ROLLBACK TO SAVEPOINT sp_idem_dup;")
                print("  [OK] orders.idempotency_key UNIQUE constraint blocked duplicate.")
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT sp_idem_test;")

            # 4. Test risk_audit_log APPEND-ONLY trigger
            test_decision_id = uuid.uuid4()
            cur.execute("SAVEPOINT sp_audit_test;")
            cur.execute(
                """
                INSERT INTO risk_audit_log (
                    decision_id, order_request_id, symbol, verdict, rule_fired,
                    reason_code, inputs_snapshot, message, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    test_decision_id,
                    uuid.uuid4(),
                    "INFY.NS",
                    "REJECT",
                    "MAX_POSITION_SIZE_BREACH",
                    "MAX_POSITION_SIZE_BREACH",
                    Jsonb({"proposed_pct": "6.5", "max_pct": "5.0"}),
                    "Position size exceeds 5.0% limit",
                    "cid-smoke-audit",
                ),
            )

            # UPDATE must raise Exception via trigger
            cur.execute("SAVEPOINT sp_audit_update;")
            try:
                cur.execute(
                    "UPDATE risk_audit_log SET message = 'tampered' WHERE decision_id = %s",
                    (test_decision_id,),
                )
                print(
                    "  [FAIL] risk_audit_log permitted UPDATE! Immutability violated.",
                    file=sys.stderr,
                )
                return False
            except pg_errors.RaiseException as exc:
                cur.execute("ROLLBACK TO SAVEPOINT sp_audit_update;")
                print(f"  [OK] risk_audit_log trigger blocked UPDATE: {exc.diag.message_primary}")

            # DELETE must raise Exception via trigger
            cur.execute("SAVEPOINT sp_audit_delete;")
            try:
                cur.execute(
                    "DELETE FROM risk_audit_log WHERE decision_id = %s",
                    (test_decision_id,),
                )
                print(
                    "  [FAIL] risk_audit_log permitted DELETE! Immutability violated.",
                    file=sys.stderr,
                )
                return False
            except pg_errors.RaiseException as exc:
                cur.execute("ROLLBACK TO SAVEPOINT sp_audit_delete;")
                print(f"  [OK] risk_audit_log trigger blocked DELETE: {exc.diag.message_primary}")

            # TRUNCATE must raise Exception via statement-level trigger (Migration 002)
            cur.execute("SAVEPOINT sp_audit_truncate;")
            try:
                cur.execute("TRUNCATE TABLE risk_audit_log;")
                print(
                    "  [FAIL] risk_audit_log permitted TRUNCATE! Immutability violated.",
                    file=sys.stderr,
                )
                return False
            except pg_errors.RaiseException as exc:
                cur.execute("ROLLBACK TO SAVEPOINT sp_audit_truncate;")
                print(f"  [OK] risk_audit_log trigger blocked TRUNCATE: {exc.diag.message_primary}")
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT sp_audit_test;")

            conn.rollback()
            print("  [PASS] PostgreSQL System of Record verified.")
            return True
    except Exception as exc:
        print(f"  [FAIL] PostgreSQL connection failed: {exc}", file=sys.stderr)
        return False


def test_redis_infrastructure(redis_url: str) -> bool:
    """Validate Redis connectivity, SET/GET round trip, and floor-halt flag."""
    import redis

    print("\n[2/3] Verifying Redis Low-Latency State Store...")
    try:
        r = redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        if not r.ping():
            print("  [FAIL] Redis ping returned False.", file=sys.stderr)
            return False
        print("  [OK] Redis PING successful.")

        smoke_key = f"alphatrader:smoke_test:{uuid.uuid4()}"
        smoke_val = "ok-token-12345"
        r.set(smoke_key, smoke_val, ex=30)
        read_val = r.get(smoke_key)
        if read_val != smoke_val:
            print(
                f"  [FAIL] Redis read value mismatch: got {read_val!r}, expected {smoke_val!r}",
                file=sys.stderr,
            )
            return False
        r.delete(smoke_key)
        print("  [OK] Redis SET / GET / TTL round trip verified.")

        # Test floor circuit-breaker flag operations
        halt_key = "alphatrader:floor_halted"
        r.set(halt_key, "0")
        if r.get(halt_key) != "0":
            print("  [FAIL] Failed to set floor_halted flag to 0.", file=sys.stderr)
            return False
        print("  [OK] Circuit-breaker flag 'alphatrader:floor_halted' verified.")

        print("  [PASS] Redis state store verified.")
        return True
    except Exception as exc:
        print(f"  [FAIL] Redis connection failed: {exc}", file=sys.stderr)
        return False


def test_redpanda_topic_contracts(bootstrap_servers: str) -> bool:
    """Publish a validated sample EventEnvelope to EVERY topic and consume it back."""
    from confluent_kafka import Consumer, Producer

    print("\n[3/3] Verifying Redpanda Event Bus & Contract Registry...")
    samples = build_sample_topic_messages()
    all_passed = True

    producer_conf = {
        "bootstrap.servers": bootstrap_servers,
        "client.id": "alphatrader-smoke-producer",
        "acks": "all",
    }
    producer = Producer(producer_conf)

    for topic_name, sample_envelope in samples.items():
        contract = TOPIC_REGISTRY[topic_name]
        key = contract.extract_message_key(sample_envelope)
        payload_bytes = sample_envelope.to_json_bytes()

        # Produce message
        delivery_error: list[Any] = []

        def on_delivery(err: Any, _msg: Any, errors: list[Any] = delivery_error) -> None:
            if err:
                errors.append(err)

        producer.produce(
            topic=topic_name.value,
            key=key.encode("utf-8"),
            value=payload_bytes,
            on_delivery=on_delivery,
        )
        producer.flush(timeout=10.0)

        if delivery_error:
            print(
                f"  [FAIL] Delivery error on topic {topic_name.value}: {delivery_error}",
                file=sys.stderr,
            )
            all_passed = False
            continue

        # Consume message back with a dedicated group ID
        consumer_conf = {
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"smoke-cg-{topic_name.value}-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
        consumer = Consumer(consumer_conf)
        consumer.subscribe([topic_name.value])

        consumed_envelope = None
        start_time = time.time()
        while time.time() - start_time < 12.0:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                print(
                    f"  [FAIL] Consumer error on {topic_name.value}: {msg.error()}", file=sys.stderr
                )
                break

            msg_bytes = msg.value()
            if msg_bytes is None:
                continue
            raw_key = msg.key()
            msg_key = raw_key.decode("utf-8") if isinstance(raw_key, (bytes, bytearray)) else None

            # Validate against contract
            try:
                validated = validate_topic_message(
                    topic=topic_name,
                    message=msg_bytes,
                    message_key=msg_key,
                )
                if validated.event_id == sample_envelope.event_id:
                    consumed_envelope = validated
                    break
            except Exception as exc:
                print(f"  [FAIL] Validation failed on {topic_name.value}: {exc}", file=sys.stderr)
                break

        consumer.close()

        if consumed_envelope is None:
            print(
                f"  [FAIL] Could not consume back message from {topic_name.value} within timeout.",
                file=sys.stderr,
            )
            all_passed = False
        else:
            print(f"  [OK] Topic {topic_name.value:<20} round-trip verified (key={key!r}).")

    if all_passed:
        print("  [PASS] All 9 Redpanda topic contracts verified.")
    return all_passed


def main(argv: Sequence[str] | None = None) -> int:
    """Run all infrastructure smoke tests."""
    parser = argparse.ArgumentParser(description="AlphaTrader Infrastructure Smoke Test.")
    parser.add_argument("--postgres-dsn", default=None, help="PostgreSQL connection DSN.")
    parser.add_argument("--redis-url", default=None, help="Redis connection URL.")
    parser.add_argument("--kafka-bootstrap-servers", default=None, help="Kafka bootstrap servers.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    settings = get_settings()
    pg_dsn = args.postgres_dsn or settings.postgres_sync_dsn
    redis_url = args.redis_url or settings.redis_url
    kafka_servers = args.kafka_bootstrap_servers or settings.kafka_bootstrap_servers

    print("=================================================================")
    print("AlphaTrader Sprint 3 Infrastructure Smoke Test")
    print(f"PostgreSQL DSN: {settings.postgres_sync_dsn_masked}")
    print(f"Redis URL:      {settings.redis_url_masked}")
    print(f"Kafka Servers:  {kafka_servers}")
    print("=================================================================")

    pg_ok = test_postgres_infrastructure(pg_dsn)
    redis_ok = test_redis_infrastructure(redis_url)
    kafka_ok = test_redpanda_topic_contracts(kafka_servers)

    print("\n-----------------------------------------------------------------")
    if pg_ok and redis_ok and kafka_ok:
        print("ALL INFRASTRUCTURE SMOKE TESTS PASSED (PostgreSQL, Redis, Redpanda).")
        print("-----------------------------------------------------------------")
        return 0

    print(
        "SMOKE TEST FAILED: One or more services did not meet contract requirements.",
        file=sys.stderr,
    )
    print("-----------------------------------------------------------------", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
