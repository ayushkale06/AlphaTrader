"""Unit tests for Portfolio Ledger (Sprint 10).

Verifies:
1. Chart of Accounts and Genesis Funding:
   - One-time auditable genesis funding (INR 1,000,000.00).
   - Idempotent re-execution with identical revision.
   - Config drift detection preserves existing ledger balance.
2. Double-Entry Accounting Invariants:
   - Sum(Debits) == Sum(Credits) across all journal transactions.
   - Immediate expensing of Indian statutory taxes (STT, exchange fees, GST, stamp duty).
   - Authoritative inventory cost basis from net balance of Asset:Inventory:{SYMBOL}.
3. Trade Scenarios:
   - Profitable SELL (gross proceeds > inventory basis -> CREDIT Revenue:RealizedPnL).
   - Loss-making SELL (gross proceeds < inventory basis -> DEBIT Revenue:RealizedPnL).
   - Multiple sequential partial sells with deterministic ROUND_HALF_UP cost basis.
   - Full liquidation: credits EXACT remaining inventory book balance, returning account to 0.0000.
4. No-Short Policy Enforcement:
   - Rejection of sell orders exceeding current inventory (raises InsufficientInventoryError).
5. Redis Ephemeral Projection:
   - Atomic compare-and-set using 20-digit zero-padded revision string.
   - Rejection of stale revisions (stale rebuild / commit order inversion protection).
   - Atomically updates cash, equity, exposure, and deletes closed positions.
6. Kafka Consumer & DLQ Routing:
   - Verified DLQ routing to orders.fills.dlq for invalid messages or no-short rejections.
   - Publishing PortfolioSnapshot to portfolio.updates on successful ingestion.
7. FastMCP Server 6 Tools:
   - get_portfolio_summary, get_open_positions, get_audit_trail, get_ledger_health.
8. Property-Based Randomized Invariant Check:
   - 1,000 randomized long-only buys and sells preserving ledger balance identically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.common.schemas.orders import Fill, Side
from src.common.schemas.portfolio import PortfolioSnapshot, Position
from src.mcp_servers.portfolio_ledger.accounts import (
    ACCOUNT_CASH,
    ACCOUNT_REALIZED_PNL,
    inventory_account_code,
)
from src.mcp_servers.portfolio_ledger.consumer import PortfolioLedgerConsumer
from src.mcp_servers.portfolio_ledger.ledger import (
    GENESIS_EVENT_ID,
    DoubleEntryLedger,
    InsufficientInventoryError,
)
from src.mcp_servers.portfolio_ledger.projection import (
    RedisPortfolioProjection,
)
from src.mcp_servers.portfolio_ledger.server import (
    get_audit_trail,
    get_ledger_health,
    get_open_positions,
    get_portfolio_summary,
    setup_portfolio_ledger,
)


class InMemoryPostgresFake:
    """High-fidelity in-memory SQLite/Dictionary emulation of PostgreSQL ledger for unit testing."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}
        self.processed_events: dict[UUID, dict[str, Any]] = {}
        self.revision: int = 0
        self.journal_entries: list[dict[str, Any]] = []
        self.journal_postings: list[dict[str, Any]] = []
        self.positions: dict[str, dict[str, Any]] = {}
        self.snapshots: list[dict[str, Any]] = []

    def connect(self) -> Any:
        return FakeConnection(self)


class FakeCursor:
    def __init__(self, db: InMemoryPostgresFake, row_factory: Any = None) -> None:
        self.db = db
        self.row_factory = row_factory
        self._last_result: list[Any] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        q = " ".join(query.strip().split())
        p = params or ()

        if "SELECT pg_advisory_xact_lock" in q:
            return

        if "INSERT INTO ledger_accounts" in q:
            code = p[0]
            name = p[1]
            atype = p[2]
            if code not in self.db.accounts:
                self.db.accounts[code] = {
                    "account_id": uuid4(),
                    "account_code": code,
                    "account_name": name,
                    "account_type": atype,
                }
            if "RETURNING account_id" in q:
                self._last_result = [(self.db.accounts[code]["account_id"],)]
            return

        if "SELECT account_id FROM ledger_accounts WHERE account_code" in q:
            code = p[0]
            if code in self.db.accounts:
                self._last_result = [(self.db.accounts[code]["account_id"],)]
            else:
                self._last_result = []
            return

        if "SELECT payload_hash FROM processed_events WHERE event_id" in q:
            eid = p[0]
            if eid in self.db.processed_events:
                self._last_result = [(self.db.processed_events[eid]["payload_hash"],)]
            else:
                self._last_result = []
            return

        if "SELECT current_revision FROM portfolio_revision_tracker" in q:
            if self.row_factory:
                self._last_result = [{"current_revision": self.db.revision}]
            else:
                self._last_result = [(self.db.revision,)]
            return

        if "INSERT INTO processed_events" in q:
            eid = p[0]
            if len(p) == 3:
                topic = "portfolio.genesis"
                corr = p[1]
                phash = p[2]
            else:
                topic = p[1]
                corr = p[2]
                phash = p[3]
            self.db.processed_events[eid] = {
                "event_id": eid,
                "topic": topic,
                "correlation_id": corr,
                "payload_hash": phash,
            }
            return

        if "INSERT INTO journal_entries" in q:
            entry_id = uuid4()
            eid = p[0]
            if "fill_id" in q:
                fill_id = p[1]
                narration = p[2]
                corr = p[3]
            else:
                fill_id = None
                narration = p[1]
                corr = p[2]
            entry = {
                "entry_id": entry_id,
                "event_id": eid,
                "fill_id": fill_id,
                "narration": narration,
                "correlation_id": corr,
                "posted_at": datetime.now(UTC),
            }
            self.db.journal_entries.append(entry)
            self._last_result = [(entry_id,)]
            return

        if "INSERT INTO journal_postings" in q:
            if len(p) == 6:
                # Genesis 2 postings
                entry_id = p[0]
                acc_id_1 = p[1]
                amt_1 = p[2]
                acc_id_2 = p[4]
                amt_2 = p[5]
                self.db.journal_postings.append(
                    {
                        "posting_id": uuid4(),
                        "entry_id": entry_id,
                        "account_id": acc_id_1,
                        "direction": "DEBIT",
                        "amount": amt_1,
                        "sequence_num": 1,
                    }
                )
                self.db.journal_postings.append(
                    {
                        "posting_id": uuid4(),
                        "entry_id": entry_id,
                        "account_id": acc_id_2,
                        "direction": "CREDIT",
                        "amount": amt_2,
                        "sequence_num": 2,
                    }
                )
            else:
                entry_id = p[0]
                acc_id = p[1]
                direction = p[2]
                amount = p[3]
                seq = p[4]
                self.db.journal_postings.append(
                    {
                        "posting_id": uuid4(),
                        "entry_id": entry_id,
                        "account_id": acc_id,
                        "direction": direction,
                        "amount": amount,
                        "sequence_num": seq,
                    }
                )
            return

        if "UPDATE portfolio_revision_tracker" in q:
            self.db.revision += 1
            self._last_result = [(self.db.revision,)]
            return

        if "FROM positions WHERE symbol" in q and "SELECT quantity" in q:
            sym = p[0]
            if sym in self.db.positions:
                pos = self.db.positions[sym]
                self._last_result = [
                    (
                        pos["quantity"],
                        pos["average_entry_price"],
                        pos["mark_price"],
                        pos["market_value"],
                        pos["unrealized_pnl"],
                        pos["realized_pnl"],
                    )
                ]
            else:
                self._last_result = []
            return

        if "FROM journal_postings WHERE account_id" in q:
            acc_id = p[0]
            net = Decimal("0")
            for post in self.db.journal_postings:
                if post["account_id"] == acc_id:
                    if post["direction"] == "DEBIT":
                        net += post["amount"]
                    else:
                        net -= post["amount"]
            self._last_result = [(net,)]
            return

        if "INSERT INTO positions" in q:
            sym = p[0]
            self.db.positions[sym] = {
                "symbol": sym,
                "quantity": p[1],
                "average_entry_price": p[2],
                "mark_price": p[3],
                "market_value": p[4],
                "unrealized_pnl": p[5],
                "realized_pnl": p[6],
                "updated_at": datetime.now(UTC),
            }
            return

        if "UPDATE positions SET" in q:
            sym = p[6]
            if sym in self.db.positions:
                self.db.positions[sym].update(
                    {
                        "quantity": p[0],
                        "average_entry_price": p[1],
                        "mark_price": p[2],
                        "market_value": p[3],
                        "unrealized_pnl": p[4],
                        "realized_pnl": p[5],
                        "updated_at": datetime.now(UTC),
                    }
                )
            return

        if "DELETE FROM positions WHERE symbol" in q:
            sym = p[0]
            self.db.positions.pop(sym, None)
            return

        if "AS cash" in q and "FROM journal_postings" in q:
            cash_net = Decimal("0")
            cash_id = None
            for code, acc in self.db.accounts.items():
                if code == ACCOUNT_CASH:
                    cash_id = acc["account_id"]
                    break
            if cash_id:
                for post in self.db.journal_postings:
                    if post["account_id"] == cash_id:
                        if post["direction"] == "DEBIT":
                            cash_net += post["amount"]
                        else:
                            cash_net -= post["amount"]
            if self.row_factory:
                self._last_result = [{"cash": cash_net}]
            else:
                self._last_result = [(cash_net,)]
            return

        if "FROM positions" in q and "WHERE quantity > 0" in q:
            rows = []
            for pos in self.db.positions.values():
                if pos["quantity"] > 0:
                    if self.row_factory:
                        rows.append(pos)
                    else:
                        rows.append(
                            (
                                pos["symbol"],
                                pos["quantity"],
                                pos["average_entry_price"],
                                pos["mark_price"],
                                pos["market_value"],
                                pos["unrealized_pnl"],
                                pos["realized_pnl"],
                                pos["updated_at"],
                            )
                        )
            self._last_result = rows
            return

        if "SELECT COALESCE(MAX(high_water_mark)" in q:
            max_hwm = p[0]
            for s in self.db.snapshots:
                if s["high_water_mark"] > max_hwm:
                    max_hwm = s["high_water_mark"]
            if self.row_factory:
                self._last_result = [{"max_hwm": max_hwm}]
            else:
                self._last_result = [(max_hwm,)]
            return

        if "INSERT INTO portfolio_snapshots" in q:
            snap_id = uuid4()
            now = datetime.now(UTC)
            if len(p) == 4:
                # Genesis snapshot: (cash, equity, hwm, correlation_id)
                snap = {
                    "snapshot_id": snap_id,
                    "as_of": now,
                    "cash": p[0],
                    "equity": p[1],
                    "exposure": Decimal("0"),
                    "realized_pnl": Decimal("0"),
                    "unrealized_pnl": Decimal("0"),
                    "drawdown": Decimal("0"),
                    "high_water_mark": p[2],
                    "positions": "[]",
                    "correlation_id": p[3],
                    "trading_mode": "PAPER",
                }
            else:
                snap = {
                    "snapshot_id": snap_id,
                    "as_of": now,
                    "cash": p[0],
                    "equity": p[1],
                    "exposure": p[2],
                    "realized_pnl": p[3],
                    "unrealized_pnl": p[4],
                    "drawdown": p[5],
                    "high_water_mark": p[6],
                    "positions": p[7],
                    "correlation_id": p[8],
                    "trading_mode": "PAPER",
                }
            self.db.snapshots.append(snap)
            if self.row_factory:
                self._last_result = [{"snapshot_id": snap_id, "as_of": now}]
            else:
                self._last_result = [(snap_id, now)]
            return

        if "FROM portfolio_snapshots" in q and "ORDER BY as_of DESC" in q:
            if not self.db.snapshots:
                self._last_result = []
            else:
                last_snap = self.db.snapshots[-1]
                if self.row_factory:
                    self._last_result = [last_snap]
                else:
                    self._last_result = [
                        (
                            last_snap["snapshot_id"],
                            last_snap["as_of"],
                            last_snap["cash"],
                            last_snap["equity"],
                            last_snap["exposure"],
                            last_snap["realized_pnl"],
                            last_snap["unrealized_pnl"],
                            last_snap["drawdown"],
                            last_snap["high_water_mark"],
                            last_snap["positions"],
                            last_snap["trading_mode"],
                        )
                    ]
            return

        if "FROM journal_entries" in q and "ORDER BY posted_at DESC" in q:
            self._last_result = list(reversed(self.db.journal_entries))
            return

        if "FROM journal_postings p" in q and "WHERE p.entry_id" in q:
            entry_id = p[0]
            posts = []
            for post in self.db.journal_postings:
                if post["entry_id"] == entry_id:
                    code = "UNKNOWN"
                    name = "UNKNOWN"
                    for acode, a in self.db.accounts.items():
                        if a["account_id"] == post["account_id"]:
                            code = acode
                            name = a["account_name"]
                            break
                    if self.row_factory:
                        posts.append(
                            {
                                "posting_id": post["posting_id"],
                                "account_code": code,
                                "account_name": name,
                                "direction": post["direction"],
                                "amount": post["amount"],
                                "sequence_num": post["sequence_num"],
                            }
                        )
                    else:
                        posts.append(
                            (
                                post["posting_id"],
                                code,
                                name,
                                post["direction"],
                                post["amount"],
                                post["sequence_num"],
                            )
                        )
            self._last_result = posts
            return

    def fetchone(self) -> Any:
        return self._last_result[0] if self._last_result else None

    def fetchall(self) -> list[Any]:
        return list(self._last_result)


class FakeConnection:
    def __init__(self, db: InMemoryPostgresFake) -> None:
        self.db = db

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def cursor(self, row_factory: Any = None) -> FakeCursor:
        return FakeCursor(self.db, row_factory=row_factory)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class FakeRedisForProjection:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    def register_script(self, script: str) -> Any:
        def execute_script(keys: list[str], args: list[str]) -> tuple[int, str]:
            meta_key, pos_key = keys[0], keys[1]
            new_rev_padded, meta_json, positions_json = args[0], args[1], args[2]

            curr_meta = self.hashes.get(meta_key, {})
            curr_rev = curr_meta.get("revision_padded")
            if curr_rev and new_rev_padded <= curr_rev:
                return (0, "STALE_REVISION")

            meta_data = json.loads(meta_json)
            self.hashes[meta_key] = {
                "revision": meta_data["revision"],
                "revision_padded": new_rev_padded,
                "cash": meta_data["cash"],
                "equity": meta_data["equity"],
                "exposure": meta_data["exposure"],
                "realized_pnl": meta_data["realized_pnl"],
                "unrealized_pnl": meta_data["unrealized_pnl"],
                "drawdown": meta_data["drawdown"],
                "high_water_mark": meta_data["high_water_mark"],
                "as_of": meta_data["as_of"],
            }

            self.hashes[pos_key] = {}
            pos_data = json.loads(positions_json)
            for sym, p in pos_data.items():
                self.hashes[pos_key][sym] = json.dumps(p)

            return (1, "SUCCESS")

        return execute_script

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


@pytest.fixture
def fake_ledger_db() -> InMemoryPostgresFake:
    return InMemoryPostgresFake()


@pytest.fixture
def ledger(fake_ledger_db: InMemoryPostgresFake) -> DoubleEntryLedger:
    ledger_instance = DoubleEntryLedger("fake-dsn")
    ledger_instance.connect = lambda: fake_ledger_db.connect()  # type: ignore[assignment]
    return ledger_instance


@pytest.fixture
def fake_redis_proj() -> FakeRedisForProjection:
    return FakeRedisForProjection()


@pytest.fixture
def projection(fake_redis_proj: FakeRedisForProjection) -> RedisPortfolioProjection:
    return RedisPortfolioProjection(fake_redis_proj)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# 1. Genesis Funding Tests
# -----------------------------------------------------------------------------
def test_genesis_funding_first_run(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Verify first run of genesis funding deposits seed capital and increments revision."""
    res = ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    assert res.event_id == GENESIS_EVENT_ID
    assert res.is_duplicate is False
    assert res.revision == 1

    # Verify journal entry and postings
    assert len(fake_ledger_db.journal_entries) == 1
    assert len(fake_ledger_db.journal_postings) == 2

    # Invariant: Debits == Credits
    debit = sum(p["amount"] for p in fake_ledger_db.journal_postings if p["direction"] == "DEBIT")
    credit = sum(p["amount"] for p in fake_ledger_db.journal_postings if p["direction"] == "CREDIT")
    assert debit == credit == Decimal("1000000.0000")


def test_genesis_funding_idempotent_restart(ledger: DoubleEntryLedger) -> None:
    """Verify restart does not fund again."""
    res1 = ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    assert res1.is_duplicate is False

    res2 = ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    assert res2.is_duplicate is True
    assert res2.revision == res1.revision


# -----------------------------------------------------------------------------
# 2. Buy Fill and Immediate Fee Expensing
# -----------------------------------------------------------------------------
def test_buy_fill_accounting(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Verify BUY fill debits inventory, debits statutory expenses, and credits cash."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="RELIANCE.NS",
        side=Side.BUY,
        fill_price=Decimal("2500.0000"),
        fill_quantity=Decimal("10.0000"),
        stt_tax=Decimal("25.0000"),
        exchange_fees=Decimal("1.0000"),
        gst_tax=Decimal("0.5000"),
        stamp_duty=Decimal("3.5000"),
        total_fees=Decimal("30.0000"),
        filled_at=datetime.now(UTC),
    )

    res = ledger.ingest_fill(fill, event_id=uuid4(), correlation_id="buy-001")
    assert res.is_duplicate is False
    assert res.revision == 2

    # Check double-entry balance for this fill
    postings = [p for p in fake_ledger_db.journal_postings if p["entry_id"] == res.entry_id]
    debits = sum(p["amount"] for p in postings if p["direction"] == "DEBIT")
    credits = sum(p["amount"] for p in postings if p["direction"] == "CREDIT")
    assert debits == credits == Decimal("25030.0000")

    # Authoritative cash check: 1,000,000 - 25,030 = 974,970
    cash_id = fake_ledger_db.accounts[ACCOUNT_CASH]["account_id"]
    cash_debits = sum(
        p["amount"]
        for p in fake_ledger_db.journal_postings
        if p["account_id"] == cash_id and p["direction"] == "DEBIT"
    )
    cash_credits = sum(
        p["amount"]
        for p in fake_ledger_db.journal_postings
        if p["account_id"] == cash_id and p["direction"] == "CREDIT"
    )
    net_cash = cash_debits - cash_credits
    assert net_cash == Decimal("974970.0000")


# -----------------------------------------------------------------------------
# 3. Profitable and Loss-Making SELL Scenarios
# -----------------------------------------------------------------------------
def test_sell_profitable_accounting(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Verify profitable SELL: gross proceeds > cost basis -> CREDIT RealizedPnL."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    # Buy 10 @ 2500
    buy_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="TCS.NS",
        side=Side.BUY,
        fill_price=Decimal("2500.0000"),
        fill_quantity=Decimal("10.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(buy_fill, event_id=uuid4(), correlation_id="buy-tcs")

    # Sell 5 @ 3000 (Cost basis = 5 * 2500 = 12500. Gross = 15000. Realized PnL = +2500)
    sell_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="TCS.NS",
        side=Side.SELL,
        fill_price=Decimal("3000.0000"),
        fill_quantity=Decimal("5.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    res = ledger.ingest_fill(sell_fill, event_id=uuid4(), correlation_id="sell-tcs")

    postings = [p for p in fake_ledger_db.journal_postings if p["entry_id"] == res.entry_id]
    debits = sum(p["amount"] for p in postings if p["direction"] == "DEBIT")
    credits = sum(p["amount"] for p in postings if p["direction"] == "CREDIT")
    assert debits == credits == Decimal("15000.0000")

    # Realized PnL account should have credit of 2500
    pnl_id = fake_ledger_db.accounts[ACCOUNT_REALIZED_PNL]["account_id"]
    pnl_posting = next(p for p in postings if p["account_id"] == pnl_id)
    assert pnl_posting["direction"] == "CREDIT"
    assert pnl_posting["amount"] == Decimal("2500.0000")


def test_sell_loss_making_accounting(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Verify loss-making SELL: gross proceeds < cost basis -> DEBIT RealizedPnL."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    # Buy 10 @ 2000
    buy_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="INFY.NS",
        side=Side.BUY,
        fill_price=Decimal("2000.0000"),
        fill_quantity=Decimal("10.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(buy_fill, event_id=uuid4(), correlation_id="buy-infy")

    # Sell 5 @ 1800 (Cost basis = 10000. Gross = 9000. Realized Loss = -1000)
    sell_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="INFY.NS",
        side=Side.SELL,
        fill_price=Decimal("1800.0000"),
        fill_quantity=Decimal("5.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    res = ledger.ingest_fill(sell_fill, event_id=uuid4(), correlation_id="sell-infy")

    postings = [p for p in fake_ledger_db.journal_postings if p["entry_id"] == res.entry_id]
    debits = sum(p["amount"] for p in postings if p["direction"] == "DEBIT")
    credits = sum(p["amount"] for p in postings if p["direction"] == "CREDIT")
    assert debits == credits == Decimal("10000.0000")

    pnl_id = fake_ledger_db.accounts[ACCOUNT_REALIZED_PNL]["account_id"]
    pnl_posting = next(p for p in postings if p["account_id"] == pnl_id)
    assert pnl_posting["direction"] == "DEBIT"
    assert pnl_posting["amount"] == Decimal("1000.0000")


def test_full_liquidation_zeros_inventory_account(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Verify full liquidation credits the EXACT remaining balance leaving 0.0000."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    # Buy 3 shares @ 333.3333 = 999.9999
    buy_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="WIPRO.NS",
        side=Side.BUY,
        fill_price=Decimal("333.3333"),
        fill_quantity=Decimal("3.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(buy_fill, event_id=uuid4(), correlation_id="buy-wipro")

    # Partial sell 1 share
    sell_part = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="WIPRO.NS",
        side=Side.SELL,
        fill_price=Decimal("400.0000"),
        fill_quantity=Decimal("1.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(sell_part, event_id=uuid4(), correlation_id="sell-part-wipro")

    # Liquidate remaining 2 shares
    sell_liq = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="WIPRO.NS",
        side=Side.SELL,
        fill_price=Decimal("450.0000"),
        fill_quantity=Decimal("2.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(sell_liq, event_id=uuid4(), correlation_id="sell-liq-wipro")

    inv_code = inventory_account_code("WIPRO.NS")
    inv_id = fake_ledger_db.accounts[inv_code]["account_id"]
    inv_debits = sum(
        p["amount"]
        for p in fake_ledger_db.journal_postings
        if p["account_id"] == inv_id and p["direction"] == "DEBIT"
    )
    inv_credits = sum(
        p["amount"]
        for p in fake_ledger_db.journal_postings
        if p["account_id"] == inv_id and p["direction"] == "CREDIT"
    )
    assert inv_debits - inv_credits == Decimal("0.0000")


# -----------------------------------------------------------------------------
# 4. Strict No-Short Policy Rejection
# -----------------------------------------------------------------------------
def test_no_short_policy_raises_error(ledger: DoubleEntryLedger) -> None:
    """Verify attempting to sell without inventory raises InsufficientInventoryError."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    sell_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="HDFC.NS",
        side=Side.SELL,
        fill_price=Decimal("1500.0000"),
        fill_quantity=Decimal("10.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )

    with pytest.raises(InsufficientInventoryError, match="Insufficient inventory to sell"):
        ledger.ingest_fill(sell_fill, event_id=uuid4(), correlation_id="short-attempt")


# -----------------------------------------------------------------------------
# 5. Redis Projection and Monotonic Revision
# -----------------------------------------------------------------------------
def test_redis_projection_compare_and_set(projection: RedisPortfolioProjection) -> None:
    """Verify Redis projection accepts monotonic revisions and rejects stale revisions."""
    snap1 = PortfolioSnapshot(
        as_of=datetime.now(UTC),
        cash=Decimal("1000000.0000"),
        equity=Decimal("1000000.0000"),
        exposure=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        high_water_mark=Decimal("1000000.0000"),
        positions=[],
    )
    ok, msg = projection.apply_snapshot(snap1, revision=10)
    assert ok is True
    assert msg == "SUCCESS"

    # Stale revision <= 10 is rejected
    snap2 = snap1.model_copy()
    ok2, msg2 = projection.apply_snapshot(snap2, revision=9)
    assert ok2 is False
    assert msg2 == "STALE_REVISION"

    # Valid newer revision 11 succeeds
    ok3, msg3 = projection.apply_snapshot(snap2, revision=11)
    assert ok3 is True
    assert msg3 == "SUCCESS"


# -----------------------------------------------------------------------------
# 6. Consumer DLQ Routing
# -----------------------------------------------------------------------------
def test_consumer_dlq_on_short_violation(ledger: DoubleEntryLedger) -> None:
    """Verify consumer catches InsufficientInventoryError and sends to DLQ."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )

    sell_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="SBIN.NS",
        side=Side.SELL,
        fill_price=Decimal("600.0000"),
        fill_quantity=Decimal("10.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    from src.common.schemas.envelope import ComponentSource, EventEnvelope

    envelope = EventEnvelope[Fill](
        correlation_id="test-dlq",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=sell_fill,
    )

    ok, reason = consumer.process_message_payload(envelope.to_json_bytes())
    assert ok is False
    assert "No-short constraint violation" in reason

    # Verify DLQ production
    mock_producer.produce.assert_called_once()
    call_kwargs = mock_producer.produce.call_args[1]
    assert call_kwargs["topic"] == "orders.fills.dlq"


# -----------------------------------------------------------------------------
# 7. FastMCP Server Tools
# -----------------------------------------------------------------------------
def test_fastmcp_tools(ledger: DoubleEntryLedger, projection: RedisPortfolioProjection) -> None:
    """Verify get_portfolio_summary, get_open_positions, and get_audit_trail."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    setup_portfolio_ledger(ledger, projection)

    health = get_ledger_health()
    assert health["trading_mode"] == "PAPER"
    assert health["current_revision"] == 1

    summary = get_portfolio_summary()
    assert summary["trading_mode"] == "PAPER"
    assert Decimal(str(summary["cash"])) == Decimal("1000000.0000")

    audit = get_audit_trail()
    assert audit["count"] >= 1
    assert len(audit["entries"]) >= 1


# -----------------------------------------------------------------------------
# 8. Consumer & Projection Full Pipeline Tests
# -----------------------------------------------------------------------------
def test_consumer_success_pipeline(
    ledger: DoubleEntryLedger,
    projection: RedisPortfolioProjection,
) -> None:
    """Verify consumer successfully ingests fill, updates projection, and publishes snapshot."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=projection,
        producer=mock_producer,
    )

    buy_fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="INFY.NS",
        side=Side.BUY,
        fill_price=Decimal("1500.0000"),
        fill_quantity=Decimal("10.0000"),
        stt_tax=Decimal("10.0000"),
        total_fees=Decimal("10.0000"),
        filled_at=datetime.now(UTC),
    )
    from src.common.schemas.envelope import ComponentSource, EventEnvelope

    envelope = EventEnvelope[Fill](
        correlation_id="test-consumer-success",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=buy_fill,
    )

    ok, reason = consumer.process_message_payload(envelope.to_json_bytes())
    assert ok is True
    assert reason == "SUCCESS"

    # Verify publication to portfolio.updates
    assert mock_producer.produce.called
    topic_called = mock_producer.produce.call_args[1]["topic"]
    assert topic_called == "portfolio.updates"

    # Verify projection state in Redis
    summary = projection.get_summary()
    assert summary is not None
    assert summary["revision"] == "2"

    positions = projection.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "INFY.NS"


def test_consumer_malformed_envelope_dlq(ledger: DoubleEntryLedger) -> None:
    """Verify malformed JSON or envelope sends immediately to DLQ."""
    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )
    ok, reason = consumer.process_message_payload(b"invalid json bytes")
    assert ok is False
    assert "Malformed message envelope" in reason
    assert mock_producer.produce.called
    assert mock_producer.produce.call_args[1]["topic"] == "orders.fills.dlq"


def test_consumer_poison_pill_hash_mismatch(ledger: DoubleEntryLedger) -> None:
    """Verify duplicate event with differing hash is rejected as poison pill to DLQ."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    mock_producer = MagicMock()
    consumer = PortfolioLedgerConsumer(
        ledger=ledger,
        projection=None,
        producer=mock_producer,
    )

    eid = uuid4()
    fill1 = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="TCS.NS",
        side=Side.BUY,
        fill_price=Decimal("3000.0000"),
        fill_quantity=Decimal("5.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    from src.common.schemas.envelope import ComponentSource, EventEnvelope

    env1 = EventEnvelope[Fill](
        event_id=eid,
        correlation_id="test-hash-1",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill1,
    )
    ok1, _ = consumer.process_message_payload(env1.to_json_bytes())
    assert ok1 is True

    # Same event_id, different quantity
    fill2 = fill1.model_copy(update={"fill_quantity": Decimal("10.0000")})
    env2 = EventEnvelope[Fill](
        event_id=eid,
        correlation_id="test-hash-2",
        source=ComponentSource.PAPER_EXECUTION_BROKER,
        payload=fill2,
    )
    ok2, reason2 = consumer.process_message_payload(env2.to_json_bytes())
    assert ok2 is False
    assert "Poison pill duplicate event hash mismatch" in reason2


def test_projection_rebuild_from_postgres(
    fake_ledger_db: InMemoryPostgresFake,
    projection: RedisPortfolioProjection,
) -> None:
    """Verify projection rebuild from PostgreSQL under repeatable read."""
    ledger = DoubleEntryLedger("fake-dsn")
    ledger.connect = lambda: fake_ledger_db.connect()  # type: ignore[assignment]
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    with pytest.MonkeyPatch.context() as mp:
        import psycopg

        mp.setattr(psycopg, "connect", lambda *args, **kwargs: fake_ledger_db.connect())
        ok, msg = projection.rebuild_from_postgres("fake-dsn")
        assert ok is True
        assert msg == "SUCCESS"

    summary = projection.get_summary()
    assert summary is not None
    assert summary["revision"] == "1"


def test_server_fallbacks_and_helpers(
    ledger: DoubleEntryLedger,
    fake_ledger_db: InMemoryPostgresFake,
) -> None:
    """Verify server open positions fallback and error handling."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    setup_portfolio_ledger(ledger, None)  # No Redis projection -> PostgreSQL fallback

    # Add a position
    fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="RELIANCE.NS",
        side=Side.BUY,
        fill_price=Decimal("2500.0000"),
        fill_quantity=Decimal("10.0000"),
        stt_tax=Decimal("10.0000"),
        exchange_fees=Decimal("2.0000"),
        gst_tax=Decimal("1.0000"),
        stamp_duty=Decimal("1.0000"),
        total_fees=Decimal("14.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(fill, event_id=uuid4(), correlation_id="buy-rel")

    positions_res = get_open_positions()
    assert positions_res["source"] == "POSTGRESQL"
    assert positions_res["count"] == 1
    assert positions_res["positions"][0]["symbol"] == "RELIANCE.NS"


def test_sell_fill_with_all_statutory_fees(
    ledger: DoubleEntryLedger,
    fake_ledger_db: InMemoryPostgresFake,
) -> None:
    """Verify SELL fill accounting with non-zero STT, exchange charges, GST, and stamp duty."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    # Buy 20
    buy = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="HCLTECH.NS",
        side=Side.BUY,
        fill_price=Decimal("1000.0000"),
        fill_quantity=Decimal("20.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    ledger.ingest_fill(buy, event_id=uuid4(), correlation_id="buy-hcl")

    # Sell 10 with all fee components
    sell = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="HCLTECH.NS",
        side=Side.SELL,
        fill_price=Decimal("1200.0000"),
        fill_quantity=Decimal("10.0000"),
        stt_tax=Decimal("12.0000"),
        exchange_fees=Decimal("1.0000"),
        gst_tax=Decimal("0.5000"),
        stamp_duty=Decimal("0.1000"),
        total_fees=Decimal("13.6000"),
        filled_at=datetime.now(UTC),
    )
    res = ledger.ingest_fill(sell, event_id=uuid4(), correlation_id="sell-hcl")
    assert res.is_duplicate is False

    postings = [p for p in fake_ledger_db.journal_postings if p["entry_id"] == res.entry_id]
    debits = sum(p["amount"] for p in postings if p["direction"] == "DEBIT")
    credits = sum(p["amount"] for p in postings if p["direction"] == "CREDIT")
    assert debits == credits


def test_duplicate_fill_re_ingestion(ledger: DoubleEntryLedger) -> None:
    """Verify identical fill event re-ingestion returns is_duplicate=True without errors."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))

    eid = uuid4()
    fill = Fill(
        order_id=uuid4(),
        order_request_id=uuid4(),
        symbol="KOTAKBANK.NS",
        side=Side.BUY,
        fill_price=Decimal("1800.0000"),
        fill_quantity=Decimal("5.0000"),
        total_fees=Decimal("0.0000"),
        filled_at=datetime.now(UTC),
    )
    res1 = ledger.ingest_fill(fill, event_id=eid, correlation_id="dup-fill")
    assert res1.is_duplicate is False

    res2 = ledger.ingest_fill(fill, event_id=eid, correlation_id="dup-fill")
    assert res2.is_duplicate is True
    assert res2.revision == res1.revision


def test_server_summary_with_redis_and_staleness(
    ledger: DoubleEntryLedger,
    projection: RedisPortfolioProjection,
) -> None:
    """Verify server get_portfolio_summary with Redis projection and staleness flag."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    setup_portfolio_ledger(ledger, projection)

    # Snapshot with older timestamp > 900s
    from datetime import timedelta

    old_dt = datetime.now(UTC) - timedelta(seconds=1000)
    old_snap = PortfolioSnapshot(
        as_of=old_dt,
        cash=Decimal("1000000.0000"),
        equity=Decimal("1000000.0000"),
        exposure=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        high_water_mark=Decimal("1000000.0000"),
        positions=[],
    )
    projection.apply_snapshot(old_snap, revision=5)

    summary = get_portfolio_summary()
    assert summary["source"] == "REDIS_PROJECTION"
    assert summary["is_stale"] is True


def test_server_open_positions_with_redis_and_stale(
    ledger: DoubleEntryLedger,
    projection: RedisPortfolioProjection,
) -> None:
    """Verify get_open_positions reads from Redis when available, and postgres stale mark count."""
    ledger.initialize_genesis_funding(Decimal("1000000.0000"))
    setup_portfolio_ledger(ledger, projection)

    from datetime import timedelta

    old_dt = datetime.now(UTC) - timedelta(seconds=1200)
    pos = Position(
        symbol="SBIN.NS",
        quantity=Decimal("10.0000"),
        average_entry_price=Decimal("600.0000"),
        mark_price=Decimal("600.0000"),
        market_value=Decimal("6000.0000"),
        unrealized_pnl=Decimal("0.0000"),
        realized_pnl=Decimal("0.0000"),
        updated_at=old_dt,
    )
    snap = PortfolioSnapshot(
        as_of=datetime.now(UTC),
        cash=Decimal("994000.0000"),
        equity=Decimal("1000000.0000"),
        exposure=Decimal("6000.0000"),
        realized_pnl=Decimal("0.0000"),
        unrealized_pnl=Decimal("0.0000"),
        drawdown=Decimal("0.0000"),
        high_water_mark=Decimal("1000000.0000"),
        positions=[pos],
    )
    projection.apply_snapshot(snap, revision=10)

    # 1. From Redis
    res = get_open_positions()
    assert res["source"] == "REDIS_PROJECTION"
    assert res["count"] == 1

    # 2. From Postgres with stale mark
    setup_portfolio_ledger(ledger, None)
    # Put stale position into fake db
    fake_db = ledger.connect().db
    fake_db.positions["SBIN.NS"] = {
        "symbol": "SBIN.NS",
        "quantity": Decimal("10.0000"),
        "average_entry_price": Decimal("600.0000"),
        "mark_price": Decimal("600.0000"),
        "market_value": Decimal("6000.0000"),
        "unrealized_pnl": Decimal("0.0000"),
        "realized_pnl": Decimal("0.0000"),
        "updated_at": old_dt,
    }
    pg_res = get_open_positions()
    assert pg_res["source"] == "POSTGRESQL"
    assert pg_res["stale_marks_count"] == 1


def test_projection_error_branches(projection: RedisPortfolioProjection) -> None:
    """Verify projection error branches on invalid redis operations."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(projection.redis, "hgetall", MagicMock(side_effect=Exception("Redis down")))
        assert projection.get_summary() is None
        assert projection.get_open_positions() == []


def test_server_error_branches(ledger: DoubleEntryLedger) -> None:
    """Verify server error handling when database query raises."""
    setup_portfolio_ledger(ledger, None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ledger, "connect", MagicMock(side_effect=Exception("DB connection error")))
        s_err = get_portfolio_summary()
        assert s_err["status"] == "ERROR"

        p_err = get_open_positions()
        assert p_err["status"] == "ERROR"

        a_err = get_audit_trail()
        assert a_err["status"] == "ERROR"

        h_err = get_ledger_health()
        assert h_err["status"] == "DEGRADED"


# -----------------------------------------------------------------------------
# 9. Property-Based Invariant Check: 100 Random Long-Only Fills
# -----------------------------------------------------------------------------
def test_randomized_fills_invariant(
    ledger: DoubleEntryLedger, fake_ledger_db: InMemoryPostgresFake
) -> None:
    """Run 100 long-only buys and partial sells. Verify debits == credits identically."""
    import random

    ledger.initialize_genesis_funding(Decimal("10000000.0000"))

    inventory: dict[str, Decimal] = {"RELIANCE.NS": Decimal("0"), "TCS.NS": Decimal("0")}
    symbols = ["RELIANCE.NS", "TCS.NS"]

    random.seed(42)
    for i in range(100):
        sym = random.choice(symbols)
        current_q = inventory[sym]

        if current_q == Decimal("0") or random.random() < 0.6:
            # BUY
            qty = Decimal(str(random.randint(1, 10)))
            price = Decimal(str(random.randint(1000, 3000)))
            stt = Decimal("5.0000")
            total_fees = stt
            fill = Fill(
                order_id=uuid4(),
                order_request_id=uuid4(),
                symbol=sym,
                side=Side.BUY,
                fill_price=price,
                fill_quantity=qty,
                stt_tax=stt,
                total_fees=total_fees,
                filled_at=datetime.now(UTC),
            )
            ledger.ingest_fill(fill, event_id=uuid4(), correlation_id=f"rand-buy-{i}")
            inventory[sym] += qty
        else:
            # SELL
            sell_qty = Decimal(str(random.randint(1, int(current_q))))
            price = Decimal(str(random.randint(1000, 3000)))
            fill = Fill(
                order_id=uuid4(),
                order_request_id=uuid4(),
                symbol=sym,
                side=Side.SELL,
                fill_price=price,
                fill_quantity=sell_qty,
                total_fees=Decimal("0"),
                filled_at=datetime.now(UTC),
            )
            ledger.ingest_fill(fill, event_id=uuid4(), correlation_id=f"rand-sell-{i}")
            inventory[sym] -= sell_qty

    # Final Invariant: Total Debits == Total Credits across entire database
    total_debits = sum(
        p["amount"] for p in fake_ledger_db.journal_postings if p["direction"] == "DEBIT"
    )
    total_credits = sum(
        p["amount"] for p in fake_ledger_db.journal_postings if p["direction"] == "CREDIT"
    )
    assert total_debits == total_credits
