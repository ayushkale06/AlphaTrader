"""FastMCP Read-Only Server for Market Data Streamer (official mcp SDK).

Exposes 5 read-only tools to agents and orchestrator:
1. get_latest_quote(symbol: str) -> dict
2. get_candles(symbol: str, interval: str, limit: int) -> list[dict]
3. get_market_status() -> dict
4. list_instruments() -> dict
5. get_feed_health() -> dict
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.common.config import get_settings
from src.common.schemas.market import CandleInterval
from src.mcp_servers.market_data_streamer.cache import MarketDataCache
from src.mcp_servers.market_data_streamer.calendar import MarketCalendar

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_UNIVERSE_PATH = REPO_ROOT / "config" / "nifty50_universe.json"

server = MCPServer("market-data-streamer")


def get_cache() -> MarketDataCache:
    settings = get_settings()
    return MarketDataCache(redis_url=settings.redis_url)


def get_calendar() -> MarketCalendar:
    return MarketCalendar()


@server.tool(
    name="get_latest_quote",
    description="Get the latest real-time or cached quote tick for an NSE NIFTY 50 symbol.",
)
def get_latest_quote(symbol: str) -> dict[str, Any]:
    """Return latest quote for a symbol."""
    cache = get_cache()
    clean_sym = symbol.strip().upper()
    if not clean_sym.endswith(".NS"):
        clean_sym = f"{clean_sym}.NS"

    tick = cache.get_latest_quote(clean_sym)
    if tick is None:
        return {
            "error": "QUOTE_NOT_FOUND",
            "symbol": clean_sym,
            "message": f"No recent cached quote tick found for symbol {clean_sym}.",
        }
    return tick.model_dump()


@server.tool(
    name="get_candles",
    description="Get OHLCV candlestick bars for a symbol and interval (1m, 5m, 15m, 1h, 1d).",
)
def get_candles(
    symbol: str,
    interval: str = "5m",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return historical candles for a symbol and interval."""
    cache = get_cache()
    clean_sym = symbol.strip().upper()
    if not clean_sym.endswith(".NS"):
        clean_sym = f"{clean_sym}.NS"

    try:
        cand_interval = CandleInterval(interval.lower())
    except ValueError:
        supported = [ci.value for ci in CandleInterval]
        return [
            {
                "error": "INVALID_INTERVAL",
                "message": f"Interval '{interval}' is invalid. Supported: {supported}",
            }
        ]

    candles = cache.get_candles(clean_sym, cand_interval, limit=min(500, max(1, limit)))
    return [c.model_dump() for c in candles]


@server.tool(
    name="get_market_status",
    description="Get NSE trading session phase (PRE_OPEN, OPEN, CLOSED) and holiday notes in IST.",
)
def get_market_status() -> dict[str, Any]:
    """Return current NSE market operating status."""
    cal = get_calendar()
    status = cal.evaluate_status()
    return status.model_dump()


@server.tool(
    name="list_instruments",
    description="List supported NSE NIFTY 50 constituents with as-of date and rebalance warning.",
)
def list_instruments() -> dict[str, Any]:
    """Return NIFTY 50 constituent universe."""
    if DEFAULT_UNIVERSE_PATH.exists():
        data: dict[str, Any] = json.loads(DEFAULT_UNIVERSE_PATH.read_text(encoding="utf-8"))
        return data
    return {
        "index_name": "NIFTY 50",
        "symbols": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
        "warning": "Default fallback constituents list.",
    }


@server.tool(
    name="get_feed_health",
    description="Get market data streamer ingestion health, ingested tick counts, and DLQ status.",
)
def get_feed_health() -> dict[str, Any]:
    """Return feed health metrics from Redis cache."""
    cache = get_cache()
    return cache.get_feed_health()


def main() -> None:
    """Run the MCP server over stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
