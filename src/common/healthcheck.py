"""CLI entry point for verifying AlphaTrader version, configuration, and safety invariants.

Usage::

    python -m src.common.healthcheck
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from src import __version__
from src.common.config import AlphaTraderSettings
from src.common.logging import configure_logging, correlation_context, get_logger


def build_health_report(settings: AlphaTraderSettings, correlation_id: str) -> dict[str, Any]:
    """Construct a deterministic health and configuration validity summary."""
    is_paper_safe = settings.trading_mode == "PAPER" and settings.live_trading_enabled is False
    return {
        "status": "HEALTHY" if is_paper_safe else "UNHEALTHY",
        "version": __version__,
        "config_valid": is_paper_safe,
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "service_name": settings.service_name,
        "app_env": settings.app_env,
        "market_timezone": settings.market_timezone,
        "nse_market_feed_mode": settings.nse_market_feed_mode,
        "risk_guardrail": {
            "max_daily_drawdown_pct": settings.max_daily_drawdown_pct,
            "max_position_size_pct": settings.max_position_size_pct,
            "max_open_positions": settings.max_open_positions,
            "max_orders_per_minute": settings.max_orders_per_minute,
        },
        "correlation_id": correlation_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Validate configuration and print version and health status to stdout."""
    _ = argv
    with correlation_context() as cid:
        try:
            settings = AlphaTraderSettings()
        except ValidationError as exc:
            error_report = {
                "status": "UNHEALTHY",
                "version": __version__,
                "config_valid": False,
                "correlation_id": cid,
                "errors": exc.errors(include_url=False, include_context=False),
            }
            print(json.dumps(error_report, indent=2, default=str), file=sys.stderr)
            return 1

        configure_logging(settings=settings, stream=sys.stderr)
        logger = get_logger("healthcheck")
        report = build_health_report(settings=settings, correlation_id=cid)

        logger.info(
            "AlphaTrader foundation healthcheck passed",
            extra={
                "version": __version__,
                "config_valid": report["config_valid"],
                "trading_mode": settings.trading_mode,
            },
        )
        print(json.dumps(report, indent=2))
        return 0 if report["config_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
