"""FastMCP Server 6: Portfolio Ledger.

Exposes tools:
1. get_portfolio_summary: Returns cash, equity, exposure, PnL, drawdown, HWM.
2. get_open_positions: Returns list of open paper positions with WAC, market value.
3. get_audit_trail: Paginated descending audit trail of journal entries and postings.
4. get_ledger_health: Returns health status, trading mode, revision, and DB status.
Governance: ADR-002, ADR-004, ADR-010.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from psycopg.rows import dict_row

from src.common.config import get_settings
from src.mcp_servers.portfolio_ledger.ledger import DoubleEntryLedger
from src.mcp_servers.portfolio_ledger.projection import RedisPortfolioProjection

logger = logging.getLogger("alphatrader.portfolio_ledger.server")

mcp = MCPServer("portfolio-ledger")

# Global instances initialized via setup_portfolio_ledger
_ledger: DoubleEntryLedger | None = None
_projection: RedisPortfolioProjection | None = None


def setup_portfolio_ledger(
    ledger: DoubleEntryLedger,
    projection: RedisPortfolioProjection | None = None,
) -> None:
    """Inject dependencies into the FastMCP server."""
    global _ledger, _projection
    _ledger = ledger
    _projection = projection


def _get_ledger() -> DoubleEntryLedger:
    global _ledger
    if _ledger is None:
        settings = get_settings()
        _ledger = DoubleEntryLedger(settings.postgres_sync_dsn)
    return _ledger


def _get_projection() -> RedisPortfolioProjection | None:
    global _projection
    if _projection is None:
        try:
            import redis

            settings = get_settings()
            r = redis.from_url(settings.redis_url)
            _projection = RedisPortfolioProjection(r)
        except Exception as e:
            logger.warning("Could not initialize Redis projection: %s", e)
            _projection = None
    return _projection


@mcp.tool()
def get_portfolio_summary() -> dict[str, Any]:
    """Fetch the real-time portfolio summary (cash, equity, exposure, PnL, drawdown, HWM)."""
    projection = _get_projection()
    summary = None
    if projection is not None:
        summary = projection.get_summary()

    if summary is not None:
        # Check staleness if updated_at / as_of > 900 seconds
        as_of_str = summary.get("as_of")
        is_stale = False
        if as_of_str:
            try:
                as_of_dt = datetime.fromisoformat(as_of_str)
                age_seconds = (datetime.now(UTC) - as_of_dt).total_seconds()
                if age_seconds > 900:
                    is_stale = True
            except Exception:
                is_stale = False

        summary["is_stale"] = is_stale
        summary["source"] = "REDIS_PROJECTION"
        summary["trading_mode"] = "PAPER"
        return summary

    # Fallback to PostgreSQL
    ledger = _get_ledger()
    try:
        with ledger.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                    SELECT snapshot_id, as_of, cash, equity, exposure, realized_pnl, unrealized_pnl,
                           drawdown, high_water_mark, trading_mode
                    FROM portfolio_snapshots
                    ORDER BY as_of DESC, snapshot_id DESC
                    LIMIT 1;
                    """
            )
            row = cur.fetchone()
            if not row:
                return {
                    "status": "UNINITIALIZED",
                    "trading_mode": "PAPER",
                    "message": "Portfolio ledger has not yet been funded.",
                }

            as_of_dt = row["as_of"]
            age_seconds = (datetime.now(UTC) - as_of_dt).total_seconds()
            is_stale = age_seconds > 900

            return {
                "snapshot_id": str(row["snapshot_id"]),
                "as_of": as_of_dt.isoformat(),
                "cash": str(row["cash"]),
                "equity": str(row["equity"]),
                "exposure": str(row["exposure"]),
                "realized_pnl": str(row["realized_pnl"]),
                "unrealized_pnl": str(row["unrealized_pnl"]),
                "drawdown": str(row["drawdown"]),
                "high_water_mark": str(row["high_water_mark"]),
                "is_stale": is_stale,
                "source": "POSTGRESQL_SNAPSHOT",
                "trading_mode": "PAPER",
            }
    except Exception as e:
        logger.exception("Failed to query portfolio summary from PostgreSQL: %s", e)
        return {
            "status": "ERROR",
            "error": str(e),
            "trading_mode": "PAPER",
        }


@mcp.tool()
def get_open_positions() -> dict[str, Any]:
    """Fetch current open positions with entry WAC, mark price, market value, and freshness."""
    projection = _get_projection()
    positions = None
    if projection is not None:
        positions = projection.get_open_positions()

    if positions:
        return {
            "source": "REDIS_PROJECTION",
            "count": len(positions),
            "positions": positions,
            "trading_mode": "PAPER",
        }

    # Fallback to PostgreSQL
    ledger = _get_ledger()
    try:
        with ledger.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                    SELECT symbol, quantity, average_entry_price, mark_price, market_value,
                           unrealized_pnl, realized_pnl, updated_at
                    FROM positions
                    WHERE quantity > 0
                    ORDER BY symbol;
                    """
            )
            rows = cur.fetchall()
            now = datetime.now(UTC)
            pos_list = []
            stale_count = 0
            for r in rows:
                upd = r["updated_at"]
                age_seconds = (now - upd).total_seconds()
                is_stale = age_seconds > 900
                if is_stale:
                    stale_count += 1
                pos_list.append(
                    {
                        "symbol": r["symbol"],
                        "quantity": str(r["quantity"]),
                        "average_entry_price": str(r["average_entry_price"]),
                        "mark_price": str(r["mark_price"]),
                        "market_value": str(r["market_value"]),
                        "unrealized_pnl": str(r["unrealized_pnl"]),
                        "realized_pnl": str(r["realized_pnl"]),
                        "updated_at": upd.isoformat(),
                        "is_stale": is_stale,
                    }
                )
            return {
                "source": "POSTGRESQL",
                "count": len(pos_list),
                "stale_marks_count": stale_count,
                "positions": pos_list,
                "trading_mode": "PAPER",
            }
    except Exception as e:
        logger.exception("Failed to query open positions from PostgreSQL: %s", e)
        return {
            "status": "ERROR",
            "error": str(e),
            "positions": [],
            "trading_mode": "PAPER",
        }


@mcp.tool()
def get_audit_trail(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Fetch paginated, descending double-entry journal entries and postings."""
    ledger = _get_ledger()
    try:
        with ledger.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT entry_id, event_id, fill_id, narration, posted_at, correlation_id
                FROM journal_entries
                ORDER BY posted_at DESC, entry_id DESC
                LIMIT %s OFFSET %s;
                """,
                (limit, offset),
            )
            entries = cur.fetchall()

            results = []
            for entry in entries:
                cur.execute(
                    """
                    SELECT p.posting_id, a.account_code, a.account_name,
                           p.direction, p.amount, p.sequence_num
                    FROM journal_postings p
                    JOIN ledger_accounts a ON p.account_id = a.account_id
                    WHERE p.entry_id = %s
                    ORDER BY p.sequence_num;
                    """,
                    (entry["entry_id"],),
                )
                postings = cur.fetchall()
                results.append(
                    {
                        "entry_id": str(entry["entry_id"]),
                        "event_id": str(entry["event_id"]),
                        "fill_id": str(entry["fill_id"]) if entry["fill_id"] else None,
                        "narration": entry["narration"],
                        "posted_at": entry["posted_at"].isoformat(),
                        "correlation_id": entry["correlation_id"],
                        "postings": [
                            {
                                "posting_id": str(p["posting_id"]),
                                "account_code": p["account_code"],
                                "account_name": p["account_name"],
                                "direction": p["direction"],
                                "amount": str(p["amount"]),
                                "sequence_num": p["sequence_num"],
                            }
                            for p in postings
                        ],
                    }
                )
            return {
                "count": len(results),
                "limit": limit,
                "offset": offset,
                "entries": results,
            }
    except Exception as e:
        logger.exception("Failed to query audit trail from PostgreSQL: %s", e)
        return {
            "status": "ERROR",
            "error": str(e),
            "entries": [],
        }


@mcp.tool()
def get_ledger_health() -> dict[str, Any]:
    """Check operational health, trading mode, current revision, and database connectivity."""
    ledger = _get_ledger()
    db_connected = False
    current_rev = 0
    try:
        with ledger.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_revision FROM portfolio_revision_tracker WHERE tracker_id = 1;"
            )
            row = cur.fetchone()
            if row:
                current_rev = row[0]
                db_connected = True
    except Exception as e:
        logger.warning("PostgreSQL health check failed: %s", e)
        db_connected = False

    return {
        "status": "HEALTHY" if db_connected else "DEGRADED",
        "trading_mode": "PAPER",
        "live_trading_enabled": False,
        "database_connected": db_connected,
        "current_revision": current_rev,
    }
