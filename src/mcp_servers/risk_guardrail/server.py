"""FastMCP Server 4: Deterministic Risk Guardrail (Non-LLM).

Exposes:
1. validate_order_proposal: Evaluates candidate OrderRequest and publishes RiskDecision.
2. get_floor_risk_status: Returns read-only operational telemetry.
3. trigger_manual_floor_halt: Privileged emergency kill-switch (disabled pending trusted auth).
"""

from __future__ import annotations

import json
import logging

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from src.common.schemas.orders import OrderRequest
from src.common.schemas.risk import RiskDecision, RiskReasonCode, RiskVerdict
from src.mcp_servers.risk_guardrail.evaluator import DeterministicRiskEvaluator
from src.mcp_servers.risk_guardrail.publisher import RiskPublisher
from src.mcp_servers.risk_guardrail.state import RiskStateManager

logger = logging.getLogger("alphatrader.risk_guardrail.server")

mcp = MCPServer("risk-guardrail")

# Global instances initialized via setup_risk_guardrail
_state_manager: RiskStateManager | None = None
_evaluator: DeterministicRiskEvaluator | None = None
_publisher: RiskPublisher | None = None


def setup_risk_guardrail(
    state_manager: RiskStateManager,
    evaluator: DeterministicRiskEvaluator,
    publisher: RiskPublisher | None = None,
) -> None:
    """Inject dependencies into the FastMCP server."""
    global _state_manager, _evaluator, _publisher
    _state_manager = state_manager
    _evaluator = evaluator
    _publisher = publisher


def _get_evaluator() -> DeterministicRiskEvaluator:
    if _evaluator is None:
        msg = "Risk guardrail evaluator has not been initialized."
        raise RuntimeError(msg)
    return _evaluator


def _get_state_manager() -> RiskStateManager:
    if _state_manager is None:
        msg = "Risk state manager has not been initialized."
        raise RuntimeError(msg)
    return _state_manager


@mcp.tool()
def validate_order_proposal(order_json: str) -> str:
    """Validate a candidate OrderRequest against pre-trade risk invariants.

    Returns:
        JSON-serialized RiskDecision.
    """
    evaluator = _get_evaluator()
    state_manager = _get_state_manager()

    try:
        order_dict = json.loads(order_json)
        order = OrderRequest.model_validate(order_dict)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error("Failed to parse OrderRequest JSON: %s", e)
        # Construct fail-closed rejection
        return json.dumps(
            {
                "decision": RiskVerdict.REJECT.value,
                "reason_code": RiskReasonCode.MISSING_STOP_LOSS.value,
                "message": f"Malformed order proposal: {e}",
                "approval_token": None,
            }
        )

    # Fetch coherent state from Redis
    portfolio_state = state_manager.get_portfolio_state_for_evaluation()

    # Evaluate order proposal
    decision = evaluator.evaluate_order(order, portfolio_state)

    # Publish RiskDecision if publisher is configured
    if _publisher is not None:
        try:
            _publisher.publish_risk_decision(decision)
        except Exception as e:
            logger.error("Failed to publish RiskDecision: %s", e)
            # Failing closed: if audit record cannot be published, revoke approval
            if decision.decision == RiskVerdict.APPROVE:
                decision = RiskDecision(
                    order_request_id=order.order_request_id,
                    evaluated_at=decision.evaluated_at,
                    decision=RiskVerdict.REJECT,
                    rule_fired="RULE_AUDIT_PUBLISH_FAILED",
                    reason_code=RiskReasonCode.FLOOR_ALREADY_HALTED,
                    inputs_snapshot=decision.inputs_snapshot,
                    approval_token=None,
                    message=(
                        "Order rejected because risk audit record could not be durably published."
                    ),
                )

    return decision.model_dump_json()


@mcp.tool()
def get_floor_risk_status() -> str:
    """Fetch read-only operational telemetry of floor risk and drawdown.

    Never exposes secret keys or signing material.
    """
    state_manager = _get_state_manager()
    telemetry = state_manager.get_telemetry()
    return json.dumps(
        {
            "is_floor_halted": telemetry.is_floor_halted,
            "current_drawdown_pct": str(telemetry.current_drawdown_pct),
            "high_water_mark_inr": str(telemetry.high_water_mark),
            "equity_inr": str(telemetry.equity),
            "start_of_day_equity_inr": str(telemetry.start_of_day_equity),
            "session_date": telemetry.session_date,
            "open_positions_count": telemetry.open_positions_count,
        }
    )


@mcp.tool()
def trigger_manual_floor_halt(reason: str, operator_id: str) -> str:
    """Privileged emergency kill switch.

    DISABLED: Manual halt via caller-supplied string is disabled because MCP transport
    lacks trusted caller authentication. Rejected calls do not mutate Redis or publish events.
    """
    logger.warning(
        "Privileged manual halt rejected: caller-supplied operator_id %r is unauthenticated.",
        operator_id,
    )
    return json.dumps(
        {
            "success": False,
            "error": (
                "OPERATION_DISABLED: Manual floor halt is disabled pending trusted "
                "authentication architecture."
            ),
        }
    )
