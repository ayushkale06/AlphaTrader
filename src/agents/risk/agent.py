"""Risk Guardrail Autonomous Agent.

Invokes MCP Server 4 (risk-guardrail) to evaluate pre-trade guardrails and retrieve
cryptographic HMAC RiskApprovalTokens without client-side forgery or override.
Governance: ADR-002, ADR-004, ADR-011.
"""

from __future__ import annotations

import logging

from src.agents.base import BaseAgent, RiskRequest, RiskResult
from src.agents.mcp_client import MCPClientSession
from src.common.schemas.envelope import ComponentSource
from src.common.schemas.risk import RiskDecision, RiskVerdict

logger = logging.getLogger("alphatrader.agents.risk")


class RiskAgent(BaseAgent):
    """Specialized agent wrapper for Risk Guardrail MCP Server."""

    agent_name: str = "RiskAgent"
    component_source: ComponentSource = ComponentSource.RISK_AGENT

    def __init__(self, mcp_client: MCPClientSession | None = None) -> None:
        super().__init__(mcp_client=mcp_client)

    def process(self, request: RiskRequest) -> RiskResult:
        """Validate order proposal deterministically via Server 4."""
        if self.mcp_client is None:
            return RiskResult(
                success=False,
                approved=False,
                risk_decision=None,
                approval_token=None,
                rejection_reasons=["MCP client session is not configured."],
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code="NO_MCP_CLIENT",
            )

        order_json = request.order_proposal.model_dump_json()

        # Invoke validate_order_proposal on Server 4
        resp = self.mcp_client.call_tool(
            server_name="risk-guardrail",
            tool_name="validate_order_proposal",
            arguments={"order_json": order_json},
        )

        if "error" in resp:
            err_code = resp.get("error", "RISK_SERVER_ERROR")
            msg = resp.get("message", "Error communicating with Risk Guardrail server.")
            return RiskResult(
                success=False,
                approved=False,
                risk_decision=None,
                approval_token=None,
                rejection_reasons=[str(msg)],
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code=str(err_code),
            )

        # Parse RiskDecision from response
        # Server 4 returns JSON string or dict of RiskDecision
        try:
            if "decision_id" in resp:
                decision = RiskDecision.model_validate(resp)
            elif "result" in resp and isinstance(resp["result"], dict):
                decision = RiskDecision.model_validate(resp["result"])
            elif "result" in resp and isinstance(resp["result"], str):
                decision = RiskDecision.model_validate_json(resp["result"])
            else:
                decision = RiskDecision.model_validate(resp)
        except Exception as e:
            logger.error("Failed to parse RiskDecision from server response: %s", e)
            return RiskResult(
                success=False,
                approved=False,
                risk_decision=None,
                approval_token=None,
                rejection_reasons=[f"Malformed RiskDecision from server: {e}"],
                correlation_id=request.correlation_id,
                causation_id=request.trigger_event_id,
                error_code="MALFORMED_DECISION",
            )

        is_approved = decision.decision == RiskVerdict.APPROVE
        reasons = [decision.reason_code.value] if decision.reason_code else []
        if decision.message:
            reasons.append(decision.message)

        return RiskResult(
            success=True,
            approved=is_approved,
            risk_decision=decision,
            approval_token=decision.approval_token,
            rejection_reasons=reasons,
            correlation_id=request.correlation_id,
            causation_id=request.trigger_event_id,
        )
