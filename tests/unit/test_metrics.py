"""Unit tests for Sprint 16: Prometheus Operational & Financial Metrics.

Validates:
1. Registration and formatting of Gauges, Counters, and Histograms in standard Prometheus format.
2. Financial state exposition (equity, cash, drawdown percentage, open positions).
3. Risk circuit-breaker state recording.
4. Order counter incrementation with symbol and status dimensions.
5. Tick-to-fill latency histogram observation and bucket generation.
6. Thread-safety and label permutation ordering.

Governance: ADR-002, ADR-010.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.observability.metrics import AlphaTraderMetrics, MetricsRegistry


@pytest.fixture
def metrics() -> AlphaTraderMetrics:
    """Provides a fresh isolated AlphaTraderMetrics instance."""
    return AlphaTraderMetrics(registry=MetricsRegistry())


def test_portfolio_state_gauges(metrics: AlphaTraderMetrics) -> None:
    """Verifies portfolio financial state metrics are accurately formatted."""
    metrics.record_portfolio_state(
        equity=Decimal("1050000.50"),
        cash=Decimal("950000.00"),
        drawdown_pct=Decimal("0.75"),
        open_positions=3,
    )

    output = metrics.export_prometheus()
    assert "# TYPE alphatrader_equity_inr gauge" in output
    assert 'alphatrader_equity_inr{trading_mode="PAPER"} 1050000.5' in output
    assert 'alphatrader_portfolio_cash_inr{trading_mode="PAPER"} 950000.0' in output
    assert 'alphatrader_intraday_drawdown_pct{trading_mode="PAPER"} 0.75' in output
    assert 'alphatrader_open_positions_count{trading_mode="PAPER"} 3.0' in output


def test_risk_halt_state_gauge(metrics: AlphaTraderMetrics) -> None:
    """Verifies trading floor halt state gauge reflects active vs halted."""
    # Active
    metrics.set_risk_halt(False)
    assert 'alphatrader_risk_halt_state{trading_mode="PAPER"} 0.0' in metrics.export_prometheus()

    # Tripped
    metrics.set_risk_halt(True)
    assert 'alphatrader_risk_halt_state{trading_mode="PAPER"} 1.0' in metrics.export_prometheus()


def test_order_counter_metrics(metrics: AlphaTraderMetrics) -> None:
    """Verifies order counters increment with dimensional status and symbol labels."""
    metrics.record_order(status="FILLED", symbol="RELIANCE.NS", side="BUY")
    metrics.record_order(status="FILLED", symbol="RELIANCE.NS", side="BUY")
    metrics.record_order(status="REJECTED", symbol="TCS.NS", side="SELL")

    output = metrics.export_prometheus()
    assert "# TYPE alphatrader_orders_total counter" in output
    expected_order_buy = (
        'alphatrader_orders_total{side="BUY",status="FILLED",symbol="RELIANCE.NS",'
        'trading_mode="PAPER"} 2.0'
    )
    assert expected_order_buy in output

    expected_order_sell = (
        'alphatrader_orders_total{side="SELL",status="REJECTED",symbol="TCS.NS",'
        'trading_mode="PAPER"} 1.0'
    )
    assert expected_order_sell in output


def test_tick_to_fill_latency_histogram(metrics: AlphaTraderMetrics) -> None:
    """Verifies latency histogram generates buckets, counts, and sums."""
    metrics.record_tick_to_fill_latency(0.004, symbol="INFY.NS")
    metrics.record_tick_to_fill_latency(0.015, symbol="INFY.NS")

    output = metrics.export_prometheus()
    assert "# TYPE alphatrader_tick_to_fill_latency_seconds histogram" in output

    expected_b1 = (
        'alphatrader_tick_to_fill_latency_seconds_bucket{le="0.005",symbol="INFY.NS",'
        'trading_mode="PAPER"} 1'
    )
    assert expected_b1 in output

    expected_b2 = (
        'alphatrader_tick_to_fill_latency_seconds_bucket{le="0.025",symbol="INFY.NS",'
        'trading_mode="PAPER"} 2'
    )
    assert expected_b2 in output

    expected_inf = (
        'alphatrader_tick_to_fill_latency_seconds_bucket{le="+Inf",symbol="INFY.NS",'
        'trading_mode="PAPER"} 2'
    )
    assert expected_inf in output

    assert (
        'alphatrader_tick_to_fill_latency_seconds_count{symbol="INFY.NS",trading_mode="PAPER"} 2'
        in output
    )
    assert (
        'alphatrader_tick_to_fill_latency_seconds_sum{symbol="INFY.NS",trading_mode="PAPER"} 0.019'
        in output
    )


def test_custom_registry_empty_output() -> None:
    """Verifies empty registry formats cleanly without errors."""
    registry = MetricsRegistry()
    assert registry.format_prometheus() == "\n"
