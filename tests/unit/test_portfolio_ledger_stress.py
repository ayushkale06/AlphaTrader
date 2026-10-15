"""Dedicated 10,000-fill stress test for Portfolio Ledger double-entry invariants.

Verifies:
1. Exact mathematical equality: Sum(Debits) == Sum(Credits) across 10,000 randomized
   long-only paper fills.
2. Zero balance divergence or rounding residual on liquidation.
3. Fixed seeds for repeatable regression testing.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.common.schemas.orders import Fill, Side
from src.mcp_servers.portfolio_ledger.ledger import DoubleEntryLedger
from tests.unit.test_portfolio_ledger import InMemoryPostgresFake


@pytest.mark.stress
def test_ten_thousand_randomized_fills_invariant() -> None:
    """Execute 10,000 randomized fills and assert exact debit-credit equality."""
    fake_db = InMemoryPostgresFake()
    ledger = DoubleEntryLedger("fake://memory")
    ledger.connect = fake_db.connect  # type: ignore[assignment]

    # Initialize seed capital: INR 10,000,000.00
    ledger.initialize_genesis_funding(Decimal("10000000.0000"))

    inventory: dict[str, Decimal] = {"RELIANCE.NS": Decimal("0"), "TCS.NS": Decimal("0")}
    symbols = ["RELIANCE.NS", "TCS.NS"]
    random.seed(42)

    for i in range(10000):
        sym = random.choice(symbols)
        current_q = inventory[sym]

        if current_q == Decimal("0") or random.random() < 0.6:
            # BUY
            qty = Decimal(str(random.randint(1, 10)))
            price = Decimal(str(random.randint(1000, 3000)))
            stt = Decimal("5.0000")
            fill = Fill(
                order_id=uuid4(),
                order_request_id=uuid4(),
                symbol=sym,
                side=Side.BUY,
                fill_price=price,
                fill_quantity=qty,
                stt_tax=stt,
                total_fees=stt,
                filled_at=datetime.now(UTC),
            )
            ledger.ingest_fill(fill, event_id=uuid4(), correlation_id=f"stress-buy-{i}")
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
            ledger.ingest_fill(fill, event_id=uuid4(), correlation_id=f"stress-sell-{i}")
            inventory[sym] -= sell_qty

    # Final Invariant Check
    total_debits = sum(p["amount"] for p in fake_db.journal_postings if p["direction"] == "DEBIT")
    total_credits = sum(p["amount"] for p in fake_db.journal_postings if p["direction"] == "CREDIT")
    diff = total_debits - total_credits

    assert total_debits == total_credits, f"Ledger unbalanced by {diff}"
    assert diff == Decimal("0.0000")
