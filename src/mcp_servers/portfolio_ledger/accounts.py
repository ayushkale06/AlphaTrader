"""Ledger chart of accounts definitions and code constants.

Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    """Standard double-entry accounting element categories."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class PostingDirection(StrEnum):
    """Posting direction in double-entry bookkeeping."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


# System Account Codes
ACCOUNT_CASH = "1000"  # Asset: Settlement Cash
ACCOUNT_INVENTORY_PREFIX = "1100"  # Asset: Inventory (e.g. 1100:RELIANCE)
ACCOUNT_INITIAL_CAPITAL = "3000"  # Equity: Initial Paper Capital
ACCOUNT_REALIZED_PNL = "4000"  # Revenue: Realized Trading PnL
ACCOUNT_EXPENSE_STT = "5101"  # Expense: Securities Transaction Tax (STT)
ACCOUNT_EXPENSE_EXCHANGE = "5102"  # Expense: Exchange Turnover & SEBI Charges
ACCOUNT_EXPENSE_GST = "5103"  # Expense: Goods and Services Tax (GST)
ACCOUNT_EXPENSE_STAMP = "5104"  # Expense: Stamp Duty

DEFAULT_SYSTEM_ACCOUNTS: list[dict[str, str]] = [
    {
        "account_code": ACCOUNT_CASH,
        "account_name": "Asset:Cash",
        "account_type": AccountType.ASSET.value,
    },
    {
        "account_code": ACCOUNT_INITIAL_CAPITAL,
        "account_name": "Equity:InitialCapital",
        "account_type": AccountType.EQUITY.value,
    },
    {
        "account_code": ACCOUNT_REALIZED_PNL,
        "account_name": "Revenue:RealizedPnL",
        "account_type": AccountType.REVENUE.value,
    },
    {
        "account_code": ACCOUNT_EXPENSE_STT,
        "account_name": "Expense:STT",
        "account_type": AccountType.EXPENSE.value,
    },
    {
        "account_code": ACCOUNT_EXPENSE_EXCHANGE,
        "account_name": "Expense:ExchangeCharges",
        "account_type": AccountType.EXPENSE.value,
    },
    {
        "account_code": ACCOUNT_EXPENSE_GST,
        "account_name": "Expense:GST",
        "account_type": AccountType.EXPENSE.value,
    },
    {
        "account_code": ACCOUNT_EXPENSE_STAMP,
        "account_name": "Expense:StampDuty",
        "account_type": AccountType.EXPENSE.value,
    },
]


def inventory_account_code(symbol: str) -> str:
    """Return canonical account code for symbol inventory."""
    return f"{ACCOUNT_INVENTORY_PREFIX}:{symbol.upper()}"


def inventory_account_name(symbol: str) -> str:
    """Return human-readable account name for symbol inventory."""
    return f"Asset:Inventory:{symbol.upper()}"
