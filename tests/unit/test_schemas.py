"""Comprehensive unit tests for all Pydantic v2 domain schemas in src/common/schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.common.schemas.envelope import CURRENT_SCHEMA_VERSION
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
    sample_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    prov = DataProvenance(
        provider="sim",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
    )
    tech = TechnicalSignal(
        symbol="INFY.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        regime=MarketRegime.TRENDING_UP,
        indicators={"ema_9": Decimal("1850.25"), "rsi_14": Decimal("61.50")},
        rules_long=["ema_9_cross"],
        rules_short=[],
        config_version="1.0.0",
        config_hash=sample_hash,
        provenance=prov,
    )
    assert TechnicalSignal.model_validate_json(tech.model_dump_json()) == tech
    assert tech.rules_fired == ["ema_9_cross"]
    assert tech.rules_against == []

    with pytest.raises(ValidationError, match="cannot be empty"):
        TechnicalSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            direction=Direction.LONG,
            confidence=Decimal("1.00"),
            regime=MarketRegime.TRENDING_UP,
            config_version="1.0.0",
            config_hash=sample_hash,
            rules_long=["r1"],
            indicators={},
            provenance=prov,
        )

    with pytest.raises(ValidationError, match="non-empty strings"):
        TechnicalSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            direction=Direction.LONG,
            confidence=Decimal("1.00"),
            indicators={"   ": Decimal("1.0")},
            config_version="1.0.0",
            config_hash=sample_hash,
            rules_long=["r1"],
            provenance=prov,
        )

    # FLAT rules_fired and rules_against return []
    tech_flat = TechnicalSignal(
        symbol="INFY.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.FLAT,
        confidence=Decimal("0.50"),
        regime=None,
        indicators={"ema_9": Decimal("1850.25")},
        rules_long=["r_bull"],
        rules_short=["r_bear"],
        config_version="1.0.0",
        config_hash=sample_hash,
        provenance=prov,
    )
    assert tech_flat.rules_fired == []
    assert tech_flat.rules_against == []
    assert tech_flat.regime is None

    # QuantSignal: direction LONG requires target_weight > 0, parameters round-trips int/Decimal/str
    quant = QuantSignal(
        symbol="INFY.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        strategy_name="mean_reversion_bb_v1",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.03"),
        expected_net_edge_bps_per_trade=None,
        config_hash=sample_hash,
        provenance=prov,
        parameters={
            "lookback": 20,
            "threshold": Decimal("2.50"),
            "mode": "aggressive",
        },
    )
    dumped_quant = quant.model_dump_json()
    restored_quant = QuantSignal.model_validate_json(dumped_quant)
    assert restored_quant == quant
    assert restored_quant.parameters["lookback"] == 20
    assert restored_quant.parameters["threshold"] == Decimal("2.50")
    assert restored_quant.parameters["mode"] == "aggressive"

    # QuantSignal rejects SHORT in Schema 1.3.0
    with pytest.raises(ValidationError, match="direction=SHORT is prohibited"):
        QuantSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            strategy_name="short_strat",
            strategy_version="1.0.0",
            direction=Direction.SHORT,
            target_weight=Decimal("0.05"),
            config_hash=sample_hash,
            provenance=prov,
        )

    with pytest.raises(ValidationError, match="direction=FLAT must have target_weight=0"):
        QuantSignal(
            symbol="INFY.NS",
            evaluated_at=FIXED_UTC,
            interval=CandleInterval.FIVE_MINUTES,
            strategy_name="flat_strat",
            strategy_version="1.0.0",
            direction=Direction.FLAT,
            target_weight=Decimal("0.05"),
            config_hash=sample_hash,
            provenance=prov,
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
    tampered_bytes = raw_bytes.replace(
        f'"schema_version":"{CURRENT_SCHEMA_VERSION}"'.encode(), b'"schema_version":"9.9.9"'
    )
    with pytest.raises(SchemaVersionMismatchError, match="Schema version mismatch"):
        EventEnvelope[Tick].from_json_bytes(tampered_bytes)

    # Deserialization expecting a specific schema_version
    with pytest.raises(SchemaVersionMismatchError, match="Schema version mismatch"):
        EventEnvelope[Tick].from_json_bytes(raw_bytes, expected_schema_version="2.0.0")
    assert (
        EventEnvelope[Tick].from_json_bytes(raw_bytes, expected_schema_version=CURRENT_SCHEMA_VERSION)
        == envelope
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
        schema_version="1.2.0",
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
    """Schema Content Hash Guard (ADR-005, ADR-006, Sprint 6):
    Guarantees that any schema modification triggers an intentional schema bump.
    Embeds the exact sha256 hashes of all 24 exported schemas from git commit 28bb984 (1.2.0 baseline).
    Detects any modification when running on 1.2.0, and verifies bumped schemas when on 1.3.0.
    """
    import hashlib
    from pathlib import Path

    from src.common.schemas.envelope import CURRENT_SCHEMA_VERSION

    # Baseline SHA-256 digests derived directly from git show 28bb984:docs/contracts/<file>
    baseline_1_2_0_hashes = {
        "Candle.schema.json": "7147f88d7b2e11e23ee6e2e83db90f82e4e2e0bdbdc224c3cff3c3e17c750208",
        "DataProvenance.schema.json": "cf4c3b588eef1add7758bd9851e2d970b04079c855ab3715a2429ea87f861e3c",
        "EventEnvelope_Candle.schema.json": "11e7081b9e77eaf1c08d96ac325fad69da046ceaf07925132d4abf4967b92a2b",
        "EventEnvelope_Fill.schema.json": "c510c7cb69235733e1d288d28c3dc2203c73f8f0e00e9a22acf364af907a086e",
        "EventEnvelope_OrderRequest.schema.json": "c570c777af17072fef427ac4872db3cb64647a2b29e72b39358e070fc246b878",
        "EventEnvelope_PortfolioSnapshot.schema.json": "749788ef4abfd914eef66844f5507e576b78c6d48c39da327accd23796139366",
        "EventEnvelope_QuantSignal.schema.json": "8f0ba85bc403b8f86b925d6a4b3bc45773bd22aaa5c397fca983382997960be9",
        "EventEnvelope_RiskDecision.schema.json": "8512582b4d15a54464512d4261ae30a93b816e943827d46b8e3a909d6f74b838",
        "EventEnvelope_SystemEvent.schema.json": "f5525af0a590f5fe8dc7f65c2d8e3f6ebb6ce4a07bab51a53e51b657c4c3b1f9",
        "EventEnvelope_TechnicalSignal.schema.json": "f9368a195825f6f26d9141d8df2678ea67aa38ce46ae98fb46156d6706863306",
        "EventEnvelope_Tick.schema.json": "31a33dc7d5370150e379d7ce9d3c394d0103ec56705c490c715e9df49048ff0f",
        "Fill.schema.json": "f73db6ffeb0f7a5cb11c27e208a0f3b4b55f50b77b35f5eeb970e3c09ee2c798",
        "HaltEvent.schema.json": "8c47b6dd94090d7127db4c0e250ea0d81b2e5db7c37eff5669785852a92ae34b",
        "MarketStatus.schema.json": "635d3de67a6cf6f5eaaab8333a25f560aab7da6f0ab52cd6ceaafb47d7d9142d",
        "Order.schema.json": "00d4bfa19f22dcb472f636125fa0f45bf21fbd78a743dfe9adf2de7064f32c17",
        "OrderRequest.schema.json": "40e442a63bcb78214dd9b243049b74d146f017bedc806894dbb68480b7b1455b",
        "PortfolioSnapshot.schema.json": "8bec106441a27802239de54795f72c82ac64eb6bb6874aa22a0830c802b3d45e",
        "Position.schema.json": "dc665c4dd9d3f2db42124a6e4b014add3deb6ad41984fbd2f5c4a9128d1e5d92",
        "QuantSignal.schema.json": "4036134b5ee28f6a996656a983497f8c3e4cf388911bc76224faa47153e7ae8a",
        "RiskDecision.schema.json": "1da6330cee0ff590c2f3847c58d747660109ff0fb84f8c1a7e54ac66f6ee62d1",
        "RiskInputSnapshot.schema.json": "a7d0315c54ec25754d7f44fb37a3450e132059cfe4c786a20001ae2ae0c229d2",
        "SystemEvent.schema.json": "98a2cf398757321f910cfab16417f0fe018faa5f9799ca8c63fdea7fd74b1f9c",
        "TechnicalSignal.schema.json": "ece9b5c2bc074a7e7b0dbff5bc098cc4c455ac0005c2e21078b960c365103877",
        "Tick.schema.json": "c46277f848abc93691bf640c9734a8e9777e9b2c307f9bef0d5f0b8631cdb883",
    }

    contracts_dir = Path(__file__).resolve().parent.parent.parent / "docs" / "contracts"

    # All 24 exported contracts must exist and match hash expectations
    assert len(baseline_1_2_0_hashes) == 24
    for schema_filename, baseline_hash in baseline_1_2_0_hashes.items():
        file_path = contracts_dir / schema_filename
        assert file_path.exists(), f"Missing contract schema file: {schema_filename}"
        curr_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert len(curr_hash) == 64

        if CURRENT_SCHEMA_VERSION == "1.2.0":
            assert curr_hash == baseline_hash, (
                f"Schema {schema_filename} was modified without bumping CURRENT_SCHEMA_VERSION!"
            )
        elif CURRENT_SCHEMA_VERSION == "1.3.0":
            # In 1.3.0:
            # - TechnicalSignal and QuantSignal models were updated
            # - All EventEnvelope_* schemas diverge because schema_version default bumped from '1.2.0' to '1.3.0'
            # - Underlying domain payload schemas (Candle, Tick, Fill, etc.) remain strictly identical to 1.2.0 baseline
            envelope_and_signal_schemas = {
                "TechnicalSignal.schema.json",
                "QuantSignal.schema.json",
                "EventEnvelope_Candle.schema.json",
                "EventEnvelope_Fill.schema.json",
                "EventEnvelope_OrderRequest.schema.json",
                "EventEnvelope_PortfolioSnapshot.schema.json",
                "EventEnvelope_QuantSignal.schema.json",
                "EventEnvelope_RiskDecision.schema.json",
                "EventEnvelope_SystemEvent.schema.json",
                "EventEnvelope_TechnicalSignal.schema.json",
                "EventEnvelope_Tick.schema.json",
            }
            if schema_filename in envelope_and_signal_schemas:
                assert curr_hash != baseline_hash, (
                    f"Expected schema bump divergence for {schema_filename} in 1.3.0!"
                )
            else:
                assert curr_hash == baseline_hash, (
                    f"Unintended drift in unmodified schema {schema_filename}!"
                )


def test_legacy_1_2_technical_signal_payload_rejected_on_1_3() -> None:
    """Explicit upgrade test (ADR-003, ADR-007):
    A legacy 1.2.0 TechnicalSignal payload containing 'rules_fired' and missing 'config_hash'
    must be strictly rejected by the 1.3.0 TechnicalSignal model due to extra='forbid'
    and missing required 64-hex config_hash.
    """
    import uuid

    legacy_1_2_payload = {
        "signal_id": str(uuid.uuid4()),
        "symbol": "RELIANCE.NS",
        "evaluated_at": FIXED_UTC.isoformat(),
        "interval": "5m",
        "direction": "LONG",
        "confidence": "0.80",
        "regime": "TRENDING_UP",
        "indicators": {"ema_9": "2950.00"},
        "rules_fired": ["ema_9_above_ema_21"],  # Legacy 1.2.0 field
        "config_version": "1.0.0",
        "provenance": {
            "provider": "sim",
            "feed_mode": "SIMULATED",
            "is_delayed": False,
            "delay_seconds": 0,
        },
    }

    # 1. Direct validation failure: extra input rules_fired and missing config_hash
    with pytest.raises(ValidationError) as exc_info:
        TechnicalSignal.model_validate(legacy_1_2_payload)

    errors = exc_info.value.errors()
    error_fields = {e["loc"][0] for e in errors}
    assert "rules_fired" in error_fields  # extra input
    assert "config_hash" in error_fields  # missing required input

    # 2. Envelope deserialization with expected_schema_version="1.3.0" rejects 1.2.0 version
    env_1_2_json = (
        '{"schema_version": "1.2.0", "event_id": "a0000001-0000-4000-8000-000000000001", '
        '"correlation_id": "cid-legacy-001", "produced_at": "2026-10-13T04:30:00Z", '
        '"source": "mcp.technical_analyst", "payload": ' + json.dumps(legacy_1_2_payload) + '}'
    )
    with pytest.raises(SchemaVersionMismatchError, match="Schema version mismatch"):
        EventEnvelope[TechnicalSignal].from_json_bytes(
            env_1_2_json, expected_schema_version="1.3.0"
        )

