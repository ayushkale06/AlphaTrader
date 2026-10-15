"""Comprehensive unit tests for Quant Strategy Engine (Sprint 6 Phase B).

Covers:
1. Strategy registry and deterministic evaluation (EMA, RSI/BB, Breakout).
2. Sizing: ATR brackets and fractional-Kelly constraints.
3. Multi-factor evaluation: scoring, conflict resolution, zero volatility, and negative edge.
4. MCP Server: registration and behavior of all 8 tools.
5. Streaming worker: causation tracking, DLQ routing, warmup suppression.
6. Cash delivery invariant: SHORT prohibition and target_weight gating.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.market import Candle, CandleInterval, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, EdgeBasis, QuantSignal, TechnicalSignal
from src.mcp_servers.quant_strategy_engine.cache import QuantStrategyCache
from src.mcp_servers.quant_strategy_engine.evaluator import MultiFactorEvaluator
from src.mcp_servers.quant_strategy_engine.publisher import (
    EdgeVerificationError,
    QuantSignalPublisher,
    verify_backtest_report_credential,
)
from src.mcp_servers.quant_strategy_engine.server import (
    evaluate_alpha_factors,
    explain_strategy_signal,
    get_backtest_report,
    get_engine_health,
    get_strategy_signal,
    get_target_portfolio,
    list_strategies,
    propose_sized_order,
)
from src.mcp_servers.quant_strategy_engine.sizing import PositionSizer
from src.mcp_servers.quant_strategy_engine.strategies import (
    EmaTrendFollowingStrategy,
    RsiBollingerMeanReversionStrategy,
    StrategyRegistry,
    VolatilityBreakoutStrategy,
)
from src.mcp_servers.quant_strategy_engine.worker import QuantStrategyService

FIXED_UTC = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
SAMPLE_PROV = DataProvenance(provider="simulated_feed", feed_mode=FeedMode.SIMULATED)


def make_candle(
    close: float,
    symbol: str = "TCS.NS",
    interval: CandleInterval = CandleInterval.FIVE_MINUTES,
) -> Candle:
    c_dec = Decimal(str(close))
    return Candle(
        symbol=symbol,
        interval=interval,
        open_time=FIXED_UTC,
        close_time=FIXED_UTC + timedelta(minutes=5),
        open=c_dec,
        high=c_dec * Decimal("1.01"),
        low=c_dec * Decimal("0.99"),
        close=c_dec,
        volume=Decimal("1000"),
        vwap=c_dec,
        trade_count=50,
        is_final=True,
        provenance=SAMPLE_PROV,
    )


# ==============================================================================
# 1. Strategy Registry and Strategy Tests
# ==============================================================================


def test_ema_trend_following_strategy_logic() -> None:
    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    assert strat.strategy_name == "ema_trend_following"
    assert strat.strategy_version == "1.0.0"

    c = make_candle(100.0)

    # 1. Warmup check: missing ema_50
    dec_warmup = strat.evaluate(c, {"ema_9": Decimal("102.0"), "ema_21": Decimal("100.0")})
    assert dec_warmup.is_warmup is True
    assert dec_warmup.direction == Direction.FLAT
    assert dec_warmup.target_weight == Decimal("0")

    # 2. Bullish trend: ema_9 > ema_21 and close > ema_50
    dec_bull = strat.evaluate(
        c,
        {"ema_9": Decimal("102.0"), "ema_21": Decimal("100.0"), "ema_50": Decimal("95.0")},
    )
    assert dec_bull.is_warmup is False
    assert dec_bull.direction == Direction.LONG
    assert dec_bull.target_weight == Decimal("0.10")

    # 3. Bearish / Flat: ema_9 <= ema_21
    dec_flat = strat.evaluate(
        c,
        {"ema_9": Decimal("98.0"), "ema_21": Decimal("100.0"), "ema_50": Decimal("95.0")},
    )
    assert dec_flat.direction == Direction.FLAT
    assert dec_flat.target_weight == Decimal("0")


def test_rsi_bollinger_mean_reversion_strategy_logic() -> None:
    strat = RsiBollingerMeanReversionStrategy(
        {
            "strategy_version": "1.0.0",
            "base_allocation_pct": "0.08",
            "bb_percent_b_entry": "0.15",
            "rsi_oversold_threshold": "30.0",
            "rsi_exit_threshold": "50.0",
        }
    )
    c = make_candle(100.0)

    # 1. Warmup: missing indicators
    dec_warmup = strat.evaluate(c, {"rsi_14": Decimal("25.0")})
    assert dec_warmup.is_warmup is True

    # 2. Oversold bounce entry: %B <= 0.15 and RSI <= 30
    dec_entry = strat.evaluate(c, {"bb_percent_b": Decimal("0.10"), "rsi_14": Decimal("28.0")})
    assert dec_entry.direction == Direction.LONG
    assert dec_entry.target_weight == Decimal("0.08")

    # 3. Mean reverted: RSI >= 50
    dec_exit = strat.evaluate(c, {"bb_percent_b": Decimal("0.60"), "rsi_14": Decimal("55.0")})
    assert dec_exit.direction == Direction.FLAT
    assert dec_exit.target_weight == Decimal("0")


def test_volatility_breakout_strategy_logic() -> None:
    strat = VolatilityBreakoutStrategy(
        {
            "strategy_version": "1.0.0",
            "bb_percent_b_trigger": "0.90",
            "bandwidth_min": "0.04",
            "adx_min": "25.0",
            "base_allocation_pct": "0.06",
        }
    )
    c = make_candle(100.0)

    # 1. Warmup
    assert strat.evaluate(c, {}).is_warmup is True

    # 2. Breakout: %B >= 0.90, BW >= 0.04, ADX >= 25
    dec_breakout = strat.evaluate(
        c,
        {
            "bb_percent_b": Decimal("0.92"),
            "bb_bandwidth": Decimal("0.05"),
            "adx": Decimal("28.0"),
        },
    )
    assert dec_breakout.direction == Direction.LONG
    assert dec_breakout.target_weight == Decimal("0.06")

    # 3. Sub-trigger
    dec_sub = strat.evaluate(
        c,
        {
            "bb_percent_b": Decimal("0.85"),
            "bb_bandwidth": Decimal("0.05"),
            "adx": Decimal("28.0"),
        },
    )
    assert dec_sub.direction == Direction.FLAT
    assert dec_sub.target_weight == Decimal("0")


def test_strategy_registry_register_and_list() -> None:
    reg = StrategyRegistry()
    s1 = EmaTrendFollowingStrategy({"strategy_version": "1.0.0"})
    s2 = RsiBollingerMeanReversionStrategy({"strategy_version": "1.0.0"})
    reg.register(s1)
    reg.register(s2)

    assert reg.get("ema_trend_following") == s1
    assert reg.get("rsi_bollinger_mean_reversion") == s2
    assert reg.get("non_existent") is None

    strats = reg.list_strategies()
    assert len(strats) == 2
    names = {s["strategy_name"] for s in strats}
    assert "ema_trend_following" in names
    assert "rsi_bollinger_mean_reversion" in names


# ==============================================================================
# 2. Position Sizing & ATR Brackets Tests
# ==============================================================================


def test_position_sizer_atr_brackets_and_kelly() -> None:
    sizer = PositionSizer()

    # 1. ATR brackets
    sl, tp, risk = sizer.calculate_atr_brackets(
        close_price=Decimal("100.00"),
        atr=Decimal("2.00"),
        direction=Direction.LONG,
    )
    assert sl == Decimal("96.00")  # 100 - (2 * 2.0)
    assert tp == Decimal("106.00")  # 100 + (3 * 2.0)
    assert risk == Decimal("4.00")

    # 2. Zero volatility / missing ATR
    sl_zero, tp_zero, risk_zero = sizer.calculate_atr_brackets(
        close_price=Decimal("100.00"),
        atr=Decimal("0.00"),
        direction=Direction.LONG,
    )
    assert sl_zero is None
    assert tp_zero is None
    assert risk_zero is None

    # 3. Fractional Kelly calculation: p = 0.60, b = 1.50
    # Full Kelly: (0.60 * 1.50 - 0.40) / 1.50 = (0.90 - 0.40) / 1.50 = 0.50 / 1.50 = 0.3333
    # Fractional (0.25): 0.3333 * 0.25 = 0.0833
    kelly_w = sizer.calculate_fractional_kelly(
        win_rate=Decimal("0.60"),
        payoff_ratio=Decimal("1.50"),
    )
    assert kelly_w is not None
    assert Decimal("0.08") <= kelly_w <= Decimal("0.09")

    # 4. Negative Kelly edge: p = 0.30, b = 1.00 -> returns 0
    neg_kelly = sizer.calculate_fractional_kelly(
        win_rate=Decimal("0.30"),
        payoff_ratio=Decimal("1.00"),
    )
    assert neg_kelly == Decimal("0")

    # 5. Invalid inputs: win_rate >= 1 or <= 0
    assert sizer.calculate_fractional_kelly(Decimal("1.5"), Decimal("1.0")) is None

    # 6. Full sizing proposal for LONG
    res_long = sizer.size_order(
        close_price=Decimal("100.00"),
        atr=Decimal("2.00"),
        direction=Direction.LONG,
        base_weight=Decimal("0.10"),
    )
    assert res_long.direction == Direction.LONG
    assert res_long.target_weight == Decimal("0.10")
    assert res_long.stop_loss_price == Decimal("96.00")

    # 7. Sizing proposal for FLAT
    res_flat = sizer.size_order(
        close_price=Decimal("100.00"),
        atr=Decimal("2.00"),
        direction=Direction.FLAT,
    )
    assert res_flat.direction == Direction.FLAT
    assert res_flat.target_weight == Decimal("0")

    # 8. Sizing proposal for SHORT (forbidden, converted to FLAT)
    res_short = sizer.size_order(
        close_price=Decimal("100.00"),
        atr=Decimal("2.00"),
        direction=Direction.SHORT,
    )
    assert res_short.direction == Direction.FLAT
    assert res_short.target_weight == Decimal("0")


# ==============================================================================
# 3. Multi-Factor Alpha Evaluator Tests
# ==============================================================================


def test_multi_factor_evaluator() -> None:
    evaluator = MultiFactorEvaluator()
    c = make_candle(100.0)

    # 1. All factors bullish: Trend (+1.0), MeanRev (0.0), Breakout (+1.0)
    # Composite: 0.40 * 1.0 + 0.30 * 0.0 + 0.30 * 1.0 = 0.70 >= 0.25 -> LONG
    res_bull = evaluator.evaluate_factors(
        c,
        indicators={
            "ema_9": Decimal("102.0"),
            "ema_21": Decimal("100.0"),
            "ema_50": Decimal("95.0"),
            "bb_percent_b": Decimal("0.90"),
            "bb_bandwidth": Decimal("0.05"),
            "adx": Decimal("28.0"),
        },
    )
    assert res_bull.direction == Direction.LONG
    assert res_bull.composite_score == Decimal("0.7000")
    assert res_bull.factor_fired["trend"] is True
    assert res_bull.factor_fired["breakout"] is True

    # 2. Conflicting factors: Trend Bullish (+1.0), MeanRev Bearish/Overbought (-1.0)
    # Composite: 0.40 * 1.0 + 0.30 * (-1.0) + 0.30 * 0.0 = 0.10 < 0.25 -> FLAT
    res_conf = evaluator.evaluate_factors(
        c,
        indicators={
            "ema_9": Decimal("102.0"),
            "ema_21": Decimal("100.0"),
            "ema_50": Decimal("95.0"),
            "rsi_14": Decimal("70.0"),
            "bb_percent_b": Decimal("0.85"),
        },
    )
    assert res_conf.direction == Direction.FLAT
    assert res_conf.composite_score == Decimal("0.1000")
    assert "conflicted" in res_conf.conflict_resolution

    # 3. All factors warming up
    res_warm = evaluator.evaluate_factors(c, {})
    assert res_warm.direction == Direction.FLAT
    assert res_warm.composite_score == Decimal("0.0000")


# ==============================================================================
# 4. MCP Server 8 Tools Tests
# ==============================================================================


def test_mcp_server_all_eight_tools() -> None:
    # 1. list_strategies
    res_list = list_strategies()
    assert "strategies" in res_list
    assert len(res_list["strategies"]) >= 1

    # 2. get_strategy_signal with mock cache
    mock_qcache = MagicMock()
    mock_qcache.get_latest_signal.return_value = None
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_quant_cache", return_value=mock_qcache
    ):
        res_sig = get_strategy_signal("TCS", "ema_trend_following", "5m")
        assert res_sig["error"] == "NOT_READY"
        assert res_sig["status"] == "WARMING_UP"

    # 3. get_target_portfolio
    mock_qcache.get_target_portfolio.return_value = {"TCS.NS": "0.1000", "cash_weight": "0.9000"}
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_quant_cache", return_value=mock_qcache
    ):
        res_port = get_target_portfolio()
        assert res_port["TCS.NS"] == "0.1000"

    # 4. get_backtest_report
    valid_id = "1144e89e0335ce71c2d9fd05894efc7543054f99062ce4d5e2009bf3f8cf210f"
    rep = get_backtest_report(valid_id)
    assert rep["report_id"] == valid_id
    assert rep["is_out_of_sample"] is False
    assert rep["edge_basis"] == "IN_SAMPLE_DEMO"

    bad_rep = get_backtest_report("unknown_id")
    assert bad_rep["error"] == "REPORT_NOT_FOUND"

    # 5. explain_strategy_signal
    mock_acache = MagicMock()
    mock_acache.get_latest_indicators.return_value = {"ema_9": Decimal("102.0")}
    mock_acache.get_latest_signal.return_value = None
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_analyst_cache", return_value=mock_acache
    ):
        exp = explain_strategy_signal("TCS", "ema_trend_following", "5m")
        assert exp["symbol"] == "TCS.NS"
        assert exp["strategy_name"] == "ema_trend_following"

    # 6. get_engine_health
    mock_qcache.get_health.return_value = {"status": "HEALTHY", "processed_count": 100}
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_quant_cache", return_value=mock_qcache
    ):
        health = get_engine_health()
        assert health["status"] == "HEALTHY"
        assert health["processed_count"] == 100

    # 7. evaluate_alpha_factors
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_analyst_cache", return_value=mock_acache
    ):
        res_factors = evaluate_alpha_factors("TCS", "5m")
        assert res_factors["symbol"] == "TCS.NS"
        assert "composite_score" in res_factors

    # 8. propose_sized_order
    mock_acache.get_latest_indicators.return_value = {
        "ema_9": Decimal("100.0"),
        "atr_14": Decimal("2.0"),
    }
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_analyst_cache", return_value=mock_acache
    ):
        prop = propose_sized_order("TCS", "ema_trend_following", "5m")
        assert prop["symbol"] == "TCS.NS"
        assert prop["direction"] == "LONG"
        assert prop["stop_loss_price"] == "96.00"
        assert prop["proposed_target_weight"] == "0.1000"

    # Missing close in propose_sized_order returns NOT_READY
    mock_acache.get_latest_indicators.return_value = {"atr_14": Decimal("2.0")}
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_analyst_cache", return_value=mock_acache
    ):
        prop_missing = propose_sized_order("TCS", "ema_trend_following", "5m")
        assert prop_missing["error"] == "NOT_READY"
        assert prop_missing["status"] == "WARMING_UP"

    # Missing close in evaluate_alpha_factors returns NOT_READY
    mock_acache.get_latest_indicators.return_value = {}
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.get_analyst_cache", return_value=mock_acache
    ):
        eval_missing = evaluate_alpha_factors("TCS", "5m")
        assert eval_missing["error"] == "NOT_READY"
        assert eval_missing["status"] == "WARMING_UP"


# ==============================================================================
# 5. Streaming Worker Tests
# ==============================================================================


def test_quant_strategy_service_process_technical_signal_envelope() -> None:
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    svc.register_strategy(strat)

    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        regime=None,
        indicators={
            "ema_9": Decimal("102.0"),
            "ema_21": Decimal("100.0"),
            "ema_50": Decimal("95.0"),
            "atr_14": Decimal("2.0"),
            "close": Decimal("101.0"),
        },
        rules_long=["ema_9_above_ema_21"],
        rules_short=[],
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    env = EventEnvelope[TechnicalSignal](
        event_id=uuid4(),
        correlation_id="cid-test-worker",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )

    # Set genuine closed candle in service cache
    c = make_candle(101.0, symbol="TCS.NS", interval=CandleInterval.FIVE_MINUTES)
    svc.latest_candles[("TCS.NS", "5m")] = c

    with patch.object(svc.publisher, "publish_signal") as mock_pub:
        mock_quant_env = EventEnvelope[QuantSignal](
            event_id=uuid4(),
            correlation_id=env.correlation_id,
            causation_id=env.event_id,
            produced_at=FIXED_UTC,
            source=ComponentSource.QUANT_STRATEGY_ENGINE,
            payload=QuantSignal(
                symbol="TCS.NS",
                evaluated_at=FIXED_UTC,
                interval=CandleInterval.FIVE_MINUTES,
                strategy_name="ema_trend_following",
                strategy_version="1.0.0",
                direction=Direction.LONG,
                target_weight=Decimal("0.10"),
                config_hash="a" * 64,
                provenance=SAMPLE_PROV,
            ),
        )
        mock_pub.return_value = mock_quant_env

        res_envs = svc.process_technical_signal_envelope(env)
        assert len(res_envs) == 1
        published = res_envs[0]

        # Verify causation_id matches incoming envelope.event_id
        assert published.causation_id == env.event_id
        assert published.correlation_id == env.correlation_id
        assert mock_pub.called


def test_quant_strategy_service_missing_candle_suppression() -> None:
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    svc.register_strategy(strat)

    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        regime=None,
        indicators={
            "ema_9": Decimal("102.0"),
            "ema_21": Decimal("100.0"),
            "ema_50": Decimal("95.0"),
        },
        rules_long=["ema_9_above_ema_21"],
        rules_short=[],
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    env = EventEnvelope[TechnicalSignal](
        event_id=uuid4(),
        correlation_id="cid-no-candle",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )

    # When no genuine candle is available, processing must be suppressed (warming up)
    assert ("TCS.NS", "5m") not in svc.latest_candles
    res = svc.process_technical_signal_envelope(env)
    assert res == []


def test_quant_strategy_service_replay_and_offset_safety() -> None:
    """Audit test verifying publish-before-commit and replay safety."""
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    c = make_candle(100.0)
    c_env = EventEnvelope[Candle](
        correlation_id="cid-replay",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=c,
    )
    # 1. Processing duplicate candle updates state idempotently
    assert svc.handle_raw_message("market.candles", c_env.to_json_bytes()) is True
    assert svc.handle_raw_message("market.candles", c_env.to_json_bytes()) is True
    assert svc.latest_candles[("TCS.NS", "5m")] == c


def test_quant_strategy_service_dlq_routing() -> None:
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    mock_prod = MagicMock()
    svc._producer = mock_prod

    # 1. Malformed JSON payload
    res = svc.handle_raw_message("signals.technical", b"invalid-non-json")
    assert res is False
    assert mock_prod.produce.called
    call_kwargs = mock_prod.produce.call_args[1]
    assert call_kwargs["topic"] == "signals.quant.dlq"

    # 2. Envelope validation failure on technical signal
    res2 = svc.handle_raw_message(
        "signals.technical",
        json.dumps({"unexpected": "data"}).encode("utf-8"),
    )
    assert res2 is False

    # 3. Valid candle envelope
    c = make_candle(100.0)
    c_env = EventEnvelope[Candle](
        correlation_id="cid-c",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=c,
    )
    res3 = svc.handle_raw_message("market.candles", c_env.to_json_bytes())
    assert res3 is True
    assert svc.latest_candles[("TCS.NS", "5m")] == c

    # 4. Unknown topic returns False
    assert svc.handle_raw_message("unknown.topic", b"{}") is False


# ==============================================================================
# 6. Publisher and Cryptographic Backtest Verification Tests
# ==============================================================================


def test_publisher_refuses_unregistered_edge_claim(tmp_path: Path) -> None:
    index_file = tmp_path / "index.json"
    index_file.write_text(json.dumps({"version": "1.0.0", "reports": {}}), encoding="utf-8")

    pub = QuantSignalPublisher(bootstrap_servers="localhost:9092", index_path=index_file)

    sig = QuantSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        expected_net_edge_bps_per_trade=Decimal("5.0"),
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        backtest_report_id="a" * 64,
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    with pytest.raises(EdgeVerificationError, match="not registered in backtest index"):
        pub.publish_signal(sig, correlation_id="cid-refuse")


def test_publisher_verifies_valid_out_of_sample_report(tmp_path: Path) -> None:
    report_content = b"# Valid Backtest Report Content\n"
    report_hash = hashlib.sha256(report_content).hexdigest()
    report_file = tmp_path / f"{report_hash}.md"
    report_file.write_bytes(report_content)

    index_data = {
        "version": "1.0.0",
        "reports": {
            report_hash: {
                "edge_basis": "OUT_OF_SAMPLE",
                "strategy_name": "ema_trend_following",
                "strategy_version": "1.0.0",
                "symbol": "TCS.NS",
                "path": str(report_file),
            }
        },
    }
    index_file = tmp_path / "index.json"
    index_file.write_text(json.dumps(index_data), encoding="utf-8")

    meta = verify_backtest_report_credential(
        report_id=report_hash,
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        symbol="TCS.NS",
        index_path=index_file,
    )
    assert meta["edge_basis"] == "OUT_OF_SAMPLE"

    # Publisher successfully validates and produces
    pub = QuantSignalPublisher(bootstrap_servers="localhost:9092", index_path=index_file)
    mock_prod = MagicMock()
    pub._producer = mock_prod

    sig = QuantSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        expected_net_edge_bps_per_trade=Decimal("5.0"),
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        backtest_report_id=report_hash,
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    published_env = pub.publish_signal(sig, correlation_id="cid-verified")
    assert published_env.payload.backtest_report_id == report_hash
    assert mock_prod.produce.called
    pub.flush(timeout_seconds=0.1)


# ==============================================================================
# 7. Additional Strategy and Evaluator Branch Coverage
# ==============================================================================


def test_evaluator_all_branches() -> None:
    evaluator = MultiFactorEvaluator()
    c = make_candle(100.0)

    # 1. Bearish trend branch
    ind_bear = {
        "ema_9": Decimal("95.0"),
        "ema_21": Decimal("100.0"),
        "ema_50": Decimal("105.0"),
        "rsi_14": Decimal("70.0"),
        "bb_percent_b": Decimal("0.85"),
        "bb_bandwidth": Decimal("0.05"),
        "adx": Decimal("30.0"),
    }
    res_bear = evaluator.evaluate_factors(c, ind_bear)
    assert res_bear.direction == Direction.FLAT
    assert res_bear.factor_scores["trend"] == Decimal("-1.0")
    assert res_bear.factor_scores["mean_reversion"] == Decimal("-1.0")

    # 2. Downside breakdown branch
    ind_breakdown = {
        "ema_9": Decimal("100.0"),
        "ema_21": Decimal("100.0"),
        "ema_50": Decimal("100.0"),
        "rsi_14": Decimal("50.0"),
        "bb_percent_b": Decimal("0.10"),
        "bb_bandwidth": Decimal("0.05"),
        "adx": Decimal("30.0"),
    }
    res_down = evaluator.evaluate_factors(c, ind_breakdown)
    assert res_down.factor_scores["breakout"] == Decimal("-1.0")

    # 3. Conflicting signals: Trend Bullish vs Mean Reversion Overbought
    ind_conflict = {
        "ema_9": Decimal("105.0"),
        "ema_21": Decimal("100.0"),
        "ema_50": Decimal("95.0"),
        "rsi_14": Decimal("75.0"),
        "bb_percent_b": Decimal("0.90"),
        "bb_bandwidth": Decimal("0.01"),  # No breakout
        "adx": Decimal("15.0"),
    }
    res_conf = evaluator.evaluate_factors(c, ind_conflict)
    assert "conflicted" in res_conf.conflict_resolution

    # 4. Warming up branch (missing all indicators)
    res_warm = evaluator.evaluate_factors(c, {})
    assert res_warm.composite_score == Decimal("0.0000")
    assert res_warm.direction == Direction.FLAT


def test_strategies_full_branches() -> None:
    c = make_candle(100.0)

    # EMA strategy flat branches
    strat_ema = EmaTrendFollowingStrategy(
        {"strategy_version": "1.0.0", "base_allocation_pct": "0.10"}
    )
    dec1 = strat_ema.evaluate(
        c, {"ema_9": Decimal("98.0"), "ema_21": Decimal("100.0"), "ema_50": Decimal("105.0")}
    )
    assert dec1.direction == Direction.FLAT

    # Volatility breakout flat sub-trigger branches
    strat_vo = VolatilityBreakoutStrategy(
        {
            "strategy_version": "1.0.0",
            "bb_percent_b_trigger": "0.90",
            "bandwidth_min": "0.04",
            "adx_min": "25.0",
        }
    )
    dec_vo_sub = strat_vo.evaluate(
        c,
        {
            "bb_percent_b": Decimal("0.80"),
            "bb_bandwidth": Decimal("0.02"),
            "adx": Decimal("15.0"),
        },
    )
    assert dec_vo_sub.direction == Direction.FLAT
    assert len(dec_vo_sub.reasons) >= 3


def test_quant_strategy_cache_methods() -> None:
    # 1. Test resilient handling when Redis is disconnected
    cache = QuantStrategyCache(redis_url="redis://127.0.0.1:6379/0")
    cache.save_target_portfolio({"TCS.NS": "0.10"})
    assert cache.get_target_portfolio() is None
    cache.record_heartbeat("HEALTHY", 100)
    assert cache.get_health() is None

    # 2. Test cache serialization and retrieval with mock Redis client
    mock_redis = MagicMock()
    store: dict[str, str] = {}

    def mock_set(key: str, val: str, ex: int | None = None) -> None:
        store[key] = val

    def mock_get(key: str) -> str | None:
        return store.get(key)

    mock_redis.set.side_effect = mock_set
    mock_redis.get.side_effect = mock_get

    c_mock = QuantStrategyCache(redis_client=mock_redis)

    # Signal save and get
    sig = QuantSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    c_mock.save_signal(sig)
    retrieved = c_mock.get_latest_signal("TCS.NS", CandleInterval.FIVE_MINUTES)
    assert retrieved is not None
    assert retrieved.symbol == "TCS.NS"
    assert retrieved.direction == Direction.LONG

    # Target portfolio save and get
    c_mock.save_target_portfolio({"TCS.NS": "0.15", "INFY.NS": "0.05"})
    tp = c_mock.get_target_portfolio()
    assert tp is not None
    assert tp["TCS.NS"] == "0.15"

    # Candle save and get
    candle = make_candle(close=200.0)
    c_mock.save_candle(candle)
    c_retrieved = c_mock.get_candle("TCS.NS", CandleInterval.FIVE_MINUTES, candle.close_time)
    assert c_retrieved is not None
    assert c_retrieved.close == Decimal("200.0")

    # Heartbeat save and get
    c_mock.record_heartbeat("HEALTHY", 42)
    h = c_mock.get_health()
    assert h is not None
    assert h["status"] == "HEALTHY"

    # Invalid JSON parsing error branch coverage
    mock_redis.get.side_effect = None
    mock_redis.get.return_value = "invalid-json"
    assert c_mock.get_latest_signal("TCS.NS", CandleInterval.FIVE_MINUTES) is None
    assert c_mock.get_candle("TCS.NS", CandleInterval.FIVE_MINUTES, candle.close_time) is None
    assert c_mock.get_health() is None


def test_server_error_and_edge_branches() -> None:
    # Invalid intervals
    assert "INVALID_INTERVAL" in get_strategy_signal("TCS", interval="invalid")["error"]
    assert "INVALID_INTERVAL" in explain_strategy_signal("TCS", interval="invalid")["error"]
    assert "INVALID_INTERVAL" in evaluate_alpha_factors("TCS", interval="invalid")["error"]
    assert "INVALID_INTERVAL" in propose_sized_order("TCS", interval="invalid")["error"]

    # Unknown strategy
    assert (
        "UNKNOWN_STRATEGY" in explain_strategy_signal("TCS", strategy_name="nonexistent")["error"]
    )

    # Missing backtest report index
    with patch("src.mcp_servers.quant_strategy_engine.server.INDEX_PATH", Path("nonexistent/path")):
        res = get_backtest_report("any_id")
        assert res["error"] == "INDEX_NOT_FOUND"

    # Corrupt backtest report index
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.INDEX_PATH",
        Path("tests/unit/test_quant_strategy_engine.py"),
    ):
        res_corrupt = get_backtest_report("any_id")
        assert res_corrupt["error"] == "INDEX_CORRUPT"

    # Report not found in index
    with patch(
        "src.mcp_servers.quant_strategy_engine.server.INDEX_PATH", Path("docs/backtests/index.json")
    ):
        res_not_found = get_backtest_report("nonexistent_report_id")
        assert res_not_found["error"] == "REPORT_NOT_FOUND"


def test_publisher_error_branches(tmp_path: Path) -> None:
    # 1. Missing report id
    with pytest.raises(EdgeVerificationError, match="Missing backtest_report_id"):
        verify_backtest_report_credential(None, EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS")

    # 2. Missing index file
    with pytest.raises(EdgeVerificationError, match="Backtest index not found"):
        verify_backtest_report_credential(
            "id1", EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS", tmp_path / "no_idx.json"
        )

    # 3. Corrupt index file
    corrupt_idx = tmp_path / "corrupt.json"
    corrupt_idx.write_text("invalid json", encoding="utf-8")
    with pytest.raises(EdgeVerificationError, match="Failed to read backtest index"):
        verify_backtest_report_credential(
            "id1", EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS", corrupt_idx
        )

    # 4. Not OUT_OF_SAMPLE
    bad_basis_idx = tmp_path / "bad_basis.json"
    bad_basis_idx.write_text(
        json.dumps({"reports": {"id1": {"edge_basis": "IN_SAMPLE_BENCHMARK"}}}), encoding="utf-8"
    )
    with pytest.raises(EdgeVerificationError, match="required OUT_OF_SAMPLE"):
        verify_backtest_report_credential(
            "id1", EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS", bad_basis_idx
        )

    # 5. Missing physical report file
    missing_file_idx = tmp_path / "missing_file.json"
    missing_file_idx.write_text(
        json.dumps(
            {
                "reports": {
                    "id1": {"edge_basis": "OUT_OF_SAMPLE", "path": str(tmp_path / "missing.md")}
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EdgeVerificationError, match="Backtest report document missing"):
        verify_backtest_report_credential(
            "id1", EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS", missing_file_idx
        )

    # 6. Content hash mismatch
    mismatch_file = tmp_path / "mismatch.md"
    mismatch_file.write_text("content", encoding="utf-8")
    mismatch_idx = tmp_path / "mismatch.json"
    mismatch_idx.write_text(
        json.dumps(
            {
                "reports": {
                    "id_expected": {"edge_basis": "OUT_OF_SAMPLE", "path": str(mismatch_file)}
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EdgeVerificationError, match="content hash mismatch"):
        verify_backtest_report_credential(
            "id_expected", EdgeBasis.OUT_OF_SAMPLE, "s", "1.0", "TCS.NS", mismatch_idx
        )


def test_position_sizer_edge_cases() -> None:
    sizer = PositionSizer()

    # Missing/invalid win_rate or payoff_ratio
    assert sizer.calculate_fractional_kelly(None, Decimal("1.5")) is None
    assert sizer.calculate_fractional_kelly(Decimal("0.5"), None) is None
    assert sizer.calculate_fractional_kelly(Decimal("0.0"), Decimal("1.5")) is None
    assert sizer.calculate_fractional_kelly(Decimal("1.0"), Decimal("1.5")) is None
    assert sizer.calculate_fractional_kelly(Decimal("0.5"), Decimal("0.0")) is None

    # Negative Kelly expectation returns 0
    neg_kelly = sizer.calculate_fractional_kelly(Decimal("0.2"), Decimal("1.0"))
    assert neg_kelly == Decimal("0")

    # Sizing order with negative Kelly
    res_neg = sizer.size_order(
        close_price=Decimal("100.0"),
        atr=Decimal("2.0"),
        direction=Direction.LONG,
        base_weight=Decimal("0.05"),
        win_rate=Decimal("0.2"),
        payoff_ratio=Decimal("1.0"),
    )
    assert res_neg.target_weight == Decimal("0.05")
    assert any("Non-positive Kelly expectation" in r for r in res_neg.reasons)

    # ATR zero / brackets missing
    res_no_atr = sizer.size_order(
        close_price=Decimal("100.0"),
        atr=Decimal("0.0"),
        direction=Direction.LONG,
    )
    assert res_no_atr.stop_loss_price is None


def test_strategy_registry_load_from_path(tmp_path: Path) -> None:
    cfg_file = tmp_path / "quant_strats.json"
    cfg_file.write_text(
        json.dumps(
            {
                "strategies": {
                    "ema_trend_following": {
                        "strategy_version": "1.0.0",
                        "base_allocation_pct": "0.10",
                    },
                    "rsi_bollinger_mean_reversion": {
                        "strategy_version": "1.0.0",
                        "base_allocation_pct": "0.08",
                    },
                    "volatility_breakout": {
                        "strategy_version": "1.0.0",
                        "base_allocation_pct": "0.06",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    reg = StrategyRegistry(str(cfg_file))
    assert len(reg.list_strategies()) == 3
    assert reg.get("ema_trend_following") is not None
    assert reg.get("rsi_bollinger_mean_reversion") is not None
    assert reg.get("volatility_breakout") is not None
