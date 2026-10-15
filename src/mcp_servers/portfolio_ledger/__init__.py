"""MCP Server 6: Portfolio Ledger (double-entry PostgreSQL ledger + Redis intraday state).

Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

from src.mcp_servers.portfolio_ledger.accounts import (
    ACCOUNT_CASH,
    ACCOUNT_EXPENSE_EXCHANGE,
    ACCOUNT_EXPENSE_GST,
    ACCOUNT_EXPENSE_STAMP,
    ACCOUNT_EXPENSE_STT,
    ACCOUNT_INITIAL_CAPITAL,
    ACCOUNT_INVENTORY_PREFIX,
    ACCOUNT_REALIZED_PNL,
    AccountType,
    PostingDirection,
    inventory_account_code,
    inventory_account_name,
)
from src.mcp_servers.portfolio_ledger.consumer import PortfolioLedgerConsumer
from src.mcp_servers.portfolio_ledger.ledger import (
    GENESIS_EVENT_ID,
    DoubleEntryLedger,
    DuplicateEventDifferentPayloadError,
    IngestionResult,
    InsufficientInventoryError,
    LedgerError,
)
from src.mcp_servers.portfolio_ledger.projection import (
    RedisPortfolioProjection,
    pad_revision,
)
from src.mcp_servers.portfolio_ledger.server import (
    get_audit_trail,
    get_ledger_health,
    get_open_positions,
    get_portfolio_summary,
    mcp,
    setup_portfolio_ledger,
)

__all__ = [
    "ACCOUNT_CASH",
    "ACCOUNT_EXPENSE_EXCHANGE",
    "ACCOUNT_EXPENSE_GST",
    "ACCOUNT_EXPENSE_STAMP",
    "ACCOUNT_EXPENSE_STT",
    "ACCOUNT_INITIAL_CAPITAL",
    "ACCOUNT_INVENTORY_PREFIX",
    "ACCOUNT_REALIZED_PNL",
    "GENESIS_EVENT_ID",
    "AccountType",
    "DoubleEntryLedger",
    "DuplicateEventDifferentPayloadError",
    "IngestionResult",
    "InsufficientInventoryError",
    "LedgerError",
    "PortfolioLedgerConsumer",
    "PostingDirection",
    "RedisPortfolioProjection",
    "get_audit_trail",
    "get_ledger_health",
    "get_open_positions",
    "get_portfolio_summary",
    "inventory_account_code",
    "inventory_account_name",
    "mcp",
    "pad_revision",
    "setup_portfolio_ledger",
]
