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
    stop_loss_price: str | None = None
    take_profit_price: str | None = None
    trailing_stop_price: str | None = None


class PositionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int
    items: list[PositionItem]


class MarketStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    is_open: bool
    session: str
    reason: str
    as_of: str
    timezone: str = "Asia/Kolkata"
    trading_hours: str = "09:15 - 15:30 IST"


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


# --- 7 Extended Professional Workstation Schemas ---


class SimulationModeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: str  # "EXCHANGE_HOURS" | "HISTORICAL_REPLAY"
    replay_speed: float = 1.0


class ManualOrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: int
    order_type: str = "MARKET"
    limit_price: str | None = None
    stop_loss_price: str | None = None
    take_profit_price: str | None = None


class ManualOrderResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    order_id: str
    symbol: str
    side: str
    quantity: int
    executed_price: str
    total_trade_val: str
    stt_tax: str
    exchange_fees: str
    gst_tax: str
    stamp_duty: str
    net_cash_impact: str
    correlation_id: str
    risk_verdict: str
    narration: str


class StrategyAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_name: str
    trades_count: int
    realized_pnl: str
    win_rate_pct: float
    profit_factor: float


class QuantAnalyticsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    max_drawdown_pct: float
    average_trade_pnl: str
    strategies: list[StrategyAttribution]


class WatchlistConstituent(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    last_price: str
    change_pct: float
    volume: int
    rsi_14: float
    ema_trend: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    volatility_atr: str
    day_high: str
    day_low: str


class WatchlistHeatmapResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_constituents: int
    as_of: str
    items: list[WatchlistConstituent]


class BacktestRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_name: str
    symbol: str
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    initial_capital: float = 10_000_000.0


class BacktestRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    strategy_name: str
    symbol: str
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    trades_executed: int
    final_equity: str
    status: str
    equity_curve: list[dict[str, Any]]


class AlertItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_id: str
    level: str  # "INFO" | "SUCCESS" | "WARNING" | "CRITICAL"
    category: str
    title: str
    message: str
    timestamp: str
    symbol: str | None = None
    correlation_id: str | None = None


class AlertsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int
    alerts: list[AlertItem]


class NotificationWebhookConfigRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    telegram_webhook_url: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    is_enabled: bool = True


class NotificationWebhookConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    telegram_configured: bool
    discord_configured: bool
    is_enabled: bool
    last_dispatched_at: str | None = None
    status: str


class SizingRecommendationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    current_price: float
    atr: float | None = None
    win_rate: float = 0.62
    payoff_ratio: float = 1.85


class SizingRecommendationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    recommended_shares: int
    recommended_notional: str
    target_equity_weight_pct: float
    kelly_fraction: float
    method: str
    stop_loss_price: str
    take_profit_price: str
    risk_reward_ratio: float
