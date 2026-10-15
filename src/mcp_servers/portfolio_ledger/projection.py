"""Redis real-time portfolio projection manager with atomic compare-and-set.

Maintains fast-path read state in Redis under alphatrader:portfolio:*.
Governance: ADR-002, ADR-010.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import redis
from psycopg.rows import dict_row

from src.common.schemas.portfolio import PortfolioSnapshot, Position

logger = logging.getLogger("alphatrader.portfolio_ledger.projection")

# Lua compare-and-set atomic update script
# KEYS[1]: alphatrader:portfolio:meta
# KEYS[2]: alphatrader:portfolio:positions (Hash)
# ARGV[1]: new_revision_padded (20-digit zero-padded string)
# ARGV[2]: meta_json (string containing cash, equity, exposure, pnl, etc.)
# ARGV[3]: positions_json (JSON string of active positions dictionary)
LUA_UPDATE_PROJECTION = """
local meta_key = KEYS[1]
local positions_key = KEYS[2]
local new_rev_str = ARGV[1]
local meta_json = ARGV[2]
local positions_json = ARGV[3]

local current_rev_str = redis.call('HGET', meta_key, 'revision_padded')
if current_rev_str then
    -- Lexicographical string comparison on 20-digit zero-padded numbers is exact
    if new_rev_str <= current_rev_str then
        return {0, "STALE_REVISION"}
    end
end

-- Update meta hash
local meta = cjson.decode(meta_json)
redis.call('HSET', meta_key,
    'revision', meta.revision,
    'revision_padded', new_rev_str,
    'cash', meta.cash,
    'equity', meta.equity,
    'exposure', meta.exposure,
    'realized_pnl', meta.realized_pnl,
    'unrealized_pnl', meta.unrealized_pnl,
    'drawdown', meta.drawdown,
    'high_water_mark', meta.high_water_mark,
    'as_of', meta.as_of
)

-- Atomically replace positions hash
redis.call('DEL', positions_key)
local pos_map = cjson.decode(positions_json)
for symbol, pos_data in pairs(pos_map) do
    redis.call('HSET', positions_key, symbol, cjson.encode(pos_data))
end

return {1, "SUCCESS"}
"""


def pad_revision(rev: int) -> str:
    """Zero-pad revision to 20 digits for exact lexicographical comparison."""
    return f"{rev:020d}"


class RedisPortfolioProjection:
    """Manages ephemeral real-time portfolio read model in Redis."""

    KEY_META = "alphatrader:portfolio:meta"
    KEY_POSITIONS = "alphatrader:portfolio:positions"

    def __init__(self, redis_client: redis.Redis) -> None:  # type: ignore[type-arg]
        self.redis = redis_client
        self._lua_update = self.redis.register_script(LUA_UPDATE_PROJECTION)

    def apply_snapshot(self, snapshot: PortfolioSnapshot, revision: int) -> tuple[bool, str]:
        """Atomically project PortfolioSnapshot to Redis with monotonic revision guard."""
        rev_padded = pad_revision(revision)
        meta_dict = {
            "revision": str(revision),
            "cash": str(snapshot.cash),
            "equity": str(snapshot.equity),
            "exposure": str(snapshot.exposure),
            "realized_pnl": str(snapshot.realized_pnl),
            "unrealized_pnl": str(snapshot.unrealized_pnl),
            "drawdown": str(snapshot.drawdown),
            "high_water_mark": str(snapshot.high_water_mark),
            "as_of": snapshot.as_of.isoformat(),
        }

        positions_dict: dict[str, Any] = {}
        for pos in snapshot.positions:
            if pos.quantity > Decimal("0"):
                positions_dict[pos.symbol] = {
                    "symbol": pos.symbol,
                    "quantity": str(pos.quantity),
                    "average_entry_price": str(pos.average_entry_price),
                    "mark_price": str(pos.mark_price),
                    "market_value": str(pos.market_value),
                    "unrealized_pnl": str(pos.unrealized_pnl),
                    "realized_pnl": str(pos.realized_pnl),
                    "updated_at": pos.updated_at.isoformat(),
                }

        try:
            res = self._lua_update(
                keys=[self.KEY_META, self.KEY_POSITIONS],
                args=[rev_padded, json.dumps(meta_dict), json.dumps(positions_dict)],
            )
            code, msg = res[0], res[1].decode("utf-8") if isinstance(res[1], bytes) else str(res[1])
            return (code > 0), msg
        except Exception as e:
            logger.error("Failed to apply Redis portfolio projection: %s", e)
            return False, f"REDIS_ERROR: {e}"

    def get_summary(self) -> dict[str, Any] | None:
        """Fetch real-time portfolio summary from Redis projection."""
        try:
            meta = self.redis.hgetall(self.KEY_META)
            if not meta:
                return None
            res = {
                (k.decode("utf-8") if isinstance(k, bytes) else k): (
                    v.decode("utf-8") if isinstance(v, bytes) else v
                )
                for k, v in meta.items()
            }
            return res
        except Exception as e:
            logger.error("Error reading portfolio summary from Redis: %s", e)
            return None

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Fetch active open positions from Redis projection."""
        try:
            raw_pos = self.redis.hgetall(self.KEY_POSITIONS)
            positions = []
            for _, v in raw_pos.items():
                val_str = v.decode("utf-8") if isinstance(v, bytes) else v
                positions.append(json.loads(val_str))
            return positions
        except Exception as e:
            logger.error("Error reading open positions from Redis: %s", e)
            return []

    def rebuild_from_postgres(self, pg_dsn: str) -> tuple[bool, str]:
        """Rebuild Redis projection from PostgreSQL under REPEATABLE READ isolation."""
        try:
            with psycopg.connect(pg_dsn, autocommit=False) as conn:
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                with conn.cursor(row_factory=dict_row) as cur:
                    # 1. Fetch current revision
                    cur.execute(
                        "SELECT current_revision FROM portfolio_revision_tracker "
                        "WHERE tracker_id = 1;"
                    )
                    rev_row = cur.fetchone()
                    if not rev_row:
                        return False, "NO_REVISION_TRACKER"
                    revision = int(rev_row["current_revision"])

                    # 2. Fetch latest snapshot
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
                    snap_row = cur.fetchone()
                    if not snap_row:
                        return False, "NO_PORTFOLIO_SNAPSHOT"

                    # Parse positions
                    pos_raw = snap_row["positions"]
                    pos_data = json.loads(pos_raw) if isinstance(pos_raw, str) else pos_raw

                    positions = [Position.model_validate(p) for p in pos_data]

                    snapshot = PortfolioSnapshot(
                        snapshot_id=UUID(str(snap_row["snapshot_id"])),
                        as_of=snap_row["as_of"],
                        cash=Decimal(str(snap_row["cash"])),
                        equity=Decimal(str(snap_row["equity"])),
                        exposure=Decimal(str(snap_row["exposure"])),
                        realized_pnl=Decimal(str(snap_row["realized_pnl"])),
                        unrealized_pnl=Decimal(str(snap_row["unrealized_pnl"])),
                        drawdown=Decimal(str(snap_row["drawdown"])),
                        high_water_mark=Decimal(str(snap_row["high_water_mark"])),
                        positions=positions,
                        trading_mode="PAPER",
                    )

                    conn.commit()

            success, msg = self.apply_snapshot(snapshot, revision)
            return success, msg
        except Exception as e:
            logger.error("Failed to rebuild projection from PostgreSQL: %s", e)
            return False, f"REBUILD_FAILED: {e}"
