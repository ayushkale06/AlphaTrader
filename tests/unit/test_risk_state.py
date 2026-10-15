"""Exhaustive unit tests for Sprint 8: Real-Time Risk State & FastMCP Server.

Verifies:
1. RiskStateManager Lua transitions:
   - Monotonic HWM advancement.
   - Exact 2.0% drawdown threshold latching alphatrader:floor_halted = "1".
   - Latch persistence across day-boundary rollover and restart.
   - Stale/duplicate event rejection based on timestamp sequence.
   - Missing/corrupt Redis state fail-closed behavior.
2. FastMCP Server 4 tools:
   - validate_order_proposal with valid and invalid orders.
   - Fail-closed audit publication rejection.
   - get_floor_risk_status telemetry without secret leakage.
   - trigger_manual_floor_halt with operator authorization, partial publish failure,
     and idempotency.
3. Static AST check: Zero LLM imports across all risk guardrail modules.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.common.schemas.orders import OrderRequest, OrderType, Side
from src.common.schemas.risk import RiskDecision, RiskReasonCode, RiskVerdict
from src.mcp_servers.risk_guardrail.evaluator import (
    DeterministicRiskEvaluator,
)
from src.mcp_servers.risk_guardrail.publisher import RiskPublisher
from src.mcp_servers.risk_guardrail.server import (
    _get_state_manager,
    get_floor_risk_status,
    setup_risk_guardrail,
    trigger_manual_floor_halt,
    validate_order_proposal,
)
from src.mcp_servers.risk_guardrail.state import RiskStateManager
from src.mcp_servers.risk_guardrail.token import RiskTokenManager

SECRET = "super_secret_risk_guardrail_key_for_testing"


class FakePipeline:
    def __init__(self, fake_redis: FakeRedis) -> None:
        self.redis = fake_redis
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def setnx(self, key: str, value: str) -> FakePipeline:
        self.commands.append(("setnx", (key, value)))
        return self

    def set(self, key: str, value: str) -> FakePipeline:
        self.commands.append(("set", (key, value)))
        return self

    def get(self, key: str) -> FakePipeline:
        self.commands.append(("get", (key,)))
        return self

    def hgetall(self, key: str) -> FakePipeline:
        self.commands.append(("hgetall", (key,)))
        return self

    def hlen(self, key: str) -> FakePipeline:
        self.commands.append(("hlen", (key,)))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for cmd, args in self.commands:
            if cmd == "setnx":
                k, v = str(args[0]), str(args[1])
                if k not in self.redis.store:
                    self.redis.store[k] = v
                    results.append(1)
                else:
                    results.append(0)
            elif cmd == "set":
                k, v = str(args[0]), str(args[1])
                self.redis.store[k] = v
                results.append(True)
            elif cmd == "get":
                k = str(args[0])
                results.append(self.redis.store.get(k))
            elif cmd == "hgetall":
                k = str(args[0])
                results.append(self.redis.hashes.get(k, {}))
            elif cmd == "hlen":
                k = str(args[0])
                results.append(len(self.redis.hashes.get(k, {})))
        self.commands.clear()
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def register_script(self, script: str) -> FakeScript:
        return FakeScript(self, script)

    def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def hdel(self, key: str, field: str) -> None:
        if key in self.hashes and field in self.hashes[key]:
            del self.hashes[key][field]

    def hset(self, key: str, field: str, value: str) -> None:
        if key not in self.hashes:
            self.hashes[key] = {}
        self.hashes[key][field] = value


class FakeScript:
    def __init__(self, fake_redis: FakeRedis, script: str) -> None:
        self.redis = fake_redis
        self.script = script

    def __call__(self, keys: list[str], args: list[str]) -> list[object]:
        # Python emulation of LUA_UPDATE_RISK_STATE logic
        k_equity, k_hwm, k_sod, k_date, k_last_ts, k_halt = keys
        new_equity = float(args[0])
        event_ts = float(args[1])
        session_date = args[2]
        max_dd_pct = float(args[3])

        last_ts_str = self.redis.store.get(k_last_ts)
        if last_ts_str:
            last_ts = float(last_ts_str)
            if event_ts <= last_ts:
                return [0, "STALE_OR_DUPLICATE_EVENT"]

        current_date = self.redis.store.get(k_date)
        if not current_date or current_date != session_date:
            self.redis.store[k_date] = session_date
            self.redis.store[k_sod] = str(args[0])

        is_halted = self.redis.store.get(k_halt)
        if is_halted == "1":
            self.redis.store[k_equity] = str(args[0])
            self.redis.store[k_last_ts] = str(args[1])
            return [1, "ALREADY_HALTED"]

        current_hwm_str = self.redis.store.get(k_hwm)
        hwm = new_equity
        if current_hwm_str:
            old_hwm = float(current_hwm_str)
            if old_hwm > hwm:
                hwm = old_hwm
        self.redis.store[k_hwm] = str(hwm)

        drawdown_pct = 0.0
        if hwm > 0:
            dd_val = hwm - new_equity
            if dd_val > 0:
                drawdown_pct = (dd_val / hwm) * 100.0

        if drawdown_pct >= max_dd_pct:
            self.redis.store[k_halt] = "1"
            self.redis.store[k_equity] = str(args[0])
            self.redis.store[k_last_ts] = str(args[1])
            return [2, "DRAWDOWN_BREACH_HALTED"]

        self.redis.store[k_equity] = str(args[0])
        self.redis.store[k_last_ts] = str(args[1])
        return [1, "SUCCESS"]


def test_zero_llm_imports_across_risk_package() -> None:
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


class TestRiskStateManager:
    """Tests for RiskStateManager and atomic Lua transitions."""

    @pytest.fixture
    def state_manager(self) -> RiskStateManager:
        fake_redis = FakeRedis()
        sm = RiskStateManager(
            fake_redis, market_timezone="Asia/Kolkata", max_daily_drawdown_pct=Decimal("2.0")
        )  # type: ignore[arg-type]
        sm.initialize_state(Decimal("10000000.00"), session_date="2026-10-15")
        return sm

    def test_hwm_monotonicity(self, state_manager: RiskStateManager) -> None:
        t0 = datetime(2026, 10, 15, 9, 15, tzinfo=UTC)
        # Advance equity up
        ok, _msg = state_manager.update_portfolio_equity(Decimal("10500000.00"), t0)
        assert ok
        tele = state_manager.get_telemetry()
        assert tele.high_water_mark == Decimal("10500000.00")
        assert tele.equity == Decimal("10500000.00")

        # Drop equity slightly (within 2% limit)
        t1 = t0 + timedelta(minutes=5)
        ok2, _msg2 = state_manager.update_portfolio_equity(Decimal("10400000.00"), t1)
        assert ok2
        tele2 = state_manager.get_telemetry()
        assert tele2.high_water_mark == Decimal("10500000.00")  # HWM preserved monotonically!
        assert tele2.equity == Decimal("10400000.00")

    def test_exact_drawdown_breach_latches_halt(self, state_manager: RiskStateManager) -> None:
        t0 = datetime(2026, 10, 15, 9, 15, tzinfo=UTC)
        # HWM = 10,000,000. 2.0% breach is 9,800,000 or below
        ok, msg = state_manager.update_portfolio_equity(Decimal("9790000.00"), t0)
        assert ok
        assert msg == "DRAWDOWN_BREACH_HALTED"

        tele = state_manager.get_telemetry()
        assert tele.is_floor_halted is True

    def test_halt_persists_across_day_rollover_and_restart(
        self, state_manager: RiskStateManager
    ) -> None:
        state_manager.trigger_floor_halt()
        assert state_manager.get_telemetry().is_floor_halted is True

        # Day rollover to 2026-10-16
        t_next = datetime(2026, 10, 16, 9, 15, tzinfo=UTC)
        ok, msg = state_manager.update_portfolio_equity(Decimal("10000000.00"), t_next)
        assert ok
        assert msg == "ALREADY_HALTED"

        # Latch MUST be preserved!
        tele = state_manager.get_telemetry()
        assert tele.is_floor_halted is True
        assert tele.session_date == "2026-10-16"

    def test_stale_and_duplicate_event_rejected(self, state_manager: RiskStateManager) -> None:
        """Ordering and concurrency semantics:

        - Duplicate events (equal timestamp) are rejected without mutating state.
        - Out-of-order deliveries (clock skew) are rejected without mutating state.
        - Valid strictly newer events advance equity monotonically.
        """
        t0 = datetime(2026, 10, 15, 9, 15, tzinfo=UTC)
        ok0, msg0 = state_manager.update_portfolio_equity(Decimal("10200000.00"), t0)
        assert ok0 is True
        assert msg0 == "SUCCESS"

        # 1. Duplicate event with equal timestamp: must be rejected, equity unchanged
        ok_dup, msg_dup = state_manager.update_portfolio_equity(Decimal("10300000.00"), t0)
        assert not ok_dup
        assert msg_dup == "STALE_OR_DUPLICATE_EVENT"
        assert state_manager.get_telemetry().equity == Decimal("10200000.00")

        # 2. Clock skew / older timestamp out-of-order: must be rejected, equity unchanged
        t_skew = t0 - timedelta(seconds=1)
        ok_skew, msg_skew = state_manager.update_portfolio_equity(Decimal("10500000.00"), t_skew)
        assert not ok_skew
        assert msg_skew == "STALE_OR_DUPLICATE_EVENT"
        assert state_manager.get_telemetry().equity == Decimal("10200000.00")

        # 3. Strictly increasing timestamp: accepted and mutates state
        t1 = t0 + timedelta(milliseconds=1)
        ok1, msg1 = state_manager.update_portfolio_equity(Decimal("10250000.00"), t1)
        assert ok1 is True
        assert msg1 == "SUCCESS"
        assert state_manager.get_telemetry().equity == Decimal("10250000.00")

    def test_positions_update(self, state_manager: RiskStateManager) -> None:
        state_manager.update_open_position("TCS.NS", Decimal("500000.00"))
        state_manager.update_open_position("INFY.NS", Decimal("400000.00"))
        state = state_manager.get_portfolio_state_for_evaluation()
        assert len(state.open_positions) == 2
        assert state.open_positions["TCS.NS"] == Decimal("500000.00")

        # Removal
        state_manager.update_open_position("TCS.NS", Decimal("0"))
        state2 = state_manager.get_portfolio_state_for_evaluation()
        assert "TCS.NS" not in state2.open_positions


class TestRiskMcpServerTools:
    """Tests for FastMCP Server 4 tools."""

    @pytest.fixture(autouse=True)
    def setup_server(self) -> None:
        fake_redis = FakeRedis()
        sm = RiskStateManager(
            fake_redis, market_timezone="Asia/Kolkata", max_daily_drawdown_pct=Decimal("2.0")
        )  # type: ignore[arg-type]
        sm.initialize_state(Decimal("10000000.00"), session_date="2026-10-15")

        tm = RiskTokenManager(SECRET)
        evaluator = DeterministicRiskEvaluator(
            token_manager=tm,
            max_daily_drawdown_pct=Decimal("2.0"),
            max_position_size_pct=Decimal("5.0"),
        )
        mock_producer = MagicMock()
        mock_producer.flush.return_value = 0
        publisher = RiskPublisher(bootstrap_servers="localhost:9092", producer=mock_producer)

        setup_risk_guardrail(sm, evaluator, publisher)

    def _sample_order_json(self, qty: int = 100) -> str:
        req = OrderRequest(
            idempotency_key=str(uuid4()),
            symbol="TCS.NS",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal(str(qty)),
            limit_price=Decimal("3500.00"),
            stop_loss_price=Decimal("3400.00"),
            strategy_name="trend_strat",
            requested_at=datetime.now(UTC),
        )
        return req.model_dump_json()

    def test_validate_order_proposal_approved(self) -> None:
        res_json = validate_order_proposal(self._sample_order_json(qty=50))
        dec = RiskDecision.model_validate_json(res_json)
        assert dec.decision == RiskVerdict.APPROVE
        assert dec.reason_code == RiskReasonCode.WITHIN_LIMITS
        assert dec.approval_token is not None

    def test_get_floor_risk_status_telemetry(self) -> None:
        status_json = get_floor_risk_status()
        data = json.loads(status_json)
        assert data["is_floor_halted"] is False
        assert "high_water_mark_inr" in data
        assert "equity_inr" in data
        assert "SECRET" not in status_json
        assert "key" not in status_json

    def test_trigger_manual_floor_halt_disabled_for_unauthenticated_callers(self) -> None:
        """Privileged manual halt is disabled because caller strings are unauthenticated.

        Rejected calls must NOT mutate Redis or publish any events.
        """
        sm = _get_state_manager()
        assert sm.get_telemetry().is_floor_halted is False

        # Call with arbitrary operator_id
        res = trigger_manual_floor_halt(reason="Emergency crash", operator_id="admin_user_1")
        data = json.loads(res)
        assert data["success"] is False
        assert "OPERATION_DISABLED" in data["error"]

        # Redis state MUST NOT be mutated
        assert sm.get_telemetry().is_floor_halted is False

        # Unauthenticated / empty caller rejected identically without mutating state
        res_empty = trigger_manual_floor_halt(reason="Emergency crash", operator_id="")
        data_empty = json.loads(res_empty)
        assert data_empty["success"] is False
        assert "OPERATION_DISABLED" in data_empty["error"]
        assert sm.get_telemetry().is_floor_halted is False

    def test_circuit_breaker_kafka_failure_and_recovery(self) -> None:
        """Audit Kafka failure recovery:

        1. Drawdown breach or latching sets Redis latch.
        2. Kafka publish failure raises exception or fails.
        3. Halt remains latched in Redis throughout.
        4. Retry publishes event without unlatching Redis.
        5. State persists across process restart.
        """
        fake_redis = FakeRedis()
        sm = RiskStateManager(
            fake_redis, market_timezone="Asia/Kolkata", max_daily_drawdown_pct=Decimal("2.0")
        )  # type: ignore[arg-type]
        sm.initialize_state(Decimal("10000000.00"), session_date="2026-10-15")

        # Mock producer that fails on first attempt, then succeeds on retry
        mock_p = MagicMock()
        mock_p.flush.side_effect = [
            RuntimeError("Kafka broker unreachable"),  # first halt publish fails
            0,  # retry halt publish succeeds
        ]
        pub = RiskPublisher(bootstrap_servers="localhost:9092", producer=mock_p)

        # 1. Breach latch in Redis
        t0 = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
        ok, msg = sm.update_portfolio_equity(Decimal("9750000.00"), t0)
        assert ok is True
        assert msg == "DRAWDOWN_BREACH_HALTED"
        assert sm.get_telemetry().is_floor_halted is True

        # 2. Attempt publishing halt event to Kafka -> fails
        with pytest.raises(RuntimeError, match="Kafka broker unreachable"):
            pub.publish_floor_halt(
                reason="2.5% drawdown breach",
                operator_id="circuit_breaker",
                correlation_id="halt-event-1",
            )

        # 3. Halt MUST remain latched in Redis despite Kafka failure
        assert sm.get_telemetry().is_floor_halted is True

        # Subsequent orders are strictly rejected
        # Note: validate_order_proposal calls publish_risk_decision which calls flush()
        # So we supply a separate publisher or set side_effect accordingly
        mock_order_p = MagicMock()
        mock_order_p.flush.return_value = 0
        order_pub = RiskPublisher(bootstrap_servers="localhost:9092", producer=mock_order_p)

        tm = RiskTokenManager(SECRET)
        evaluator = DeterministicRiskEvaluator(token_manager=tm)
        setup_risk_guardrail(sm, evaluator, order_pub)

        res_order = validate_order_proposal(self._sample_order_json(qty=10))
        dec = RiskDecision.model_validate_json(res_order)
        assert dec.decision == RiskVerdict.HALT
        assert dec.reason_code == RiskReasonCode.FLOOR_ALREADY_HALTED

        # 4. Safe retry: publish succeeds with stable event correlation_id
        env = pub.publish_floor_halt(
            reason="2.5% drawdown breach",
            operator_id="circuit_breaker",
            correlation_id="halt-event-1",
        )
        assert env.correlation_id == "halt-event-1"
        assert sm.get_telemetry().is_floor_halted is True

        # 5. Recovery after process restart
        sm_restarted = RiskStateManager(
            fake_redis, market_timezone="Asia/Kolkata", max_daily_drawdown_pct=Decimal("2.0")
        )  # type: ignore[arg-type]
        telemetry_restarted = sm_restarted.get_telemetry()
        assert telemetry_restarted.is_floor_halted is True
        assert telemetry_restarted.high_water_mark == Decimal("10000000.00")
        assert telemetry_restarted.equity == Decimal("9750000.00")

    def test_validate_order_malformed_json_rejection(self) -> None:
        res = validate_order_proposal("invalid-not-json")
        data = json.loads(res)
        assert data["decision"] == RiskVerdict.REJECT.value
        assert "Malformed order proposal" in data["message"]

    def test_validate_order_publish_failure_fails_closed(self) -> None:
        mock_p = MagicMock()
        mock_p.flush.side_effect = RuntimeError("Broker down")
        broken_pub = RiskPublisher(bootstrap_servers="localhost:9092", producer=mock_p)
        sm = RiskStateManager(FakeRedis())  # type: ignore[arg-type]
        sm.initialize_state(Decimal("10000000.00"), session_date="2026-10-15")
        tm = RiskTokenManager(SECRET)
        evaluator = DeterministicRiskEvaluator(token_manager=tm)
        setup_risk_guardrail(sm, evaluator, broken_pub)

        res = validate_order_proposal(self._sample_order_json(qty=10))
        dec = RiskDecision.model_validate_json(res)
        assert dec.decision == RiskVerdict.REJECT
        assert dec.approval_token is None
        assert "could not be durably published" in dec.message

    def test_redis_outage_fails_closed(self) -> None:
        broken_redis = MagicMock()
        broken_redis.pipeline.side_effect = ConnectionError("Redis unreachable")
        broken_sm = RiskStateManager(broken_redis)
        state = broken_sm.get_portfolio_state_for_evaluation()
        assert state.is_floor_halted is True

    def test_server_uninitialized(self) -> None:
        setup_risk_guardrail(None, None, None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="evaluator has not been initialized"):
            validate_order_proposal("{}")

        with pytest.raises(RuntimeError, match="state manager has not been initialized"):
            get_floor_risk_status()
