"""Prometheus operational and financial metrics registry for AlphaTrader.

Exposes institutional-grade operational counters, gauges, and latency histograms
conforming to Prometheus standard exposition format.

Governance: ADR-002, ADR-010.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from decimal import Decimal

logger = logging.getLogger("alphatrader.observability.metrics")


@dataclass(slots=True)
class MetricSample:
    """A single metric value with its dimensional label set."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    metric_type: str = "gauge"
    help_text: str = ""


def _format_labels(labels: dict[str, str]) -> str:
    """Format dictionary as Prometheus metric label string."""
    if not labels:
        return ""
    items = sorted(labels.items())
    return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}"


class MetricsRegistry:
    """Thread-safe in-process Prometheus metrics registry and exposition formatter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gauges: dict[str, tuple[str, dict[tuple[tuple[str, str], ...], float]]] = {}
        self._counters: dict[str, tuple[str, dict[tuple[tuple[str, str], ...], float]]] = {}
        self._histograms: dict[
            str, tuple[str, list[float], dict[tuple[tuple[str, str], ...], list[float]]]
        ] = {}

    def register_gauge(self, name: str, help_text: str) -> None:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = (help_text, {})

    def register_counter(self, name: str, help_text: str) -> None:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = (help_text, {})

    def register_histogram(
        self,
        name: str,
        help_text: str,
        buckets: list[float] | None = None,
    ) -> None:
        default_buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = (help_text, sorted(default_buckets), {})

    def set_gauge(
        self,
        name: str,
        value: float | int | Decimal,
        labels: dict[str, str] | None = None,
    ) -> None:
        label_key = tuple(sorted((labels or {}).items()))
        val_float = float(value)
        with self._lock:
            if name in self._gauges:
                self._gauges[name][1][label_key] = val_float

    def inc_counter(
        self,
        name: str,
        value: float | int = 1.0,
        labels: dict[str, str] | None = None,
    ) -> None:
        label_key = tuple(sorted((labels or {}).items()))
        with self._lock:
            if name in self._counters:
                current = self._counters[name][1].get(label_key, 0.0)
                self._counters[name][1][label_key] = current + float(value)

    def observe_histogram(
        self,
        name: str,
        value: float | int,
        labels: dict[str, str] | None = None,
    ) -> None:
        label_key = tuple(sorted((labels or {}).items()))
        val_float = float(value)
        with self._lock:
            if name in self._histograms:
                if label_key not in self._histograms[name][2]:
                    self._histograms[name][2][label_key] = []
                self._histograms[name][2][label_key].append(val_float)

    def format_prometheus(self) -> str:
        """Render all registered metrics in standard Prometheus exposition format."""
        lines: list[str] = []

        with self._lock:
            # 1. Render Gauges
            for name, (help_text, series) in sorted(self._gauges.items()):
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} gauge")
                for label_tuples, val in sorted(series.items()):
                    lbl_str = ""
                    if label_tuples:
                        lbl_str = "{" + ",".join(f'{k}="{v}"' for k, v in label_tuples) + "}"
                    lines.append(f"{name}{lbl_str} {val}")

            # 2. Render Counters
            for name, (help_text, series) in sorted(self._counters.items()):
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} counter")
                for label_tuples, val in sorted(series.items()):
                    lbl_str = ""
                    if label_tuples:
                        lbl_str = "{" + ",".join(f'{k}="{v}"' for k, v in label_tuples) + "}"
                    lines.append(f"{name}{lbl_str} {val}")

            # 3. Render Histograms
            for h_name, (h_help_text, buckets, hist_series) in sorted(self._histograms.items()):
                lines.append(f"# HELP {h_name} {h_help_text}")
                lines.append(f"# TYPE {h_name} histogram")
                for label_tuples, obs_list in sorted(hist_series.items()):
                    base_labels = dict(label_tuples)
                    count = len(obs_list)
                    total_sum = sum(obs_list)

                    for b in buckets:
                        le_count = sum(1 for o in obs_list if o <= b)
                        b_labels = dict(base_labels)
                        b_labels["le"] = str(b)
                        lbl_str = _format_labels(b_labels)
                        lines.append(f"{h_name}_bucket{lbl_str} {le_count}")

                    inf_labels = dict(base_labels)
                    inf_labels["le"] = "+Inf"
                    lbl_str_inf = _format_labels(inf_labels)
                    lines.append(f"{h_name}_bucket{lbl_str_inf} {count}")

                    lbl_str_sum = _format_labels(base_labels) if base_labels else ""
                    lines.append(f"{h_name}_sum{lbl_str_sum} {total_sum}")
                    lines.append(f"{h_name}_count{lbl_str_sum} {count}")

        return "\n".join(lines) + "\n"


class AlphaTraderMetrics:
    """Canonical AlphaTrader operational and financial metrics manager."""

    # Metric Names
    EQUITY_INR = "alphatrader_equity_inr"
    INTRADAY_DRAWDOWN_PCT = "alphatrader_intraday_drawdown_pct"
    ORDERS_TOTAL = "alphatrader_orders_total"
    RISK_HALT_STATE = "alphatrader_risk_halt_state"
    TICK_TO_FILL_LATENCY_SECONDS = "alphatrader_tick_to_fill_latency_seconds"
    PORTFOLIO_CASH_INR = "alphatrader_portfolio_cash_inr"
    OPEN_POSITIONS_COUNT = "alphatrader_open_positions_count"

    def __init__(self, registry: MetricsRegistry | None = None) -> None:
        self.registry = registry or MetricsRegistry()
        self._init_standard_metrics()

    def _init_standard_metrics(self) -> None:
        self.registry.register_gauge(
            self.EQUITY_INR,
            "Current total portfolio equity in INR (Cash + Net Market Value)",
        )
        self.registry.register_gauge(
            self.PORTFOLIO_CASH_INR,
            "Current unencumbered cash balance in INR",
        )
        self.registry.register_gauge(
            self.INTRADAY_DRAWDOWN_PCT,
            "Current intraday drawdown percentage against high water mark",
        )
        self.registry.register_gauge(
            self.OPEN_POSITIONS_COUNT,
            "Current number of active open inventory positions",
        )
        self.registry.register_gauge(
            self.RISK_HALT_STATE,
            "Trading floor risk circuit breaker state (0=Active, 1=Halted)",
        )
        self.registry.register_counter(
            self.ORDERS_TOTAL,
            "Total count of order submissions partitioned by status and symbol",
        )
        self.registry.register_histogram(
            self.TICK_TO_FILL_LATENCY_SECONDS,
            "End-to-end latency in seconds from market tick ingestion to paper fill commit",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
        )

    def record_portfolio_state(
        self,
        equity: float | int | Decimal,
        cash: float | int | Decimal,
        drawdown_pct: float | int | Decimal,
        open_positions: int,
    ) -> None:
        labels = {"trading_mode": "PAPER"}
        self.registry.set_gauge(self.EQUITY_INR, equity, labels)
        self.registry.set_gauge(self.PORTFOLIO_CASH_INR, cash, labels)
        self.registry.set_gauge(self.INTRADAY_DRAWDOWN_PCT, drawdown_pct, labels)
        self.registry.set_gauge(self.OPEN_POSITIONS_COUNT, open_positions, labels)

    def set_risk_halt(self, halted: bool) -> None:
        self.registry.set_gauge(
            self.RISK_HALT_STATE,
            1.0 if halted else 0.0,
            {"trading_mode": "PAPER"},
        )

    def record_order(self, status: str, symbol: str, side: str) -> None:
        self.registry.inc_counter(
            self.ORDERS_TOTAL,
            1.0,
            {"status": status, "symbol": symbol, "side": side, "trading_mode": "PAPER"},
        )

    def record_tick_to_fill_latency(self, latency_seconds: float, symbol: str) -> None:
        self.registry.observe_histogram(
            self.TICK_TO_FILL_LATENCY_SECONDS,
            latency_seconds,
            {"symbol": symbol, "trading_mode": "PAPER"},
        )

    def export_prometheus(self) -> str:
        return self.registry.format_prometheus()


_global_metrics = AlphaTraderMetrics()


def get_metrics() -> AlphaTraderMetrics:
    """Access global singleton metrics manager."""
    return _global_metrics
