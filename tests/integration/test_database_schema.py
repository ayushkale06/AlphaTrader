"""Integration tests for PostgreSQL double-entry ledger schema, constraints, and triggers."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

pytestmark = pytest.mark.integration


def test_required_tables_exist(pg_connection: Any) -> None:
    """Verify that all 6 required Sprint 3 tables exist in the public schema."""
    expected_tables = {
        "orders",
        "fills",
        "positions",
        "portfolio_snapshots",
        "risk_audit_log",
        "schema_migrations",
    }
    with pg_connection.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        existing = {row[0] for row in cur.fetchall()}
    assert expected_tables.issubset(existing), f"Missing tables: {expected_tables - existing}"


def test_schema_migrations_has_baseline(pg_connection: Any) -> None:
    """Verify baseline migration 001_initial_schema is registered."""
    with pg_connection.cursor() as cur:
        cur.execute(
            "SELECT version, description FROM schema_migrations "
            "WHERE version = '001_initial_schema'"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "001_initial_schema"


def test_orders_idempotency_key_uniqueness(pg_connection: Any) -> None:
    """Verify orders.idempotency_key rejects duplicate submissions."""
    from psycopg import errors

    idem_key = f"idem-test-{uuid.uuid4()}"
    with pg_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders (
                order_request_id, idempotency_key, symbol, side, order_type,
                status, quantity, stop_loss_price, strategy_name, correlation_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid.uuid4(),
                idem_key,
                "TCS.NS",
                "BUY",
                "MARKET",
                "PENDING_RISK",
                Decimal("25"),
                Decimal("3900.00"),
                "vwap_momentum",
                "cid-001",
            ),
        )
        pg_connection.commit()

        with pytest.raises(errors.UniqueViolation):
            cur.execute(
                """
                INSERT INTO orders (
                    order_request_id, idempotency_key, symbol, side, order_type,
                    status, quantity, stop_loss_price, strategy_name, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    idem_key,
                    "TCS.NS",
                    "BUY",
                    "MARKET",
                    "PENDING_RISK",
                    Decimal("25"),
                    Decimal("3900.00"),
                    "vwap_momentum",
                    "cid-002",
                ),
            )
            pg_connection.commit()
    pg_connection.rollback()


def test_orders_check_constraints_reject_invalid_rows(pg_connection: Any) -> None:
    """Verify check constraints on orders reject invalid side, status, and price."""
    from psycopg import errors

    with pg_connection.cursor() as cur:
        # Invalid side
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO orders (
                    order_request_id, idempotency_key, symbol, side, order_type,
                    status, quantity, stop_loss_price, strategy_name, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    f"id-{uuid.uuid4()}",
                    "TCS.NS",
                    "HOLD",
                    "MARKET",
                    "PENDING_RISK",
                    Decimal("10"),
                    Decimal("3900"),
                    "strat",
                    "cid",
                ),
            )
        pg_connection.rollback()

        # Non-positive quantity
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO orders (
                    order_request_id, idempotency_key, symbol, side, order_type,
                    status, quantity, stop_loss_price, strategy_name, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    f"id-{uuid.uuid4()}",
                    "TCS.NS",
                    "BUY",
                    "MARKET",
                    "PENDING_RISK",
                    Decimal("0"),
                    Decimal("3900"),
                    "strat",
                    "cid",
                ),
            )
        pg_connection.rollback()

        # LIMIT order requires limit_price
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO orders (
                    order_request_id, idempotency_key, symbol, side, order_type,
                    status, quantity, limit_price, stop_loss_price, strategy_name, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    f"id-{uuid.uuid4()}",
                    "TCS.NS",
                    "BUY",
                    "LIMIT",
                    "PENDING_RISK",
                    Decimal("10"),
                    None,
                    Decimal("3900"),
                    "strat",
                    "cid",
                ),
            )
        pg_connection.rollback()


def test_risk_audit_log_immutability_trigger(pg_connection: Any) -> None:
    """Verify that UPDATE and DELETE on risk_audit_log are blocked by trigger."""
    import psycopg
    from psycopg import errors

    decision_id = uuid.uuid4()
    with pg_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_audit_log (
                decision_id, order_request_id, symbol, verdict, rule_fired,
                reason_code, inputs_snapshot, message, correlation_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                uuid.uuid4(),
                "HDFCBANK.NS",
                "APPROVE",
                "ALL_INVARIANTS_PASSED",
                "WITHIN_LIMITS",
                psycopg.types.json.Jsonb({"proposed_pct": "3.2"}),
                "Approved",
                "cid-audit-test",
            ),
        )
        pg_connection.commit()

        # Attempt UPDATE -> must raise RaiseException
        with pytest.raises(errors.RaiseException, match="append-only"):
            cur.execute(
                "UPDATE risk_audit_log SET verdict = 'REJECT' WHERE decision_id = %s",
                (decision_id,),
            )
        pg_connection.rollback()

        # Attempt DELETE -> must raise RaiseException
        with pytest.raises(errors.RaiseException, match="append-only"):
            cur.execute("DELETE FROM risk_audit_log WHERE decision_id = %s", (decision_id,))
        pg_connection.rollback()


def test_fills_total_fees_sum_constraint(pg_connection: Any) -> None:
    """Verify fills rejects rows where total_fees != sum of component fees."""
    from psycopg import errors

    order_id = uuid.uuid4()
    with pg_connection.cursor() as cur:
        # Create parent order
        cur.execute(
            """
            INSERT INTO orders (
                order_id, order_request_id, idempotency_key, symbol, side, order_type,
                status, quantity, stop_loss_price, strategy_name, correlation_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                uuid.uuid4(),
                f"id-{uuid.uuid4()}",
                "INFY.NS",
                "BUY",
                "MARKET",
                "FILLED",
                Decimal("10"),
                Decimal("1800"),
                "strat",
                "cid",
            ),
        )
        pg_connection.commit()

        # Insert fill with incorrect total_fees
        with pytest.raises(errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO fills (
                    fill_id, order_id, order_request_id, symbol, side, fill_price,
                    fill_quantity, stt_tax, exchange_fees, gst_tax, stamp_duty, total_fees,
                    correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4(),
                    order_id,
                    uuid.uuid4(),
                    "INFY.NS",
                    "BUY",
                    Decimal("1850.00"),
                    Decimal("10"),
                    Decimal("10.00"),
                    Decimal("2.00"),
                    Decimal("1.00"),
                    Decimal("1.00"),
                    Decimal("99.00"),  # Mismatch: 10 + 2 + 1 + 1 != 99
                    "cid-fill-test",
                ),
            )
        pg_connection.rollback()
