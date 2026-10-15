"""Core PostgreSQL Double-Entry Portfolio Ledger engine.

Maintains the authoritative system-of-record double-entry ledger in PostgreSQL.
Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from src.common.schemas.orders import Fill, Side
from src.common.schemas.portfolio import PortfolioSnapshot, Position
from src.mcp_servers.portfolio_ledger.accounts import (
    ACCOUNT_CASH,
    ACCOUNT_EXPENSE_EXCHANGE,
    ACCOUNT_EXPENSE_GST,
    ACCOUNT_EXPENSE_STAMP,
    ACCOUNT_EXPENSE_STT,
    ACCOUNT_INITIAL_CAPITAL,
    ACCOUNT_REALIZED_PNL,
    DEFAULT_SYSTEM_ACCOUNTS,
    PostingDirection,
    inventory_account_code,
    inventory_account_name,
)

logger = logging.getLogger("alphatrader.portfolio_ledger.ledger")

# Canonical Genesis Event ID (RFC 4122 Nil/Deterministic Constant)
GENESIS_EVENT_ID = UUID("00000000-0000-0000-0000-000000000001")
MONEY_QUANTIZE = Decimal("0.0001")
MUTEX_ADVISORY_LOCK_ID = 74218999


class LedgerError(Exception):
    """Base exception for ledger operations."""


class InsufficientInventoryError(LedgerError):
    """Raised when a sell fill exceeds existing inventory (no-short invariant)."""


class DuplicateEventDifferentPayloadError(LedgerError):
    """Raised when an event ID is re-ingested with differing payload hash (poison pill)."""


@dataclass(frozen=True)
class IngestionResult:
    """Result of an event ingestion into the ledger."""

    event_id: UUID
    is_duplicate: bool
    revision: int
    entry_id: UUID | None = None


class DoubleEntryLedger:
    """PostgreSQL-backed double-entry portfolio ledger."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self) -> psycopg.Connection[Any]:
        """Create and return a new psycopg connection."""
        return psycopg.connect(self.dsn, autocommit=False)

    def compute_payload_hash(self, payload: dict[str, Any] | str | bytes) -> str:
        """Compute SHA-256 of canonical JSON payload."""
        if isinstance(payload, bytes):
            return hashlib.sha256(payload).hexdigest()
        if isinstance(payload, str):
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def initialize_chart_of_accounts(self, conn: psycopg.Connection[Any]) -> None:
        """Ensure standard chart of accounts exists."""
        with conn.cursor() as cur:
            for acc in DEFAULT_SYSTEM_ACCOUNTS:
                cur.execute(
                    """
                    INSERT INTO ledger_accounts (
                        account_code, account_name, account_type, currency, is_active
                    ) VALUES (%s, %s, %s, 'INR', TRUE)
                    ON CONFLICT (account_code) DO NOTHING;
                    """,
                    (acc["account_code"], acc["account_name"], acc["account_type"]),
                )

    def get_or_create_account(
        self,
        conn: psycopg.Connection[Any],
        account_code: str,
        account_name: str,
        account_type: str,
    ) -> UUID:
        """Fetch or create a ledger account by account code."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account_id FROM ledger_accounts WHERE account_code = %s;",
                (account_code,),
            )
            row = cur.fetchone()
            if row:
                return UUID(str(row[0]))

            cur.execute(
                """
                INSERT INTO ledger_accounts (
                    account_code, account_name, account_type, currency, is_active
                ) VALUES (%s, %s, %s, 'INR', TRUE)
                ON CONFLICT (account_code) DO UPDATE SET is_active = TRUE
                RETURNING account_id;
                """,
                (account_code, account_name, account_type),
            )
            created_row = cur.fetchone()
            if created_row is None:
                msg = f"Failed to retrieve or create account: {account_code}"
                raise LedgerError(msg)
            return UUID(str(created_row[0]))

    def initialize_genesis_funding(
        self,
        seed_capital_inr: Decimal = Decimal("1000000.0000"),
        correlation_id: str = "GENESIS-INITIAL-FUNDING",
    ) -> IngestionResult:
        """Execute one-time auditable genesis funding if not already funded."""
        payload = {
            "action": "GENESIS_FUNDING",
            "amount_inr": str(seed_capital_inr.quantize(MONEY_QUANTIZE)),
            "trading_mode": "PAPER",
        }
        payload_hash = self.compute_payload_hash(payload)

        with self.connect() as conn:
            self.initialize_chart_of_accounts(conn)

            # Check if genesis funding already executed
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload_hash FROM processed_events WHERE event_id = %s;",
                    (GENESIS_EVENT_ID,),
                )
                existing = cur.fetchone()
                if existing:
                    existing_hash = existing[0]
                    if existing_hash != payload_hash:
                        logger.warning(
                            "Genesis funding config drift detected. "
                            "Existing: %s, Current: %s. Preserving existing.",
                            existing_hash,
                            payload_hash,
                        )
                    # Fetch current revision
                    cur.execute(
                        "SELECT current_revision FROM portfolio_revision_tracker "
                        "WHERE tracker_id = 1;"
                    )
                    rev_row = cur.fetchone()
                    rev = rev_row[0] if rev_row else 0
                    conn.commit()
                    return IngestionResult(
                        event_id=GENESIS_EVENT_ID, is_duplicate=True, revision=rev
                    )

                # Acquire advisory lock to serialize revision increment
                cur.execute("SELECT pg_advisory_xact_lock(%s);", (MUTEX_ADVISORY_LOCK_ID,))

                # Insert processed event
                cur.execute(
                    """
                    INSERT INTO processed_events (event_id, topic, correlation_id, payload_hash)
                    VALUES (%s, 'portfolio.genesis', %s, %s);
                    """,
                    (GENESIS_EVENT_ID, correlation_id, payload_hash),
                )

                # Fetch accounts
                cash_acc_id = self.get_or_create_account(conn, ACCOUNT_CASH, "Asset:Cash", "ASSET")
                capital_acc_id = self.get_or_create_account(
                    conn, ACCOUNT_INITIAL_CAPITAL, "Equity:InitialCapital", "EQUITY"
                )

                # Create journal entry header
                cur.execute(
                    """
                    INSERT INTO journal_entries (event_id, narration, correlation_id)
                    VALUES (%s, %s, %s)
                    RETURNING entry_id;
                    """,
                    (
                        GENESIS_EVENT_ID,
                        f"Paper Genesis Initial Funding: INR {seed_capital_inr}",
                        correlation_id,
                    ),
                )
                entry_row = cur.fetchone()
                if entry_row is None:
                    msg = "Failed to create journal entry header."
                    raise LedgerError(msg)
                entry_id = UUID(str(entry_row[0]))

                # Postings: DEBIT Cash, CREDIT Capital
                cur.execute(
                    """
                    INSERT INTO journal_postings (
                        entry_id, account_id, direction, amount, sequence_num
                    ) VALUES
                        (%s, %s, 'DEBIT', %s, 1),
                        (%s, %s, 'CREDIT', %s, 2);
                    """,
                    (
                        entry_id,
                        cash_acc_id,
                        seed_capital_inr,
                        entry_id,
                        capital_acc_id,
                        seed_capital_inr,
                    ),
                )

                # Increment portfolio revision
                cur.execute(
                    """
                    UPDATE portfolio_revision_tracker
                    SET current_revision = current_revision + 1, updated_at = NOW()
                    WHERE tracker_id = 1
                    RETURNING current_revision;
                    """
                )
                rev_row = cur.fetchone()
                if rev_row is None:
                    msg = "Failed to increment portfolio revision."
                    raise LedgerError(msg)
                new_rev = int(rev_row[0])

                # Update portfolio_snapshots baseline
                cur.execute(
                    """
                    INSERT INTO portfolio_snapshots (
                        cash, equity, exposure, realized_pnl, unrealized_pnl,
                        drawdown, high_water_mark, positions, correlation_id, trading_mode
                    ) VALUES (%s, %s, 0, 0, 0, 0, %s, '[]'::jsonb, %s, 'PAPER');
                    """,
                    (seed_capital_inr, seed_capital_inr, seed_capital_inr, correlation_id),
                )

                conn.commit()
                logger.info("Genesis funding successfully committed. Revision: %d", new_rev)
                return IngestionResult(
                    event_id=GENESIS_EVENT_ID,
                    is_duplicate=False,
                    revision=new_rev,
                    entry_id=entry_id,
                )

    def ingest_fill(
        self,
        fill: Fill,
        event_id: UUID,
        correlation_id: str,
        topic: str = "orders.fills",
    ) -> IngestionResult:
        """Ingest a paper trade Fill event with double-entry journal postings."""
        payload_hash = self.compute_payload_hash(fill.model_dump(mode="json"))

        with self.connect() as conn:
            self.initialize_chart_of_accounts(conn)

            with conn.cursor() as cur:
                # 1. Idempotency Check
                cur.execute(
                    "SELECT payload_hash FROM processed_events WHERE event_id = %s;",
                    (event_id,),
                )
                existing = cur.fetchone()
                if existing:
                    existing_hash = existing[0]
                    if existing_hash != payload_hash:
                        msg = (
                            f"Idempotency collision with differing payload hash for "
                            f"event {event_id}. Stored: {existing_hash}, Incoming: {payload_hash}"
                        )
                        logger.error(msg)
                        raise DuplicateEventDifferentPayloadError(msg)

                    cur.execute(
                        "SELECT current_revision FROM portfolio_revision_tracker "
                        "WHERE tracker_id = 1;"
                    )
                    rev_track = cur.fetchone()
                    rev = int(rev_track[0]) if rev_track else 0
                    conn.commit()
                    return IngestionResult(event_id=event_id, is_duplicate=True, revision=rev)

                # 2. Acquire transaction-scoped mutex advisory lock
                cur.execute("SELECT pg_advisory_xact_lock(%s);", (MUTEX_ADVISORY_LOCK_ID,))

                # 3. Insert processed event record
                cur.execute(
                    """
                    INSERT INTO processed_events (event_id, topic, correlation_id, payload_hash)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (event_id, topic, correlation_id, payload_hash),
                )

                # 4. Fetch or create accounts
                cash_id = self.get_or_create_account(conn, ACCOUNT_CASH, "Asset:Cash", "ASSET")
                inv_code = inventory_account_code(fill.symbol)
                inv_id = self.get_or_create_account(
                    conn, inv_code, inventory_account_name(fill.symbol), "ASSET"
                )
                realized_pnl_id = self.get_or_create_account(
                    conn, ACCOUNT_REALIZED_PNL, "Revenue:RealizedPnL", "REVENUE"
                )
                stt_id = self.get_or_create_account(
                    conn, ACCOUNT_EXPENSE_STT, "Expense:STT", "EXPENSE"
                )
                exch_id = self.get_or_create_account(
                    conn, ACCOUNT_EXPENSE_EXCHANGE, "Expense:ExchangeCharges", "EXPENSE"
                )
                gst_id = self.get_or_create_account(
                    conn, ACCOUNT_EXPENSE_GST, "Expense:GST", "EXPENSE"
                )
                stamp_id = self.get_or_create_account(
                    conn, ACCOUNT_EXPENSE_STAMP, "Expense:StampDuty", "EXPENSE"
                )

                # Fetch current position from positions table
                cur.execute(
                    """
                    SELECT quantity, average_entry_price, mark_price, market_value,
                           unrealized_pnl, realized_pnl
                    FROM positions WHERE symbol = %s FOR UPDATE;
                    """,
                    (fill.symbol,),
                )
                pos_row = cur.fetchone()
                current_qty = Decimal(str(pos_row[0])) if pos_row else Decimal("0")
                current_realized_pnl = Decimal(str(pos_row[5])) if pos_row else Decimal("0")

                # Calculate authoritative current inventory book cost from ledger postings
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE -amount END), 0)
                    FROM journal_postings
                    WHERE account_id = %s;
                    """,
                    (inv_id,),
                )
                inv_cost_row = cur.fetchone()
                inv_book_cost = Decimal(str(inv_cost_row[0])) if inv_cost_row else Decimal("0")

                # 5. Process Fill based on Side (BUY vs SELL)
                gross_amount = (fill.fill_quantity * fill.fill_price).quantize(
                    MONEY_QUANTIZE, ROUND_HALF_UP
                )
                total_fees = fill.total_fees.quantize(MONEY_QUANTIZE, ROUND_HALF_UP)

                # Create journal entry header
                narration = (
                    f"Fill {fill.side.value} {fill.fill_quantity} {fill.symbol} @ {fill.fill_price}"
                )
                cur.execute(
                    """
                    INSERT INTO journal_entries (event_id, fill_id, narration, correlation_id)
                    VALUES (%s, %s, %s, %s)
                    RETURNING entry_id;
                    """,
                    (
                        event_id,
                        fill.fill_id,
                        narration,
                        correlation_id,
                    ),
                )
                entry_header_row = cur.fetchone()
                if entry_header_row is None:
                    msg = f"Failed to insert journal entry for fill {fill.fill_id}."
                    raise LedgerError(msg)
                entry_id = UUID(str(entry_header_row[0]))

                postings: list[tuple[UUID, str, Decimal, int]] = []
                seq = 1

                if fill.side == Side.BUY:
                    new_qty = current_qty + fill.fill_quantity
                    new_book_cost = inv_book_cost + gross_amount
                    new_avg_entry = (new_book_cost / new_qty).quantize(
                        MONEY_QUANTIZE, ROUND_HALF_UP
                    )

                    # Postings:
                    # 1. DEBIT Inventory: gross_amount
                    postings.append((inv_id, PostingDirection.DEBIT.value, gross_amount, seq))
                    seq += 1

                    # 2. Fee Expenses (DEBIT)
                    if fill.stt_tax > 0:
                        postings.append(
                            (
                                stt_id,
                                PostingDirection.DEBIT.value,
                                fill.stt_tax.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.exchange_fees > 0:
                        postings.append(
                            (
                                exch_id,
                                PostingDirection.DEBIT.value,
                                fill.exchange_fees.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.gst_tax > 0:
                        postings.append(
                            (
                                gst_id,
                                PostingDirection.DEBIT.value,
                                fill.gst_tax.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.stamp_duty > 0:
                        postings.append(
                            (
                                stamp_id,
                                PostingDirection.DEBIT.value,
                                fill.stamp_duty.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1

                    # 3. CREDIT Cash: gross_amount + total_fees
                    cash_credit = gross_amount + total_fees
                    postings.append((cash_id, PostingDirection.CREDIT.value, cash_credit, seq))

                    # Update positions table
                    mkt_val = (new_qty * fill.fill_price).quantize(MONEY_QUANTIZE, ROUND_HALF_UP)
                    unrealized = mkt_val - new_book_cost
                    cur.execute(
                        """
                        INSERT INTO positions (
                            symbol, quantity, average_entry_price, mark_price,
                            market_value, unrealized_pnl, realized_pnl, trading_mode, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'PAPER', NOW())
                        ON CONFLICT (symbol) DO UPDATE SET
                            quantity = EXCLUDED.quantity,
                            average_entry_price = EXCLUDED.average_entry_price,
                            mark_price = EXCLUDED.mark_price,
                            market_value = EXCLUDED.market_value,
                            unrealized_pnl = EXCLUDED.unrealized_pnl,
                            updated_at = NOW();
                        """,
                        (
                            fill.symbol,
                            new_qty,
                            new_avg_entry,
                            fill.fill_price,
                            mkt_val,
                            unrealized,
                            current_realized_pnl,
                        ),
                    )

                else:  # Side.SELL
                    # Invariant Check: No-short policy
                    if current_qty <= Decimal("0") or fill.fill_quantity > current_qty:
                        msg = (
                            f"Insufficient inventory to sell {fill.fill_quantity} {fill.symbol}. "
                            f"Current inventory: {current_qty}."
                        )
                        logger.error(msg)
                        conn.rollback()
                        raise InsufficientInventoryError(msg)

                    new_qty = current_qty - fill.fill_quantity
                    if new_qty == Decimal("0"):
                        # Full Liquidation: Credit EXACT remaining inventory book balance
                        inv_cost_basis = inv_book_cost
                        new_avg_entry = Decimal("0.0001")  # placeholder for flat position or delete
                    else:
                        # Partial Sell: Proportional cost allocation rounded ROUND_HALF_UP
                        inv_cost_basis = (
                            inv_book_cost * (fill.fill_quantity / current_qty)
                        ).quantize(MONEY_QUANTIZE, ROUND_HALF_UP)
                        remaining_book_cost = inv_book_cost - inv_cost_basis
                        new_avg_entry = (remaining_book_cost / new_qty).quantize(
                            MONEY_QUANTIZE, ROUND_HALF_UP
                        )

                    trade_realized_pnl = gross_amount - inv_cost_basis

                    # Postings:
                    # 1. DEBIT Cash: Net Proceeds = gross_amount - total_fees
                    net_cash_debit = gross_amount - total_fees
                    postings.append((cash_id, PostingDirection.DEBIT.value, net_cash_debit, seq))
                    seq += 1

                    # 2. Fee Expenses (DEBIT)
                    if fill.stt_tax > 0:
                        postings.append(
                            (
                                stt_id,
                                PostingDirection.DEBIT.value,
                                fill.stt_tax.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.exchange_fees > 0:
                        postings.append(
                            (
                                exch_id,
                                PostingDirection.DEBIT.value,
                                fill.exchange_fees.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.gst_tax > 0:
                        postings.append(
                            (
                                gst_id,
                                PostingDirection.DEBIT.value,
                                fill.gst_tax.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1
                    if fill.stamp_duty > 0:
                        postings.append(
                            (
                                stamp_id,
                                PostingDirection.DEBIT.value,
                                fill.stamp_duty.quantize(MONEY_QUANTIZE, ROUND_HALF_UP),
                                seq,
                            )
                        )
                        seq += 1

                    # 3. Realized PnL (DEBIT if loss, CREDIT if gain)
                    if trade_realized_pnl > Decimal("0"):
                        # Gain -> CREDIT Revenue:RealizedPnL
                        postings.append(
                            (
                                realized_pnl_id,
                                PostingDirection.CREDIT.value,
                                trade_realized_pnl,
                                seq,
                            )
                        )
                        seq += 1
                    elif trade_realized_pnl < Decimal("0"):
                        # Loss -> DEBIT Revenue:RealizedPnL
                        postings.append(
                            (
                                realized_pnl_id,
                                PostingDirection.DEBIT.value,
                                abs(trade_realized_pnl),
                                seq,
                            )
                        )
                        seq += 1

                    # 4. CREDIT Inventory: inv_cost_basis
                    postings.append((inv_id, PostingDirection.CREDIT.value, inv_cost_basis, seq))

                    # Update positions table
                    total_realized_pnl = current_realized_pnl + trade_realized_pnl
                    if new_qty == Decimal("0"):
                        # Position flat: delete or set to 0
                        cur.execute("DELETE FROM positions WHERE symbol = %s;", (fill.symbol,))
                    else:
                        mkt_val = (new_qty * fill.fill_price).quantize(
                            MONEY_QUANTIZE, ROUND_HALF_UP
                        )
                        rem_cost = inv_book_cost - inv_cost_basis
                        unrealized = mkt_val - rem_cost
                        cur.execute(
                            """
                            UPDATE positions SET
                                quantity = %s,
                                average_entry_price = %s,
                                mark_price = %s,
                                market_value = %s,
                                unrealized_pnl = %s,
                                realized_pnl = %s,
                                updated_at = NOW()
                            WHERE symbol = %s;
                            """,
                            (
                                new_qty,
                                new_avg_entry,
                                fill.fill_price,
                                mkt_val,
                                unrealized,
                                total_realized_pnl,
                                fill.symbol,
                            ),
                        )

                # Insert postings into journal_postings
                for acc_id, direction, amt, s_num in postings:
                    cur.execute(
                        """
                        INSERT INTO journal_postings (
                            entry_id, account_id, direction, amount, sequence_num
                        ) VALUES (%s, %s, %s, %s, %s);
                        """,
                        (entry_id, acc_id, direction, amt, s_num),
                    )

                # Increment revision
                cur.execute(
                    """
                    UPDATE portfolio_revision_tracker
                    SET current_revision = current_revision + 1, updated_at = NOW()
                    WHERE tracker_id = 1
                    RETURNING current_revision;
                    """
                )
                rev_row_2 = cur.fetchone()
                if rev_row_2 is None:
                    msg = "Failed to update revision tracker."
                    raise LedgerError(msg)
                new_rev = int(rev_row_2[0])

                # Update portfolio_snapshots
                self._compute_and_record_snapshot(conn, correlation_id)

                conn.commit()
                logger.info(
                    "Fill %s ingested successfully. Entry ID: %s, Revision: %d",
                    fill.fill_id,
                    entry_id,
                    new_rev,
                )
                return IngestionResult(
                    event_id=event_id, is_duplicate=False, revision=new_rev, entry_id=entry_id
                )

    def _compute_and_record_snapshot(
        self,
        conn: psycopg.Connection[Any],
        correlation_id: str,
    ) -> PortfolioSnapshot:
        """Compute point-in-time snapshot and persist to portfolio_snapshots."""
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Authoritative Cash from Cash Account
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE -amount END), 0)
                    AS cash
                FROM journal_postings p
                JOIN ledger_accounts a ON p.account_id = a.account_id
                WHERE a.account_code = %s;
                """,
                (ACCOUNT_CASH,),
            )
            cash_row = cur.fetchone()
            cash = Decimal(str(cash_row["cash"])) if cash_row else Decimal("0")

            # 2. Open Positions
            cur.execute(
                """
                SELECT symbol, quantity, average_entry_price, mark_price, market_value,
                       unrealized_pnl, realized_pnl, updated_at
                FROM positions
                WHERE quantity > 0
                ORDER BY symbol;
                """
            )
            pos_rows = cur.fetchall()
            positions_list: list[Position] = []
            exposure = Decimal("0")
            unrealized_pnl = Decimal("0")
            realized_pnl = Decimal("0")

            for r in pos_rows:
                pos = Position(
                    symbol=r["symbol"],
                    quantity=Decimal(str(r["quantity"])),
                    average_entry_price=Decimal(str(r["average_entry_price"])),
                    mark_price=Decimal(str(r["mark_price"])),
                    market_value=Decimal(str(r["market_value"])),
                    unrealized_pnl=Decimal(str(r["unrealized_pnl"])),
                    realized_pnl=Decimal(str(r["realized_pnl"])),
                    updated_at=r["updated_at"],
                )
                positions_list.append(pos)
                exposure += pos.market_value
                unrealized_pnl += pos.unrealized_pnl
                realized_pnl += pos.realized_pnl

            equity = cash + exposure

            # 3. High-Water Mark and Drawdown
            cur.execute(
                "SELECT COALESCE(MAX(high_water_mark), %s) as max_hwm FROM portfolio_snapshots;",
                (equity,),
            )
            hwm_row = cur.fetchone()
            hwm_val = Decimal(str(hwm_row["max_hwm"])) if hwm_row else equity
            high_water_mark = max(hwm_val, equity)

            drawdown = Decimal("0")
            if high_water_mark > Decimal("0") and equity < high_water_mark:
                drawdown = ((high_water_mark - equity) / high_water_mark * Decimal("100")).quantize(
                    Decimal("0.0001"), ROUND_HALF_UP
                )

            # Insert snapshot
            pos_json = json.dumps([p.model_dump(mode="json") for p in positions_list])
            cur.execute(
                """
                INSERT INTO portfolio_snapshots (
                    cash, equity, exposure, realized_pnl, unrealized_pnl,
                    drawdown, high_water_mark, positions, correlation_id, trading_mode
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'PAPER')
                RETURNING snapshot_id, as_of;
                """,
                (
                    cash,
                    equity,
                    exposure,
                    realized_pnl,
                    unrealized_pnl,
                    drawdown,
                    high_water_mark,
                    pos_json,
                    correlation_id,
                ),
            )
            res = cur.fetchone()
            if res is None:
                msg = "Failed to insert and retrieve portfolio snapshot."
                raise LedgerError(msg)
            return PortfolioSnapshot(
                snapshot_id=UUID(str(res["snapshot_id"])),
                as_of=res["as_of"],
                cash=cash,
                equity=equity,
                exposure=exposure,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                drawdown=drawdown,
                high_water_mark=high_water_mark,
                positions=positions_list,
                trading_mode="PAPER",
            )
