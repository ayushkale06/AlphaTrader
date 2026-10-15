"""FastMCP Read-Only Server for Quant Strategy Engine (MCP Python SDK).

Exposes the 6 exact required read-only tools:
1. list_strategies() -> dict
2. get_strategy_signal(symbol: str, strategy_name: str, interval: str) -> dict
3. get_target_portfolio() -> dict
4. get_backtest_report(report_id: str) -> dict
5. explain_strategy_signal(symbol: str, strategy_name: str, interval: str) -> dict
6. get_engine_health() -> dict

Plus the two analytical proposal tools:
7. evaluate_alpha_factors(symbol: str, interval: str) -> dict
8. propose_sized_order(symbol: str, strategy_name: str, interval: str) -> dict
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.common.config import get_settings
from src.common.schemas.market import CandleInterval
from src.common.schemas.signals import Direction
from src.mcp_servers.quant_strategy_engine.cache import QuantStrategyCache
from src.mcp_servers.quant_strategy_engine.evaluator import MultiFactorEvaluator
from src.mcp_servers.quant_strategy_engine.publisher import (
    INDEX_PATH,
)
from src.mcp_servers.quant_strategy_engine.sizing import PositionSizer
from src.mcp_servers.quant_strategy_engine.strategies import StrategyRegistry
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "quant_strategies.json"

server = MCPServer("quant-strategy-engine")

_registry: StrategyRegistry | None = None
_evaluator: MultiFactorEvaluator | None = None
_sizer: PositionSizer | None = None


def get_quant_cache() -> QuantStrategyCache:
    return QuantStrategyCache(redis_url=get_settings().redis_url)


def get_analyst_cache() -> TechnicalAnalystCache:
    return TechnicalAnalystCache(redis_url=get_settings().redis_url)


def _get_registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        _registry = StrategyRegistry(str(CONFIG_PATH) if CONFIG_PATH.exists() else None)
    return _registry


def _get_evaluator() -> MultiFactorEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = MultiFactorEvaluator()
    return _evaluator


def _get_sizer() -> PositionSizer:
    global _sizer
    if _sizer is None:
        _sizer = PositionSizer()
    return _sizer


def _clean_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not sym.endswith(".NS"):
        sym = f"{sym}.NS"
    return sym


# ==============================================================================
# Tool 1: list_strategies
# ==============================================================================
@server.tool(
    name="list_strategies",
    description="List all active deterministic quantitative strategies and versions.",
)
def list_strategies() -> dict[str, Any]:
    """Catalog of available deterministic quantitative strategies."""
    reg = _get_registry()
    return {
        "version": "1.0.0",
        "strategies": reg.list_strategies(),
    }


# ==============================================================================
# Tool 2: get_strategy_signal
# ==============================================================================
@server.tool(
    name="get_strategy_signal",
    description="Get latest QuantSignal proposal for a symbol, strategy, and interval.",
)
def get_strategy_signal(
    symbol: str, strategy_name: str = "ema_trend_following", interval: str = "5m"
) -> dict[str, Any]:
    """Retrieve the latest quantitative strategy signal proposal."""
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {"error": "INVALID_INTERVAL", "message": f"Unsupported interval '{interval}'"}

    cache = get_quant_cache()
    sig = cache.get_latest_signal(clean_sym, inv)
    if sig is None:
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "strategy_name": strategy_name,
            "message": f"Signal for {clean_sym} ({strategy_name}) is warming up or unavailable.",
        }

    return {
        "signal_id": str(sig.signal_id),
        "symbol": sig.symbol,
        "interval": sig.interval.value,
        "strategy_name": sig.strategy_name,
        "strategy_version": sig.strategy_version,
        "direction": sig.direction.value,
        "target_weight": str(sig.target_weight),
        "expected_net_edge_bps_per_trade": (
            str(sig.expected_net_edge_bps_per_trade)
            if sig.expected_net_edge_bps_per_trade is not None
            else None
        ),
        "edge_basis": sig.edge_basis.value if sig.edge_basis else None,
        "backtest_report_id": sig.backtest_report_id,
        "config_hash": sig.config_hash,
        "evaluated_at": sig.evaluated_at.isoformat(),
        "parameters": {k: str(v) for k, v in sig.parameters.items()},
    }


# ==============================================================================
# Tool 3: get_target_portfolio
# ==============================================================================
@server.tool(
    name="get_target_portfolio",
    description="Retrieve aggregate proposed target portfolio weights across symbols.",
)
def get_target_portfolio() -> dict[str, Any]:
    """Retrieve recommended portfolio asset weights."""
    cache = get_quant_cache()
    targets = cache.get_target_portfolio()
    if targets is None:
        return {
            "status": "EMPTY",
            "message": "No active target portfolio computed yet.",
            "target_weights": {},
            "cash_weight": "1.0000",
        }
    return targets


# ==============================================================================
# Tool 4: get_backtest_report
# ==============================================================================
@server.tool(
    name="get_backtest_report",
    description="Retrieve indexed backtest report metadata and validation status.",
)
def get_backtest_report(report_id: str) -> dict[str, Any]:
    """Retrieve and verify registered backtest report metadata."""
    if not INDEX_PATH.exists():
        return {"error": "INDEX_NOT_FOUND", "message": "docs/backtests/index.json does not exist."}

    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception as e:
        return {"error": "INDEX_CORRUPT", "message": f"Failed to parse index: {e}"}

    reports = idx.get("reports", {})
    if report_id not in reports:
        return {
            "error": "REPORT_NOT_FOUND",
            "report_id": report_id,
            "message": f"Report ID '{report_id}' is not registered in docs/backtests/index.json.",
        }

    meta = reports[report_id]
    is_oos = meta.get("edge_basis") == "OUT_OF_SAMPLE"

    # Distinguish full-sample / demo from true out-of-sample
    return {
        "report_id": report_id,
        "is_out_of_sample": is_oos,
        "edge_basis": meta.get("edge_basis"),
        "strategy_name": meta.get("strategy_name"),
        "strategy_version": meta.get("strategy_version"),
        "symbol": meta.get("symbol"),
        "date_range": meta.get("date_range"),
        "data_source": meta.get("data_source"),
        "path": meta.get("path"),
        "advisory": (
            "Verified OUT_OF_SAMPLE evidence"
            if is_oos
            else "Full-sample demo/in-sample only; CANNOT authorize expected edge claim."
        ),
    }


# ==============================================================================
# Tool 5: explain_strategy_signal
# ==============================================================================
@server.tool(
    name="explain_strategy_signal",
    description="Explain deterministic reasoning and factor inputs behind a strategy decision.",
)
def explain_strategy_signal(
    symbol: str, strategy_name: str = "ema_trend_following", interval: str = "5m"
) -> dict[str, Any]:
    """Explain reasoning behind a strategy proposal."""
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {"error": "INVALID_INTERVAL", "message": f"Unsupported interval '{interval}'"}

    reg = _get_registry()
    strat = reg.get(strategy_name)
    if strat is None:
        return {
            "error": "UNKNOWN_STRATEGY",
            "message": f"Strategy '{strategy_name}' not registered in quant engine.",
        }

    analyst_cache = get_analyst_cache()
    try:
        indicators = analyst_cache.get_latest_indicators(clean_sym, inv)
        tech_sig = analyst_cache.get_latest_signal(clean_sym, inv)
    except Exception:
        indicators = None
        tech_sig = None

    if indicators is None:
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": "Indicators are currently warming up or unavailable.",
        }

    return {
        "symbol": clean_sym,
        "strategy_name": strat.strategy_name,
        "strategy_version": strat.strategy_version,
        "config_hash": strat.config_hash,
        "indicators_available": {k: str(v) for k, v in indicators.items()},
        "technical_signal_direction": tech_sig.direction.value if tech_sig else None,
        "technical_signal_confidence": str(tech_sig.confidence) if tech_sig else None,
        "note": (
            "Strategies emit allocation target proposals only; pre-trade risk guardrail disposes."
        ),
    }


# ==============================================================================
# Tool 6: get_engine_health
# ==============================================================================
@server.tool(
    name="get_engine_health",
    description="Retrieve operational health and processed counts for the Quant Strategy Engine.",
)
def get_engine_health() -> dict[str, Any]:
    """Operational health monitor."""
    cache = get_quant_cache()
    h = cache.get_health()
    if h is None:
        return {
            "status": "HEALTHY",
            "processed_count": 0,
            "note": "Engine active; no heartbeat recorded in Redis yet.",
        }
    return h


# ==============================================================================
# Tool 7: evaluate_alpha_factors
# ==============================================================================
@server.tool(
    name="evaluate_alpha_factors",
    description="Deterministically evaluate alpha models (trend, mean reversion, breakout).",
)
def evaluate_alpha_factors(symbol: str, interval: str = "5m") -> dict[str, Any]:
    """Analytical multi-factor evaluation tool."""
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {"error": "INVALID_INTERVAL", "message": f"Unsupported interval '{interval}'"}

    analyst_cache = get_analyst_cache()
    try:
        indicators = analyst_cache.get_latest_indicators(clean_sym, inv)
    except Exception:
        indicators = None

    if indicators is None:
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": "Indicators are warming up; cannot evaluate alpha factors.",
        }

    close_val = indicators.get("close") or indicators.get("ema_9")
    if close_val is None or close_val <= Decimal("0"):
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": "Valid market close price unavailable; cannot evaluate alpha factors.",
        }

    from datetime import UTC, datetime, timedelta

    from src.common.schemas.market import Candle, DataProvenance, FeedMode

    now_utc = datetime.now(UTC)
    dummy_candle = Candle(
        symbol=clean_sym,
        interval=inv,
        open_time=now_utc - timedelta(minutes=5),
        close_time=now_utc,
        open=close_val,
        high=close_val,
        low=close_val,
        close=close_val,
        volume=Decimal("100"),
        vwap=close_val,
        trade_count=10,
        is_final=True,
        provenance=DataProvenance(provider="simulated", feed_mode=FeedMode.SIMULATED),
    )

    evaluator = _get_evaluator()
    res = evaluator.evaluate_factors(dummy_candle, indicators)

    return {
        "symbol": clean_sym,
        "direction": res.direction.value,
        "composite_score": str(res.composite_score),
        "factor_scores": {k: str(v) for k, v in res.factor_scores.items()},
        "factor_fired": res.factor_fired,
        "contributions": res.contributions,
        "conflict_resolution": res.conflict_resolution,
        "reasons": res.reasons,
    }


# ==============================================================================
# Tool 8: propose_sized_order
# ==============================================================================
@server.tool(
    name="propose_sized_order",
    description="Analytically calculate ATR brackets and proposed position sizing without trading.",
)
def propose_sized_order(
    symbol: str, strategy_name: str = "ema_trend_following", interval: str = "5m"
) -> dict[str, Any]:
    """Analytical position sizing proposal tool."""
    clean_sym = _clean_symbol(symbol)
    try:
        inv = CandleInterval(interval)
    except ValueError:
        return {"error": "INVALID_INTERVAL", "message": f"Unsupported interval '{interval}'"}

    analyst_cache = get_analyst_cache()
    try:
        indicators = analyst_cache.get_latest_indicators(clean_sym, inv)
    except Exception:
        indicators = None

    if indicators is None:
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": "Indicators warming up; cannot size proposal.",
        }

    close_val = indicators.get("close") or indicators.get("ema_9")
    atr_val = indicators.get("atr_14")

    if close_val is None or close_val <= Decimal("0"):
        return {
            "error": "NOT_READY",
            "status": "WARMING_UP",
            "symbol": clean_sym,
            "interval": inv.value,
            "message": "Valid market close price unavailable in indicators; cannot size proposal.",
        }

    sizer = _get_sizer()
    res = sizer.size_order(
        close_price=close_val,
        atr=atr_val,
        direction=Direction.LONG,
    )

    return {
        "symbol": clean_sym,
        "direction": res.direction.value,
        "proposed_target_weight": str(res.target_weight),
        "stop_loss_price": str(res.stop_loss_price) if res.stop_loss_price else None,
        "take_profit_price": str(res.take_profit_price) if res.take_profit_price else None,
        "risk_per_share": str(res.risk_amount_per_share) if res.risk_amount_per_share else None,
        "method": res.method,
        "reasons": res.reasons,
        "disclaimer": (
            "ANALYTICAL PROPOSAL ONLY: Does not submit orders or bypass risk guardrails."
        ),
    }


def main() -> None:
    """Run the MCP server over stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
