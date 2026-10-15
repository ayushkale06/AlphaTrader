"""Portfolio ledger and Redis projection reconciliation service.

Reconciles the fast-path Redis portfolio projection from the authoritative
PostgreSQL double-entry ledger on application startup or crash recovery.
Governance: ADR-002, ADR-010.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.common.config import AlphaTraderSettings
from src.mcp_servers.portfolio_ledger.projection import RedisPortfolioProjection

logger = logging.getLogger("alphatrader.portfolio_ledger.reconciliation")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of ledger-to-projection state reconciliation."""

    success: bool
    status: str
    revision: int | None = None
    cash: str | None = None
    equity: str | None = None
    positions_count: int = 0


class PortfolioStateReconciler:
    """Orchestrates cold-start or crash-recovery reconciliation of portfolio state."""

    def __init__(
        self,
        settings: AlphaTraderSettings,
        projection: RedisPortfolioProjection,
    ) -> None:
        self.settings = settings
        self.projection = projection

    def reconcile(self) -> ReconciliationResult:
        """Rebuild Redis portfolio projection directly from PostgreSQL ledger.

        Executes under REPEATABLE READ isolation to guarantee a point-in-time
        consistent view of cash, equity, and positions.
        """
        pg_dsn = self.settings.postgres_sync_dsn
        logger.info("Initiating portfolio state reconciliation from PostgreSQL...")

        success, msg = self.projection.rebuild_from_postgres(pg_dsn)
        if not success:
            logger.error("Portfolio state reconciliation failed: %s", msg)
            return ReconciliationResult(success=False, status=msg)

        # Inspect resulting projection state
        meta: dict[str, Any] = self.projection.get_summary() or {}
        positions = self.projection.get_open_positions()

        rev_val = int(meta.get("revision", 0)) if meta.get("revision") else None
        cash_val = meta.get("cash")
        equity_val = meta.get("equity")

        logger.info(
            "Portfolio state reconciled. Revision: %s, Cash: %s, Equity: %s, Positions: %d",
            rev_val,
            cash_val,
            equity_val,
            len(positions),
        )

        return ReconciliationResult(
            success=True,
            status="RECONCILED",
            revision=rev_val,
            cash=cash_val,
            equity=equity_val,
            positions_count=len(positions),
        )
