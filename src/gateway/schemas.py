"""Read-Only Gateway API schemas for AlphaTrader Frontend Dashboard."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PortfolioSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    equity: str
    cash: str
    exposure: str
    realized_pnl: str
    unrealized_pnl: str
    current_drawdown_pct: str
    high_water_mark: str
    revision: int
    as_of: str
    trading_mode: str = "PAPER"


class PositionItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    quantity: str
    average_entry_price: str
    mark_price: str
    market_value: str
    unrealized_pnl: str
    realized_pnl: str
    updated_at: str


class PositionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    items: list[PositionItem]


class QuoteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    last_price: str
    volume: int
    bid_price: str | None = None
    ask_price: str | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    timestamp: str
    feed_mode: str
    provenance: str


class CandleItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    open_time: str
    close_time: str
    open: str
    high: str
    low: str
    close: str
    volume: int
    interval: str


class CandlesResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    interval: str
    count: int
    candles: list[CandleItem]


class OrderItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: UUID
    order_request_id: UUID
    idempotency_key: str
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: str
    filled_quantity: str
    average_fill_price: str | None = None
    limit_price: str | None = None
    stop_loss_price: str
    take_profit_price: str | None = None
    strategy_name: str
    correlation_id: str
    created_at: str
    updated_at: str


class OrdersResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    limit: int
    offset: int
    items: list[OrderItem]


class FillItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_id: UUID
    order_id: UUID
    order_request_id: UUID
    symbol: str
    side: str
    fill_price: str
    fill_quantity: str
    slippage_bps: str
    stt_tax: str
    exchange_fees: str
    gst_tax: str
    stamp_duty: str
    total_fees: str
    correlation_id: str
    filled_at: str


class OrderFillsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: UUID
    fills: list[FillItem]


class ConfiguredLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_daily_drawdown_pct: str
    max_position_size_pct: str
    max_open_positions: int
    max_orders_per_minute: int


class RiskTelemetryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    floor_state: str  # "ACTIVE" | "HALTED" | "UNKNOWN"
    is_floor_halted: bool
    current_drawdown_pct: str
    high_water_mark_inr: str
    equity_inr: str
    start_of_day_equity_inr: str
    session_date: str
    open_positions_count: int
    configured_limits: ConfiguredLimits


class RiskAuditItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: UUID
    order_request_id: UUID
    symbol: str
    verdict: str
    rule_fired: str
    reason_code: str
    inputs_snapshot: dict[str, Any]
    approval_token: str | None = None
    message: str
    correlation_id: str
    evaluated_at: str


class RiskAuditResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    limit: int
    offset: int
    items: list[RiskAuditItem]


class ServiceHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    postgres: bool
    redis: bool


class SystemHealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    version: str
    trading_mode: str
    services: ServiceHealth
    feed_heartbeats: dict[str, Any]


# --- New Feature-Parity Schemas ---


class TechnicalAnalysisResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    interval: str
    indicators: dict[str, str]
    signal: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None


class QuantStrategyResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    interval: str
    registered_strategies: list[str]
    latest_signal: dict[str, Any] | None = None
    sizing_proposal: dict[str, Any] | None = None


class BacktestReportItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    strategy_name: str
    strategy_version: str
    symbol: str
    date_range: str
    data_source: str
    edge_basis: str
    is_out_of_sample: bool
    path: str
    summary_markdown: str


class BacktestRegistryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runs_evaluated: int
    reports: list[BacktestReportItem]


class LedgerEntryItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: UUID
    event_id: UUID
    fill_id: UUID | None
    narration: str
    posted_at: str
    correlation_id: str
    postings: list[dict[str, Any]]


class LedgerJournalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    limit: int
    offset: int
    current_revision: int
    items: list[LedgerEntryItem]


class AgentWorkflowState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    trading_mode: str
    symbol: str
    last_event_correlation_id: str
    agents: dict[str, str]
    pipeline_steps: list[dict[str, Any]]


class AuditChainVerificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    algorithm: str
    total_records_verified: int
    is_unbroken: bool
    genesis_hash: str
    latest_hash: str
    message: str


class SystemConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app_env: str
    trading_mode: str
    live_trading_enabled: bool
    market_timezone: str
    nse_market_feed_mode: str
    risk_limits: ConfiguredLimits
    schema_version: str
    app_version: str
    adrs: list[dict[str, str]]
