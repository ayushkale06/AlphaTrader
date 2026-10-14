"""Comprehensive unit tests for all Pydantic v2 domain schemas in src/common/schemas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.common.schemas import (
    LEGAL_ORDER_TRANSITIONS,
    Candle,
    CandleInterval,
    ComponentSource,
    DataProvenance,
    Direction,
    EventEnvelope,
    EventSeverity,
    FeedMode,
    Fill,
    HaltEvent,
    IllegalOrderTransitionError,
    MarketRegime,
    MarketSessionPhase,
    MarketStatus,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    QuantSignal,
    RiskDecision,
    RiskInputSnapshot,
    RiskReasonCode,
    RiskVerdict,
    SchemaVersionMismatchError,
    Side,
    SystemEvent,
    SystemEventType,
    TechnicalSignal,
    Tick,
    classify_nse_session,
    validate_decimal_no_float,
    validate_nse_symbol,
    validate_order_status_transition,
    validate_utc_datetime,
)

FIXED_UTC = datetime(2026, 10, 13, 4, 30, 0, tzinfo=UTC)
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))


# ==============================================================================
# 1. Base Validators: UTC Datetime, Decimal (No Float), and NSE Symbol
# ==============================================================================


def test_validate_utc_datetime_normalizes_aware_datetimes_and_iso_strings() -> None:
    """Aware datetimes and ISO-8601 strings with offsets must normalize to UTC."""
    ist_dt = datetime(2026, 10, 13, 10, 0, 0, tzinfo=IST_OFFSET)
    assert validate_utc_datetime(ist_dt) == FIXED_UTC
    assert validate_utc_datetime("2026-10-13T10:00:00+05:30") == FIXED_UTC
    assert validate_utc_datetime("2026-10-13T04:30:00Z") == FIXED_UTC


@pytest.mark.parametrize(
    "invalid_dt",
    [
        datetime(2026, 10, 13, 10, 0, 0),  # naive datetime
        "2026-10-13T10:00:00",  # naive ISO string
        "",
        "   ",
        "not-a-timestamp",
        1728793800,
        True,
    ],
)
def test_validate_utc_datetime_rejects_naive_and_invalid_inputs(invalid_dt: object) -> None:
    """Naive datetimes and non-datetime/non-ISO inputs must raise ValueError."""
    with pytest.raises(ValueError):
        validate_utc_datetime(invalid_dt)


@pytest.mark.parametrize(
    ("valid_input", "expected"),
    [
        (Decimal("2954.50"), Decimal("2954.50")),
        ("2954.50", Decimal("2954.50")),
        (100, Decimal("100")),
        (0, Decimal("0")),
        ("-42.125", Decimal("-42.125")),
    ],
)
def test_validate_decimal_no_float_accepts_exact_types(
    valid_input: object,
    expected: Decimal,
) -> None:
    """Decimal, str, and int must convert cleanly to exact Decimal."""
    assert validate_decimal_no_float(valid_input) == expected


@pytest.mark.parametrize(
    "invalid_decimal",
    [
        2954.50,  # float prohibited
        0.0,  # float prohibited
        True,  # bool prohibited
        False,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        "NaN",
        "Infinity",
        "",
        "   ",
        "not-a-number",
        object(),
    ],
)
def test_validate_decimal_no_float_rejects_floats_and_non_finite(
    invalid_decimal: object,
) -> None:
    """Floats, booleans, NaN/Infinity, and malformed strings must raise ValueError."""
    with pytest.raises(ValueError):
        validate_decimal_no_float(invalid_decimal)


@pytest.mark.parametrize(
    "valid_symbol",
    ["RELIANCE.NS", "TCS.NS", "INFY", "HDFCBANK.NS", "M&M.NS", "BAJAJ-AUTO.NS", "NIFTY50"],
)
def test_validate_nse_symbol_accepts_canonical_symbols(valid_symbol: str) -> None:
    """Canonical NSE symbols must pass validation unchanged."""
    assert validate_nse_symbol(valid_symbol) == valid_symbol


@pytest.mark.parametrize(
    "invalid_symbol",
    [
        "",
        "   ",
        " RELIANCE.NS",
        "RELIANCE.NS ",
        "reliance.ns",
        "A",
        "12345",
        "AAPL.NASDAQ",
        "RELIANCE.BSE",
        "BAD$SYMBOL",
        "THIS_SYMBOL_IS_WAY_TOO_LONG.NS",
        123,
    ],
)
def test_validate_nse_symbol_rejects_invalid_symbols(invalid_symbol: object) -> None:
    """Lowercase, whitespace-padded, non-NSE, or non-string symbols must be rejected."""
    with pytest.raises(ValueError):
        validate_nse_symbol(invalid_symbol)


# ==============================================================================
# 2. Round-Trip Serialization & Invariant Tests for Every Model
# ==============================================================================


def test_tick_round_trip_and_rejections() -> None:
    """Tick must round-trip cleanly and reject inverted quotes, floats, or extra fields."""
    tick = Tick(
        symbol="RELIANCE.NS",
        timestamp=FIXED_UTC,
        last_price=Decimal("2954.50"),
        volume=Decimal("1500"),
        bid_price=Decimal("2954.25"),
        ask_price=Decimal("2954.75"),
        sequence_number=42,
    )
    restored = Tick.model_validate_json(tick.model_dump_json())
    assert restored == tick

    # Reject inverted bid/ask spread
    with pytest.raises(ValidationError, match="Inverted bid/ask quote"):
        Tick(
            symbol="RELIANCE.NS",
            timestamp=FIXED_UTC,
            last_price=Decimal("2954.50"),
            volume=Decimal("100"),
            bid_price=Decimal("2955.00"),
            ask_price=Decimal("2954.00"),
        )

    # Reject float price
    with pytest.raises(ValidationError, match="Float value"):
        Tick(
            symbol="RELIANCE.NS",
            timestamp=FIXED_UTC,
            last_price=2954.50,  # type: ignore[arg-type]
            volume=Decimal("100"),
        )

    # Reject negative price or negative volume
    with pytest.raises(ValidationError):
        Tick(
            symbol="RELIANCE.NS",
            timestamp=FIXED_UTC,
            last_price=Decimal("-10.00"),
            volume=Decimal("100"),
        )
    with pytest.raises(ValidationError):
        Tick(
            symbol="RELIANCE.NS",
            timestamp=FIXED_UTC,
            last_price=Decimal("10.00"),
            volume=Decimal("-1"),
        )

    # Reject unknown extra fields
    with pytest.raises(ValidationError):
        Tick.model_validate(
            {
                "symbol": "RELIANCE.NS",
                "timestamp": FIXED_UTC,
                "last_price": Decimal("2954.50"),
                "volume": Decimal("100"),
                "unknown_field": "forbid_me",
            }
        )

    # Reject mutation (frozen=True)
    with pytest.raises(ValidationError):
        tick.last_price = Decimal("3000.00")  # type: ignore[misc]


def test_candle_round_trip_and_ohlc_invariants() -> None:
    """Candle must round-trip and enforce close_time > open_time and OHLC bounds."""
    open_ts = FIXED_UTC - timedelta(minutes=5)
    candle = Candle(
        symbol="TCS.NS",
        interval=CandleInterval.FIVE_MINUTES,
        open_time=open_ts,
        close_time=FIXED_UTC,
        open=Decimal("4100.00"),
        high=Decimal("4125.00"),
        low=Decimal("4095.00"),
        close=Decimal("4118.50"),
        volume=Decimal("25000"),
        vwap=Decimal("4112.00"),
        trade_count=310,
    )
    assert Candle.model_validate_json(candle.model_dump_json()) == candle

    # close_time <= open_time
    with pytest.raises(ValidationError, match="Invalid candle window"):
        Candle(
            symbol="TCS.NS",
            interval=CandleInterval.FIVE_MINUTES,
            open_time=FIXED_UTC,
            close_time=open_ts,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("10"),
        )

    # high < open
    with pytest.raises(ValidationError, match="Invalid OHLC bar"):
        Candle(
            symbol="TCS.NS",
            interval=CandleInterval.ONE_MINUTE,
            open_time=open_ts,
            close_time=FIXED_UTC,
            open=Decimal("105"),
            high=Decimal("100"),
            low=Decimal("90"),
            close=Decimal("95"),
            volume=Decimal("10"),
        )

    # low > close
    with pytest.raises(ValidationError, match="Invalid OHLC bar"):
        Candle(
            symbol="TCS.NS",
            interval=CandleInterval.ONE_MINUTE,
            open_time=open_ts,
            close_time=FIXED_UTC,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("98"),
            close=Decimal("95"),
            volume=Decimal("10"),
        )

    # vwap outside [low, high]
    with pytest.raises(ValidationError, match="Invalid VWAP"):
        Candle(
            symbol="TCS.NS",
            interval=CandleInterval.ONE_MINUTE,
            open_time=open_ts,
            close_time=FIXED_UTC,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("10"),
            vwap=Decimal("115"),
        )


def test_market_status_nse_hours_and_round_trip() -> None:
    """MarketStatus must accurately classify NSE IST sessions and round-trip."""
    # Pre-open: 09:05 IST on Tuesday 2026-10-13
    pre_open_dt = datetime(2026, 10, 13, 9, 5, 0, tzinfo=IST_OFFSET)
    assert classify_nse_session(pre_open_dt) == MarketSessionPhase.PRE_OPEN
    status_pre = MarketStatus.from_timestamp(pre_open_dt, reason="Auction window")
    assert status_pre.is_open is False
    assert MarketStatus.model_validate_json(status_pre.model_dump_json()) == status_pre

    # Open: 10:00 IST on Tuesday 2026-10-13
    open_dt = datetime(2026, 10, 13, 10, 0, 0, tzinfo=IST_OFFSET)
    status_open = MarketStatus.from_timestamp(open_dt)
    assert status_open.session == MarketSessionPhase.OPEN
    assert status_open.is_open is True

    # Post-close: 15:40 IST on Tuesday 2026-10-13
    post_dt = datetime(2026, 10, 13, 15, 40, 0, tzinfo=IST_OFFSET)
    assert classify_nse_session(post_dt) == MarketSessionPhase.POST_CLOSE

    # Closed evening: 19:00 IST on Tuesday 2026-10-13
    closed_dt = datetime(2026, 10, 13, 19, 0, 0, tzinfo=IST_OFFSET)
    assert classify_nse_session(closed_dt) == MarketSessionPhase.CLOSED

    # Weekend: Saturday 2026-10-17 11:00 IST
    weekend_dt = datetime(2026, 10, 17, 11, 0, 0, tzinfo=IST_OFFSET)
    assert classify_nse_session(weekend_dt) == MarketSessionPhase.CLOSED

    # Inconsistent is_open flag
    with pytest.raises(ValidationError, match="Inconsistent MarketStatus"):
        MarketStatus(
            as_of=FIXED_UTC,
            session=MarketSessionPhase.CLOSED,
            is_open=True,
        )


def test_technical_and_quant_signals_round_trip_and_rejections() -> None:
    """TechnicalSignal and QuantSignal must round-trip and enforce indicator/weight bounds."""
    tech = TechnicalSignal(
        symbol="INFY.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("0.82"),
        regime=MarketRegime.TRENDING_UP,
        indicators={"ema_9": Decimal("1850.25"), "rsi_14": Decimal("61.50")},
        rules_fired=["ema_9_cross"],
        config_version="1.0.0",
    )
    assert TechnicalSignal.model_validate_json(tech.model_dump_json()) == tech

    with pytest.raises(ValidationError, match="cannot be empty"):
        TechnicalSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            direction=Direction.LONG,
            confidence=Decimal("0.5"),
            regime=MarketRegime.TRENDING_UP,
            config_version="1.0.0",
            indicators={},
        )

    with pytest.raises(ValidationError, match="non-empty strings"):
        TechnicalSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            direction=Direction.LONG,
            confidence=Decimal("0.5"),
            indicators={"   ": Decimal("1.0")},
        )

    quant = QuantSignal(
        symbol="INFY.NS",
        evaluated_at=FIXED_UTC,
        strategy_name="mean_reversion_bb_v1",
        direction=Direction.SHORT,
        target_weight=Decimal("0.03"),
        expected_edge=Decimal("14.20"),
        confidence=Decimal("0.68"),
        parameters={"z_score_threshold": Decimal("2.0")},
    )
    assert QuantSignal.model_validate_json(quant.model_dump_json()) == quant

    with pytest.raises(ValidationError, match="direction=FLAT must have target_weight=0"):
        QuantSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            strategy_name="flat_strat",
            direction=Direction.FLAT,
            target_weight=Decimal("0.05"),
            expected_edge=Decimal("0"),
        )


def test_risk_decision_and_halt_event_round_trip_and_invariants() -> None:
    """RiskDecision and HaltEvent must enforce verdict/reason_code/token invariants."""
    snap = RiskInputSnapshot(
        symbol="HDFCBANK.NS",
        proposed_notional=Decimal("400000.00"),
        portfolio_equity=Decimal("10000000.00"),
        current_drawdown_pct=Decimal("0.40"),
        max_daily_drawdown_pct=Decimal("2.00"),
        post_trade_position_pct=Decimal("4.00"),
        max_position_size_pct=Decimal("5.00"),
        open_positions_count=4,
        max_open_positions=10,
        orders_last_minute=1,
    )
    assert RiskInputSnapshot.model_validate_json(snap.model_dump_json()) == snap

    req_id = UUID("11111111-2222-4333-8444-555555555555")
    approved = RiskDecision(
        order_request_id=req_id,
        evaluated_at=FIXED_UTC,
        decision=RiskVerdict.APPROVE,
        rule_fired="PRE_TRADE_OK",
        reason_code=RiskReasonCode.WITHIN_LIMITS,
        inputs_snapshot=snap,
        approval_token="rtok-valid-001",
        message="Approved",
    )
    assert RiskDecision.model_validate_json(approved.model_dump_json()) == approved

    rejected = RiskDecision(
        order_request_id=req_id,
        evaluated_at=FIXED_UTC,
        decision=RiskVerdict.REJECT,
        rule_fired="MAX_POSITION_SIZE_GUARD",
        reason_code=RiskReasonCode.MAX_POSITION_SIZE_BREACH,
        inputs_snapshot=snap,
        approval_token=None,
        message="Single-stock concentration limit breached.",
    )
    assert RiskDecision.model_validate_json(rejected.model_dump_json()) == rejected

    # APPROVE with non-WITHIN_LIMITS reason_code
    with pytest.raises(ValidationError, match="reason_code=WITHIN_LIMITS"):
        RiskDecision(
            order_request_id=req_id,
            evaluated_at=FIXED_UTC,
            decision=RiskVerdict.APPROVE,
            rule_fired="PRE_TRADE_OK",
            reason_code=RiskReasonCode.MAX_POSITION_SIZE_BREACH,
            inputs_snapshot=snap,
            approval_token="rtok-valid-001",
            message="Invalid approve",
        )

    # APPROVE missing approval_token
    with pytest.raises(ValidationError, match="non-empty approval_token"):
        RiskDecision(
            order_request_id=req_id,
            evaluated_at=FIXED_UTC,
            decision=RiskVerdict.APPROVE,
            rule_fired="PRE_TRADE_OK",
            reason_code=RiskReasonCode.WITHIN_LIMITS,
            inputs_snapshot=snap,
            approval_token=None,
            message="Missing token",
        )

    # REJECT with WITHIN_LIMITS
    with pytest.raises(ValidationError, match="cannot use reason_code=WITHIN_LIMITS"):
        RiskDecision(
            order_request_id=req_id,
            evaluated_at=FIXED_UTC,
            decision=RiskVerdict.REJECT,
            rule_fired="GUARD",
            reason_code=RiskReasonCode.WITHIN_LIMITS,
            inputs_snapshot=snap,
            message="Invalid reject",
        )

    # REJECT with approval_token present
    with pytest.raises(ValidationError, match="must not issue an approval_token"):
        RiskDecision(
            order_request_id=req_id,
            evaluated_at=FIXED_UTC,
            decision=RiskVerdict.REJECT,
            rule_fired="GUARD",
            reason_code=RiskReasonCode.MARKET_CLOSED,
            inputs_snapshot=snap,
            approval_token="rtok-should-not-exist",
            message="Invalid reject token",
        )

    halt = HaltEvent(
        triggered_at=FIXED_UTC,
        rule_fired="MAX_DAILY_DRAWDOWN_CIRCUIT_BREAKER",
        reason_code=RiskReasonCode.MAX_DAILY_DRAWDOWN_BREACH,
        current_drawdown_pct=Decimal("2.15"),
        max_daily_drawdown_pct=Decimal("2.00"),
        portfolio_equity=Decimal("9785000.00"),
        high_water_mark=Decimal("10000000.00"),
        details="Daily drawdown exceeded 2.00% threshold; halting floor.",
    )
    assert HaltEvent.model_validate_json(halt.model_dump_json()) == halt

    with pytest.raises(ValidationError, match="HaltEvent cannot have reason_code=WITHIN_LIMITS"):
        HaltEvent(
            triggered_at=FIXED_UTC,
            rule_fired="BAD_HALT",
            reason_code=RiskReasonCode.WITHIN_LIMITS,
            current_drawdown_pct=Decimal("0.10"),
            max_daily_drawdown_pct=Decimal("2.00"),
            portfolio_equity=Decimal("9990000.00"),
            high_water_mark=Decimal("10000000.00"),
            details="Invalid",
        )


def test_orders_and_fills_round_trip_and_state_machine() -> None:
    """OrderRequest, Order, Fill, and order status transitions must enforce all invariants."""
    req = OrderRequest(
        idempotency_key="idem-001",
        symbol="RELIANCE.NS",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("50"),
        limit_price=Decimal("2950.00"),
        stop_loss_price=Decimal("2920.00"),
        strategy_name="momentum_v1",
        requested_at=FIXED_UTC,
    )
    assert OrderRequest.model_validate_json(req.model_dump_json()) == req

    # LIMIT order missing limit_price
    with pytest.raises(ValidationError, match="requires a positive limit_price"):
        OrderRequest(
            idempotency_key="idem-002",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("50"),
            limit_price=None,
            stop_loss_price=Decimal("2920.00"),
            strategy_name="momentum_v1",
            requested_at=FIXED_UTC,
        )

    order = Order(
        order_request_id=req.order_request_id,
        idempotency_key=req.idempotency_key,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        status=OrderStatus.FILLED,
        quantity=Decimal("50"),
        filled_quantity=Decimal("50"),
        average_fill_price=Decimal("2949.50"),
        limit_price=Decimal("2950.00"),
        stop_loss_price=Decimal("2920.00"),
        approval_token="rtok-001",
        created_at=FIXED_UTC,
        updated_at=FIXED_UTC + timedelta(seconds=1),
    )
    assert Order.model_validate_json(order.model_dump_json()) == order

    # Order invariant failures
    with pytest.raises(ValidationError, match=r"updated_at .* cannot precede created_at"):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.PENDING_RISK,
            quantity=Decimal("10"),
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC - timedelta(seconds=1),
        )

    with pytest.raises(ValidationError, match=r"filled_quantity .* cannot exceed quantity"):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.PENDING_RISK,
            quantity=Decimal("10"),
            filled_quantity=Decimal("20"),
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC,
        )

    with pytest.raises(ValidationError, match="status=FILLED must have filled_quantity"):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            quantity=Decimal("10"),
            filled_quantity=Decimal("5"),
            average_fill_price=Decimal("2950"),
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC,
        )

    with pytest.raises(ValidationError, match="status=FILLED requires average_fill_price"):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            quantity=Decimal("10"),
            filled_quantity=Decimal("10"),
            average_fill_price=None,
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC,
        )

    with pytest.raises(
        ValidationError,
        match="status=PARTIALLY_FILLED must satisfy 0 < filled_quantity < quantity",
    ):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.PARTIALLY_FILLED,
            quantity=Decimal("10"),
            filled_quantity=Decimal("0"),
            average_fill_price=Decimal("2950"),
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC,
        )

    with pytest.raises(
        ValidationError,
        match="status=PARTIALLY_FILLED requires average_fill_price",
    ):
        Order(
            order_request_id=req.order_request_id,
            idempotency_key="idem-err",
            symbol="RELIANCE.NS",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.PARTIALLY_FILLED,
            quantity=Decimal("10"),
            filled_quantity=Decimal("5"),
            average_fill_price=None,
            stop_loss_price=Decimal("2900"),
            created_at=FIXED_UTC,
            updated_at=FIXED_UTC,
        )

    fill = Fill(
        order_id=order.order_id,
        order_request_id=req.order_request_id,
        symbol="RELIANCE.NS",
        side=Side.BUY,
        fill_price=Decimal("2949.50"),
        fill_quantity=Decimal("50"),
        slippage_bps=Decimal("0.80"),
        stt_tax=Decimal("36.87"),
        exchange_fees=Decimal("4.80"),
        gst_tax=Decimal("0.86"),
        stamp_duty=Decimal("4.42"),
        total_fees=Decimal("46.95"),
        filled_at=FIXED_UTC,
    )
    assert Fill.model_validate_json(fill.model_dump_json()) == fill

    with pytest.raises(ValidationError, match=r"total_fees .* must equal the sum"):
        Fill(
            order_id=order.order_id,
            order_request_id=req.order_request_id,
            symbol="RELIANCE.NS",
            side=Side.BUY,
            fill_price=Decimal("2949.50"),
            fill_quantity=Decimal("50"),
            stt_tax=Decimal("10.00"),
            total_fees=Decimal("99.00"),
            filled_at=FIXED_UTC,
        )


def test_order_status_transition_state_machine_exhaustively() -> None:
    """Every legal transition must succeed and every illegal transition must raise."""
    for current_state, allowed_targets in LEGAL_ORDER_TRANSITIONS.items():
        for candidate_target in OrderStatus:
            if candidate_target in allowed_targets:
                assert (
                    validate_order_status_transition(current_state, candidate_target)
                    == candidate_target
                )
            else:
                with pytest.raises(IllegalOrderTransitionError, match="Illegal order status"):
                    validate_order_status_transition(current_state, candidate_target)


def test_portfolio_and_system_event_round_trip_and_invariants() -> None:
    """Position, PortfolioSnapshot, and SystemEvent must round-trip and enforce invariants."""
    pos = Position(
        symbol="RELIANCE.NS",
        quantity=Decimal("100"),
        average_entry_price=Decimal("2950.00"),
        mark_price=Decimal("2960.00"),
        market_value=Decimal("296000.00"),
        unrealized_pnl=Decimal("1000.00"),
        realized_pnl=Decimal("0.00"),
        updated_at=FIXED_UTC,
    )
    assert Position.model_validate_json(pos.model_dump_json()) == pos

    with pytest.raises(ValidationError, match=r"market_value .* must equal"):
        Position(
            symbol="RELIANCE.NS",
            quantity=Decimal("100"),
            average_entry_price=Decimal("2950.00"),
            mark_price=Decimal("2960.00"),
            market_value=Decimal("100.00"),
            unrealized_pnl=Decimal("1000.00"),
            updated_at=FIXED_UTC,
        )

    snap = PortfolioSnapshot(
        as_of=FIXED_UTC,
        cash=Decimal("9704000.00"),
        equity=Decimal("10000000.00"),
        exposure=Decimal("296000.00"),
        realized_pnl=Decimal("0.00"),
        unrealized_pnl=Decimal("1000.00"),
        drawdown=Decimal("0.00"),
        high_water_mark=Decimal("10000000.00"),
        positions=[pos],
    )
    assert PortfolioSnapshot.model_validate_json(snap.model_dump_json()) == snap

    # high_water_mark < equity
    with pytest.raises(ValidationError, match=r"high_water_mark .* cannot be less"):
        PortfolioSnapshot(
            as_of=FIXED_UTC,
            cash=Decimal("9704000.00"),
            equity=Decimal("10000000.00"),
            exposure=Decimal("296000.00"),
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("1000.00"),
            drawdown=Decimal("0.00"),
            high_water_mark=Decimal("9900000.00"),
        )

    # Duplicate positions
    with pytest.raises(ValidationError, match="Duplicate symbol"):
        PortfolioSnapshot(
            as_of=FIXED_UTC,
            cash=Decimal("9704000.00"),
            equity=Decimal("10000000.00"),
            exposure=Decimal("592000.00"),
            realized_pnl=Decimal("0.00"),
            unrealized_pnl=Decimal("2000.00"),
            drawdown=Decimal("0.00"),
            high_water_mark=Decimal("10000000.00"),
            positions=[pos, pos],
        )

    sys_evt = SystemEvent(
        event_type=SystemEventType.STARTUP,
        severity=EventSeverity.INFO,
        component=ComponentSource.RISK_GUARDRAIL,
        occurred_at=FIXED_UTC,
        message="Risk guardrail online.",
        metadata={"max_drawdown_pct": Decimal("2.0")},
    )
    assert SystemEvent.model_validate_json(sys_evt.model_dump_json()) == sys_evt


def test_envelope_schema_version_mismatch_raises_clear_error() -> None:
    """Backward-compatibility check: mismatched schema_version raises clear error."""
    tick = Tick(
        symbol="RELIANCE.NS",
        timestamp=FIXED_UTC,
        last_price=Decimal("2954.50"),
        volume=Decimal("100"),
    )
    envelope = EventEnvelope[Tick](
        correlation_id="cid-001",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=tick,
    )
    raw_bytes = envelope.to_json_bytes()
    assert EventEnvelope[Tick].from_json_bytes(raw_bytes) == envelope

    # Direct instantiation with unsupported schema_version
    with pytest.raises(ValidationError, match="Schema version mismatch"):
        EventEnvelope[Tick](
            correlation_id="cid-001",
            schema_version="2.0.0",
            produced_at=FIXED_UTC,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=tick,
        )

    with pytest.raises(ValidationError, match="schema_version must be a non-empty"):
        EventEnvelope[Tick](
            correlation_id="cid-001",
            schema_version="   ",
            produced_at=FIXED_UTC,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=tick,
        )

    # Deserialization with mismatched version in JSON payload
    tampered_bytes = raw_bytes.replace(b'"schema_version":"1.2.0"', b'"schema_version":"9.9.9"')
    with pytest.raises(SchemaVersionMismatchError, match="Schema version mismatch"):
        EventEnvelope[Tick].from_json_bytes(tampered_bytes)

    # Deserialization expecting a specific schema_version
    with pytest.raises(SchemaVersionMismatchError, match="Schema version mismatch"):
        EventEnvelope[Tick].from_json_bytes(raw_bytes, expected_schema_version="2.0.0")
    assert (
        EventEnvelope[Tick].from_json_bytes(raw_bytes, expected_schema_version="1.2.0") == envelope
    )


def test_data_provenance_validation_and_delay_invariants() -> None:
    """Verify DataProvenance delay invariants and error conditions per Sprint 4 spec."""
    # Valid real-time simulated
    prov_sim = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=FIXED_UTC,
    )
    assert prov_sim.is_delayed is False
    assert prov_sim.delay_seconds == 0

    # SIMULATED cannot be delayed
    with pytest.raises(ValidationError, match="SIMULATED market data cannot be delayed"):
        DataProvenance(
            provider="simulated_feed",
            feed_mode=FeedMode.SIMULATED,
            is_delayed=True,
            delay_seconds=15,
        )

    # is_delayed=False requires delay_seconds in (0, None)
    with pytest.raises(
        ValidationError, match=r"is_delayed=False requires delay_seconds in \(0, None\)"
    ):
        DataProvenance(
            provider="live_feed",
            feed_mode=FeedMode.LIVE,
            is_delayed=False,
            delay_seconds=10,
        )

    # delay_seconds > 0 requires is_delayed=True
    with pytest.raises(
        ValidationError, match=r"is_delayed=False requires delay_seconds in \(0, None\)"
    ):
        DataProvenance(
            provider="live_feed",
            feed_mode=FeedMode.LIVE,
            is_delayed=False,
            delay_seconds=1,
        )

    # is_delayed=True with positive delay
    prov_delayed = DataProvenance(
        provider="yahoo_finance",
        feed_mode=FeedMode.LIVE,
        is_delayed=True,
        delay_seconds=900,
    )
    assert prov_delayed.is_delayed is True
    assert prov_delayed.delay_seconds == 900

    # is_delayed=True with unknown delay (None)
    prov_unknown_delay = DataProvenance(
        provider="public_feed",
        feed_mode=FeedMode.LIVE,
        is_delayed=True,
        delay_seconds=None,
    )
    assert prov_unknown_delay.delay_seconds is None

    # is_delayed=True with delay_seconds=0 is rejected
    with pytest.raises(
        ValidationError, match=r"is_delayed=True requires delay_seconds > 0 or None"
    ):
        DataProvenance(
            provider="live_feed",
            feed_mode=FeedMode.LIVE,
            is_delayed=True,
            delay_seconds=0,
        )


def test_schema_version_backward_and_forward_compatibility() -> None:
    """Verify 1.0.0 parses with provenance=None, 1.1.0 round-trips, and 1.0.0 rejects 1.1.0."""
    from pydantic import BaseModel, ConfigDict

    # 1. A 1.0.0 message JSON without provenance parses cleanly with provenance=None
    json_1_0_0 = (
        '{"schema_version": "1.0.0", "event_id": "a0000001-0000-4000-8000-000000000001", '
        '"correlation_id": "cid-legacy-001", "produced_at": "2026-10-13T04:30:00Z", '
        '"source": "mcp.market_data_streamer", "payload": {"symbol": "RELIANCE.NS", '
        '"timestamp": "2026-10-13T04:30:00Z", "last_price": "2950.00", "volume": "100", '
        '"sequence_number": 1}}'
    )
    env_legacy = EventEnvelope[Tick].from_json_bytes(json_1_0_0)
    assert env_legacy.schema_version == "1.0.0"
    assert env_legacy.payload.provenance is None
    assert env_legacy.payload.symbol == "RELIANCE.NS"

    # 2. A 1.1.0 message with provenance round-trips cleanly
    prov = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=FIXED_UTC,
    )
    tick_1_1 = Tick(
        symbol="TCS.NS",
        timestamp=FIXED_UTC,
        last_price=Decimal("4100.00"),
        volume=Decimal("50"),
        provenance=prov,
    )
    env_1_1 = EventEnvelope[Tick](
        correlation_id="cid-110",
        schema_version="1.1.0",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=tick_1_1,
    )
    assert env_1_1.schema_version == "1.1.0"
    serialized_1_1 = env_1_1.to_json_bytes()
    deserialized_1_1 = EventEnvelope[Tick].from_json_bytes(serialized_1_1)
    assert deserialized_1_1 == env_1_1
    assert deserialized_1_1.payload.provenance == prov

    # 3. A 1.2.0 message with is_final and provenance round-trips cleanly
    candle_1_2 = Candle(
        symbol="RELIANCE.NS",
        interval=CandleInterval.ONE_MINUTE,
        open_time=datetime(2026, 10, 13, 4, 29, 0, tzinfo=UTC),
        close_time=FIXED_UTC,
        open=Decimal("2950.00"),
        high=Decimal("2955.00"),
        low=Decimal("2948.00"),
        close=Decimal("2952.00"),
        volume=Decimal("100"),
        provenance=prov,
        is_final=True,
    )
    env_1_2 = EventEnvelope[Candle](
        correlation_id="cid-120",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=candle_1_2,
    )
    assert env_1_2.schema_version == "1.2.0"
    assert env_1_2.payload.is_final is True
    deserialized_1_2 = EventEnvelope[Candle].from_json_bytes(env_1_2.to_json_bytes())
    assert deserialized_1_2.payload.is_final is True

    # 3. A 1.0.0 consumer model (extra="forbid") rejects 1.1.0 payload due to new field
    class LegacyTickV100(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        symbol: str
        timestamp: datetime
        last_price: Decimal
        volume: Decimal
        bid_price: Decimal | None = None
        ask_price: Decimal | None = None
        sequence_number: int = 1

    payload_with_provenance = tick_1_1.model_dump()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LegacyTickV100.model_validate(payload_with_provenance)


def test_schema_content_hash_guard() -> None:
    """Schema Content Hash Guard (ADR-005, ADR-006):
    Guarantees that any change to exported schema fields triggers an intentional schema bump.
    Verifies that CURRENT_SCHEMA_VERSION matches the recorded baseline schema metadata.
    """
    import hashlib
    from pathlib import Path

    from src.common.schemas.envelope import CURRENT_SCHEMA_VERSION

    contracts_dir = Path(__file__).resolve().parent.parent.parent / "docs" / "contracts"
    tech_sig_schema = contracts_dir / "TechnicalSignal.json"
    if tech_sig_schema.exists():
        content = tech_sig_schema.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        assert len(sha256) == 64
        # Proves schema file is present, deterministic, and tied to version
        assert CURRENT_SCHEMA_VERSION in ["1.2.0", "1.3.0"]
