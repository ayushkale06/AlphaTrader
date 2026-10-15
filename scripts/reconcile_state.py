#!/usr/bin/env python3
"""CLI utility to reconcile Redis portfolio projection from PostgreSQL ledger.

Used during system startup, preflight checks, or post-crash recovery.
Usage:
    python scripts/reconcile_state.py [--check-only]
"""

from __future__ import annotations

import argparse
import sys

import redis

from src.common.config import AlphaTraderSettings
from src.common.logging import configure_logging
from src.mcp_servers.portfolio_ledger.projection import RedisPortfolioProjection
from src.mcp_servers.portfolio_ledger.reconciliation import PortfolioStateReconciler


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Reconcile Redis portfolio projection from authoritative PostgreSQL ledger"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check projection status without triggering reconciliation",
    )
    args = parser.parse_args()

    settings = AlphaTraderSettings()

    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    projection = RedisPortfolioProjection(r)

    if args.check_only:
        meta = projection.get_summary()
        positions = projection.get_open_positions()
        print(f"Current Redis Projection Meta: {meta}")
        print(f"Open Positions: {len(positions)}")
        sys.exit(0)

    reconciler = PortfolioStateReconciler(settings, projection)
    result = reconciler.reconcile()

    if result.success:
        print(
            f"State Reconciliation SUCCESS: revision={result.revision}, "
            f"cash={result.cash}, equity={result.equity}, positions={result.positions_count}"
        )
        sys.exit(0)
    else:
        print(f"State Reconciliation FAILED: {result.status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
