"""Technical Analyst Autonomous Agent.

Invokes MCP Server 2 (technical-analyst) to retrieve computed indicators and signals,
synthesizes regime assessment and technical context, and outputs TechnicalSignal.
Governance: ADR-004, ADR-011.
"""

from __future__ import annotations

import logging

from src.agents.base import AnalystRequest, AnalystResult, BaseAgent
from src.agents.mcp_client import MCPClientSession
from src.common.schemas.envelope import ComponentSource
from src.common.schemas.signals import MarketRegime, TechnicalSignal

logger = logging.getLogger("alphatrader.agents.analyst")


class AnalystAgent(BaseAgent):
    """Specialized agent wrapper for Technical Analyst MCP Server."""

    agent_name: str = "AnalystAgent"
    component_source: ComponentSource = ComponentSource.ANALYST_AGENT

    def __init__(self, mcp_client: MCPClientSession | None = None) -> None:
        super().__init__(mcp_client=mcp_client)

    def process(self, request: AnalystRequest) -> AnalystResult:
        """Process an analysis request deterministically via Server 2."""
        if self.mcp_client is None:
            return AnalystResult(
                success=False,
                signal=None,
                regime=None,
                explanation="MCP client session is not configured.",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code="NO_MCP_CLIENT",
            )

        # 1. Invoke get_indicators
        ind_resp = self.mcp_client.call_tool(
            server_name="technical-analyst",
            tool_name="get_indicators",
            arguments={"symbol": request.symbol, "interval": request.interval.value},
        )

        if "error" in ind_resp:
            err_code = ind_resp.get("error", "INDICATOR_ERROR")
            msg = ind_resp.get("message", "Failed to retrieve technical indicators.")
            return AnalystResult(
                success=False,
                signal=None,
                regime=None,
                explanation=f"Technical analyst server error: {msg}",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code=str(err_code),
            )

        # 2. Invoke get_signal
        sig_resp = self.mcp_client.call_tool(
            server_name="technical-analyst",
            tool_name="get_signal",
            arguments={"symbol": request.symbol, "interval": request.interval.value},
        )

        if "error" in sig_resp:
            err_code = sig_resp.get("error", "SIGNAL_ERROR")
            msg = sig_resp.get("message", "Failed to retrieve technical signal.")
            return AnalystResult(
                success=False,
                signal=None,
                regime=None,
                explanation=f"Technical analyst server error: {msg}",
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code=str(err_code),
            )

        # Parse regime
        regime_str = ind_resp.get("regime", "TRENDING_UP")
        try:
            regime = MarketRegime(regime_str)
        except ValueError:
            regime = MarketRegime.TRENDING_UP

        # Parse or construct TechnicalSignal from Server 2 response
        signal: TechnicalSignal | None = None
        if "signal_id" in sig_resp:
            try:
                signal = TechnicalSignal.model_validate(sig_resp)
            except Exception as e:
                logger.warning("Could not parse technical signal from response: %s", e)
        elif "signal" in sig_resp and isinstance(sig_resp["signal"], dict):
            try:
                signal = TechnicalSignal.model_validate(sig_resp["signal"])
            except Exception as e:
                logger.warning("Could not parse technical signal from nested response: %s", e)

        # Fallback explanation
        rsi_val = ind_resp.get("indicators", {}).get("rsi_14", "N/A")
        dir_val = signal.direction.value if signal else "NONE"
        explanation = (
            f"Analyst assessment for {request.symbol} ({request.interval.value}): "
            f"Regime={regime.value}, RSI={rsi_val}, Direction={dir_val}."
        )

        return AnalystResult(
            success=True,
            signal=signal,
            regime=regime,
            explanation=explanation,
            correlation_id=request.correlation_id,
            causation_id=request.trigger_event_id,
        )
