"""FastMCP Read-Only Server for Technical Analyst (MCP Python SDK).

Exposes 5 read-only tools to Analyst Agent and Orchestrator:
1. get_indicators(symbol: str, interval: str) -> dict
2. get_signal(symbol: str, interval: str) -> dict
3. explain_signal(symbol: str, interval: str) -> dict
4. list_supported_indicators() -> dict
5. get_analyzer_health() -> dict
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.common.config import get_settings
from src.common.schemas.market import CandleInterval
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RULES_CONFIG_PATH = REPO_ROOT / "config" / "technical_rules.json"

server = MCPServer("technical-analyst")


def get_cache() -> TechnicalAnalystCache:
    settings = get_settings()
    return TechnicalAnalystCache(redis_url=settings.redis_url)


def _clean_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym = f"{sym}.NS"
    return sym


@server.tool(
    name="get_indicators",
    description="Get latest computed indicators for an NSE symbol and timeframe interval.",
)
def get_indicators(symbol: str, interval: str = "5m") -> dict[str, Any]:
    """Retrieve computed indicator snapshot for symbol and interval."""
    cache = get_cache()
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {
            "error": "INVALID_INTERVAL",
            "message": f"Unsupported candle interval '{interval}'. Use '1m', '5m', '15m', or '1d'.",
        }

    indicators = cache.get_latest_indicators(clean_sym, inv)
    if indicators is None:
        return {
            "error": "INDICATORS_NOT_FOUND",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": f"No computed indicators found in cache for {clean_sym} on {inv.value}.",
        }

    return {
        "symbol": clean_sym,
        "interval": inv.value,
        "indicators": {k: str(v) for k, v in indicators.items()},
    }


@server.tool(
    name="get_signal",
    description="Get latest deterministic TechnicalSignal for an NSE symbol and interval.",
)
def get_signal(symbol: str, interval: str = "5m") -> dict[str, Any]:
    """Retrieve latest generated TechnicalSignal for symbol and interval."""
    cache = get_cache()
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {
            "error": "INVALID_INTERVAL",
            "message": f"Unsupported candle interval '{interval}'.",
        }

    signal = cache.get_latest_signal(clean_sym, inv)
    if signal is None:
        return {
            "error": "SIGNAL_NOT_FOUND",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": f"No technical signal available in cache for {clean_sym} on {inv.value}.",
        }

    return signal.model_dump()


@server.tool(
    name="explain_signal",
    description="Explain deterministic rules, indicator triggers, and score for a symbol's signal.",
)
def explain_signal(symbol: str, interval: str = "5m") -> dict[str, Any]:
    """Explain why a signal was emitted, showing rules fired and justification."""
    cache = get_cache()
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {
            "error": "INVALID_INTERVAL",
            "message": f"Unsupported candle interval '{interval}'.",
        }

    signal = cache.get_latest_signal(clean_sym, inv)
    if signal is None:
        return {
            "error": "SIGNAL_NOT_FOUND",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": f"No technical signal available to explain for {clean_sym} on {inv.value}.",
        }

    # Load rule metadata for human-readable explanation
    rule_descriptions: dict[str, str] = {}
    if RULES_CONFIG_PATH.exists():
        with open(RULES_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            for r in cfg.get("rules", []):
                rule_descriptions[r["name"]] = r.get("description", "")

    rule_details = [
        {"rule": r, "description": rule_descriptions.get(r, "Deterministic rule")}
        for r in signal.rules_fired
    ]

    conf_def = (
        "Rule-agreement fraction (agreed rules / total triggered rules). "
        "NOT a calibrated probability of profit."
    )

    return {
        "signal_id": str(signal.signal_id),
        "symbol": signal.symbol,
        "interval": signal.interval.value,
        "direction": signal.direction.value,
        "confidence": str(signal.confidence),
        "confidence_definition": conf_def,
        "regime": signal.regime.value,
        "evaluated_at": signal.evaluated_at.isoformat(),
        "config_version": signal.config_version,
        "rules_fired": rule_details,
        "justifying_indicators": {k: str(v) for k, v in signal.indicators.items()},
        "provenance": signal.provenance.model_dump() if signal.provenance else None,
    }


@server.tool(
    name="list_supported_indicators",
    description="List all supported indicators, parameters, and mathematical definitions.",
)
def list_supported_indicators() -> dict[str, Any]:
    """Return catalog of supported technical indicators."""
    return {
        "version": "1.0.0",
        "supported_indicators": [
            {
                "name": "SMA",
                "period": 20,
                "complexity": "O(1) streaming running sum",
                "formula": "sum(close_i)/N",
            },
            {
                "name": "EMA",
                "periods": [9, 21, 50],
                "complexity": "O(1) recursive alpha-smoothing",
                "formula": "alpha * close + (1-alpha) * prev_ema, alpha=2/(N+1)",
            },
            {
                "name": "RSI",
                "period": 14,
                "complexity": "O(1) Wilder smoothing",
                "formula": "100 - (100 / (1 + avg_gain / avg_loss))",
            },
            {
                "name": "MACD",
                "parameters": "Fast: 12, Slow: 26, Signal: 9",
                "complexity": "O(1) multi-EMA tracking",
                "formula": "Line=EMA12-EMA26; Signal=EMA9(MACD); Hist=MACD-Signal",
            },
            {
                "name": "Bollinger Bands",
                "period": 20,
                "num_std": 2.0,
                "complexity": "O(1) running sum of squares variance",
                "formula": "Middle=SMA20; Bands=Middle+/-2*std; %B=(Close-Lower)/(Upper-Lower)",
            },
            {
                "name": "ATR",
                "period": 14,
                "complexity": "O(1) Wilder smoothing of True Range",
                "formula": "TR = max(H-L, |H-C_prev|, |L-C_prev|)",
            },
            {
                "name": "Rolling VWAP",
                "reset": "Daily session open boundary",
                "complexity": "O(1) cumulative volume-price sum",
                "formula": "sum(TypicalPrice * Vol) / sum(Vol)",
            },
            {
                "name": "ADX",
                "period": 14,
                "complexity": "O(1) Wilder smoothed DX with +DI and -DI",
                "formula": "DX = 100 * |+DI - -DI| / (+DI + -DI)",
            },
            {
                "name": "Stochastic",
                "parameters": "%K: 14, Smooth %K: 3, Smooth %D: 3",
                "complexity": "O(1) rolling min/max window",
                "formula": "%K = 100 * (Close - Low_14) / (High_14 - Low_14)",
            },
        ],
    }


@server.tool(
    name="get_analyzer_health",
    description="Check Technical Analyst streaming worker heartbeat and cache connectivity.",
)
def get_analyzer_health() -> dict[str, Any]:
    """Retrieve operational health of Technical Analyst service."""
    cache = get_cache()
    health = cache.get_health()
    if health is None:
        return {
            "status": "OFFLINE",
            "message": "No heartbeat received from Technical Analyst streaming service in Redis.",
        }
    return health


def run_stdio() -> None:
    """Run FastMCP server on STDIO transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    run_stdio()
