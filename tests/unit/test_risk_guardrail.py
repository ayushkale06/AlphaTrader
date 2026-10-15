"""Comprehensive unit tests for Sprint 7: Deterministic Risk Guardrail (Part 1).

Verifies:
1. Zero LLM imports across risk_guardrail modules (static AST check).
2. HMAC-SHA256 RiskTokenManager:
   - Cryptographic signing and deterministic verification.
   - Rejection of tampered signatures.
   - Rejection of expired tokens.
   - Rejection on parameter mismatch (order_request_id, symbol, side, quantity).
3. DeterministicRiskEvaluator invariants:
   - Floor halted rejects with HALT / FLOOR_ALREADY_HALTED.
   - Daily drawdown breach >= 2% rejects with HALT / MAX_DAILY_DRAWDOWN_BREACH.
   - Rate limiter trips when orders > 30 in trailing 60 seconds.
   - Missing/non-positive/inverted stop-loss rejects with MISSING_STOP_LOSS.
   - Out-of-bounds stop loss distance (< 0.1% or > 15%) rejects.
   - Single-position concentration > 5% rejects with MAX_POSITION_SIZE_BREACH.
   - Max open positions >= 10 rejects new symbols with MAX_OPEN_POSITIONS_BREACH.
   - Approves valid orders and issues cryptographically valid RiskApprovalToken.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.common.schemas.orders import OrderRequest, OrderType, Side
from src.common.schemas.risk import RiskReasonCode, RiskVerdict
from src.mcp_servers.risk_guardrail.evaluator import (
    DeterministicRiskEvaluator,
    PortfolioState,
)
from src.mcp_servers.risk_guardrail.token import RiskTokenManager

SECRET = "super_secret_risk_guardrail_key_for_testing"


def test_zero_llm_imports_static_ast_check() -> None:
    """Enforce strict non-LLM requirement via static AST parsing."""
    risk_dir = (
        Path(__file__).resolve().parent.parent.parent / "src" / "mcp_servers" / "risk_guardrail"
    )
    prohibited = {
        "openai",
        "anthropic",
        "langchain",
        "langgraph",
        "llama",
        "transformers",
        "google.generativeai",
    }

    for py_file in risk_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in prohibited, (
                        f"Prohibited LLM import '{alias.name}' in {py_file.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for p in prohibited:
                    assert not mod.startswith(p), (
                        f"Prohibited LLM import from '{mod}' in {py_file.name}"
                    )


class TestRiskTokenManager:
    """Cryptographic HMAC-SHA256 token manager tests."""

    def test_short_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 16 bytes"):
            RiskTokenManager("short")

    def test_issue_and_verify_token_success(self) -> None:
        tm = RiskTokenManager(SECRET, default_ttl_seconds=60)
        oid = uuid4()
        token = tm.issue_token(
            order_request_id=oid,
            symbol="RELIANCE.NS",
            side=Side.BUY,
            quantity=Decimal("100"),
            limit_price=Decimal("2500.00"),
        )
        assert isinstance(token, str)
        assert "." in token

        payload = tm.verify_token(
            token=token,
            expected_order_request_id=oid,
            expected_symbol="RELIANCE.NS",
            expected_side=Side.BUY,
            expected_quantity=Decimal("100"),
        )
        assert payload.symbol == "RELIANCE.NS"
        assert payload.quantity == "100"
        assert payload.limit_price == "2500.00"

    def test_tampered_signature_rejected(self) -> None:
        import base64

        tm = RiskTokenManager(SECRET)
        token = tm.issue_token(uuid4(), "TCS.NS", Side.BUY, Decimal("10"), Decimal("3500"))
        parts = token.split(".")
        # Provide a properly padded base64 string that is not the correct HMAC
        forged_sig = base64.urlsafe_b64encode(b"0" * 32).decode("ascii")
        tampered = f"{parts[0]}.{forged_sig}"
        with pytest.raises(ValueError, match="signature"):
            tm.verify_token(tampered)

    def test_expired_token_rejected(self) -> None:
        tm = RiskTokenManager(SECRET)
        past_time = datetime.now(UTC) - timedelta(seconds=10)
        token = tm.issue_token(
            uuid4(), "INFY.NS", Side.BUY, Decimal("10"), Decimal("1500"), expires_at=past_time
        )
        with pytest.raises(ValueError, match="expired"):
            tm.verify_token(token, check_expiration=True)

    def test_mismatched_payload_bindings_rejected(self) -> None:
        tm = RiskTokenManager(SECRET)
        oid = uuid4()
        token = tm.issue_token(oid, "TCS.NS", Side.BUY, Decimal("10"), Decimal("3500"))

        with pytest.raises(ValueError, match="symbol mismatch"):
            tm.verify_token(token, expected_symbol="INFY.NS")

        with pytest.raises(ValueError, match="side mismatch"):
            tm.verify_token(token, expected_side=Side.SELL)

        with pytest.raises(ValueError, match="quantity mismatch"):
            tm.verify_token(token, expected_quantity=Decimal("50"))

        with pytest.raises(ValueError, match="order_request_id mismatch"):
            tm.verify_token(token, expected_order_request_id=uuid4())


class TestDeterministicRiskEvaluator:
    """Pre-trade risk invariant tests."""

    @pytest.fixture
    def evaluator(self) -> DeterministicRiskEvaluator:
        tm = RiskTokenManager(SECRET)
        return DeterministicRiskEvaluator(
            token_manager=tm,
            max_daily_drawdown_pct=Decimal("2.0"),
            max_position_size_pct=Decimal("5.0"),
            max_open_positions=10,
            max_orders_per_minute=30,
        )

    @pytest.fixture
    def normal_portfolio(self) -> PortfolioState:
        return PortfolioState(
            equity=Decimal("10000000.00"),  # 1 Crore INR
            cash=Decimal("8000000.00"),
            high_water_mark=Decimal("10000000.00"),
            open_positions={"RELIANCE.NS": Decimal("400000.00")},
            is_floor_halted=False,
        )

    def _make_order(
        self,
        symbol: str = "TCS.NS",
        side: Side = Side.BUY,
        qty: int = 100,
        limit_p: Decimal = Decimal("3500.00"),
        stop_p: Decimal = Decimal("3400.00"),  # ~2.8% distance
    ) -> OrderRequest:
        return OrderRequest(
            idempotency_key=str(uuid4()),
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=Decimal(str(qty)),
            limit_price=limit_p,
            stop_loss_price=stop_p,
            strategy_name="trend_following",
            requested_at=datetime.now(UTC),
        )

    def test_approve_valid_order(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        order = self._make_order(
            symbol="TCS.NS", qty=100, limit_p=Decimal("3500"), stop_p=Decimal("3400")
        )
        decision = evaluator.evaluate_order(order, normal_portfolio)

        assert decision.decision == RiskVerdict.APPROVE
        assert decision.reason_code == RiskReasonCode.WITHIN_LIMITS
        assert decision.approval_token is not None

        # Verify issued token
        payload = evaluator.token_manager.verify_token(decision.approval_token)
        assert payload.symbol == "TCS.NS"
        assert payload.quantity == "100"

    def test_floor_halted_rejection(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        normal_portfolio.is_floor_halted = True
        order = self._make_order()
        decision = evaluator.evaluate_order(order, normal_portfolio)

        assert decision.decision == RiskVerdict.HALT
        assert decision.reason_code == RiskReasonCode.FLOOR_ALREADY_HALTED
        assert decision.approval_token is None

    def test_max_daily_drawdown_breach_halt(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        # 10M HWM, 9.75M equity => 2.5% drawdown (limit is 2.0%)
        normal_portfolio.equity = Decimal("9750000.00")
        order = self._make_order()
        decision = evaluator.evaluate_order(order, normal_portfolio)

        assert decision.decision == RiskVerdict.HALT
        assert decision.reason_code == RiskReasonCode.MAX_DAILY_DRAWDOWN_BREACH
        assert decision.approval_token is None

    def test_rate_limiter_breach(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        now = datetime.now(UTC)
        # Exhaust 30 orders
        for _ in range(30):
            ord_i = self._make_order(symbol="TCS.NS", qty=1)
            dec = evaluator.evaluate_order(ord_i, normal_portfolio, current_time=now)
            assert dec.decision == RiskVerdict.APPROVE

        # 31st order trips rate limit
        overflow_order = self._make_order(symbol="TCS.NS", qty=1)
        overflow_dec = evaluator.evaluate_order(overflow_order, normal_portfolio, current_time=now)
        assert overflow_dec.decision == RiskVerdict.REJECT
        assert overflow_dec.reason_code == RiskReasonCode.ORDER_RATE_LIMIT_EXCEEDED

    def test_stop_loss_distance_rules(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        # 1. Non-positive stop loss
        with pytest.raises(ValueError):
            self._make_order(stop_p=Decimal("-100"))

        # 2. Inverted stop loss for BUY (stop >= limit)
        ord_inverted = self._make_order(limit_p=Decimal("3500"), stop_p=Decimal("3600"))
        dec_inv = evaluator.evaluate_order(ord_inverted, normal_portfolio)
        assert dec_inv.decision == RiskVerdict.REJECT
        assert dec_inv.reason_code == RiskReasonCode.MISSING_STOP_LOSS

        # 3. Stop loss too close (< 0.1%)
        ord_too_close = self._make_order(limit_p=Decimal("3500"), stop_p=Decimal("3499"))  # 0.02%
        dec_close = evaluator.evaluate_order(ord_too_close, normal_portfolio)
        assert dec_close.decision == RiskVerdict.REJECT
        assert "out of allowable bounds" in dec_close.message

        # 4. Stop loss too wide (> 15%)
        ord_too_wide = self._make_order(limit_p=Decimal("3500"), stop_p=Decimal("2900"))  # ~17%
        dec_wide = evaluator.evaluate_order(ord_too_wide, normal_portfolio)
        assert dec_wide.decision == RiskVerdict.REJECT
        assert "out of allowable bounds" in dec_wide.message

    def test_position_concentration_breach(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        # Portfolio equity 10M, limit is 5% = 500,000 INR
        # Attempt to buy 200 shares @ 3500 = 700,000 INR (7% equity)
        huge_order = self._make_order(
            symbol="INFY.NS", qty=200, limit_p=Decimal("3500"), stop_p=Decimal("3400")
        )
        dec = evaluator.evaluate_order(huge_order, normal_portfolio)
        assert dec.decision == RiskVerdict.REJECT
        assert dec.reason_code == RiskReasonCode.MAX_POSITION_SIZE_BREACH

    def test_max_open_positions_breach(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        # Populate 10 open positions
        normal_portfolio.open_positions = {f"SYM{i}.NS": Decimal("100000") for i in range(10)}
        # Attempt to open 11th new symbol
        new_sym_order = self._make_order(
            symbol="NEW11.NS", qty=10, limit_p=Decimal("1000"), stop_p=Decimal("950")
        )
        dec = evaluator.evaluate_order(new_sym_order, normal_portfolio)
        assert dec.decision == RiskVerdict.REJECT
        assert dec.reason_code == RiskReasonCode.MAX_OPEN_POSITIONS_BREACH

    def test_sell_order_stop_loss_and_validation(
        self, evaluator: DeterministicRiskEvaluator, normal_portfolio: PortfolioState
    ) -> None:
        normal_portfolio.open_positions = {"TCS.NS": Decimal("1000000")}
        # 1. Valid SELL order (stop_loss above limit)
        sell_order = self._make_order(
            symbol="TCS.NS", side=Side.SELL, qty=50, limit_p=Decimal("3500"), stop_p=Decimal("3600")
        )
        dec = evaluator.evaluate_order(sell_order, normal_portfolio)
        assert dec.decision == RiskVerdict.APPROVE

        # 2. Inverted SELL stop loss (stop_loss <= limit)
        sell_inv = self._make_order(
            symbol="TCS.NS", side=Side.SELL, qty=50, limit_p=Decimal("3500"), stop_p=Decimal("3400")
        )
        dec_inv = evaluator.evaluate_order(sell_inv, normal_portfolio)
        assert dec_inv.decision == RiskVerdict.REJECT
        assert dec_inv.reason_code == RiskReasonCode.MISSING_STOP_LOSS

    def test_token_malformed_and_json_errors(self) -> None:
        tm = RiskTokenManager(SECRET)
        with pytest.raises(ValueError, match="Malformed"):
            tm.verify_token("invalid_token_without_dot")

        with pytest.raises(ValueError, match="Base64 decoding failed"):
            tm.verify_token("not_valid_b64.not_valid_b64")

        # Tampered JSON payload
        import base64
        import hashlib
        import hmac

        bad_json = b"not-json"
        b64_bad = base64.urlsafe_b64encode(bad_json).decode("ascii")
        sig = hmac.new(SECRET.encode("utf-8"), bad_json, hashlib.sha256).digest()
        b64_sig = base64.urlsafe_b64encode(sig).decode("ascii")
        with pytest.raises(ValueError, match="Invalid JSON payload"):
            tm.verify_token(f"{b64_bad}.{b64_sig}")
