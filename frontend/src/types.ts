export interface PortfolioSummary {
  equity: string;
  cash: string;
  exposure: string;
  realized_pnl: string;
  unrealized_pnl: string;
  current_drawdown_pct: string;
  high_water_mark: string;
  revision: number;
  as_of: string;
  trading_mode: string;
}

export interface PositionItem {
  symbol: string;
  quantity: string;
  average_entry_price: string;
  mark_price: string;
  market_value: string;
  unrealized_pnl: string;
  realized_pnl: string;
  updated_at: string;
}

export interface PositionsResponse {
  total: number;
  items: PositionItem[];
}

export interface Quote {
  symbol: string;
  last_price: string;
  volume: number;
  bid_price: string | null;
  ask_price: string | null;
  bid_qty: number | null;
  ask_qty: number | null;
  timestamp: string;
  feed_mode: string;
  provenance: string;
}

export interface Candle {
  open_time: string;
  close_time: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
  interval: string;
}

export interface CandlesResponse {
  symbol: string;
  interval: string;
  count: number;
  candles: Candle[];
}

export interface OrderItem {
  order_id: string;
  order_request_id: string;
  idempotency_key: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  order_type: string;
  status: string;
  quantity: string;
  filled_quantity: string;
  average_fill_price: string | null;
  limit_price: string | null;
  stop_loss_price: string;
  take_profit_price: string | null;
  strategy_name: string;
  correlation_id: string;
  created_at: string;
  updated_at: string;
}

export interface OrdersResponse {
  total: number;
  limit: number;
  offset: number;
  items: OrderItem[];
}

export interface FillItem {
  fill_id: string;
  order_id: string;
  order_request_id: string;
  symbol: string;
  side: string;
  fill_price: string;
  fill_quantity: string;
  slippage_bps: string;
  stt_tax: string;
  exchange_fees: string;
  gst_tax: string;
  stamp_duty: string;
  total_fees: string;
  correlation_id: string;
  filled_at: string;
}

export interface OrderFillsResponse {
  order_id: string;
  fills: FillItem[];
}

export interface ConfiguredLimits {
  max_daily_drawdown_pct: string;
  max_position_size_pct: string;
  max_open_positions: number;
  max_orders_per_minute: number;
}

export interface RiskTelemetry {
  floor_state: 'ACTIVE' | 'HALTED' | 'UNKNOWN';
  is_floor_halted: boolean;
  current_drawdown_pct: string;
  high_water_mark_inr: string;
  equity_inr: string;
  start_of_day_equity_inr: string;
  session_date: string;
  open_positions_count: number;
  configured_limits: ConfiguredLimits;
}

export interface RiskAuditItem {
  decision_id: string;
  order_request_id: string;
  symbol: string;
  verdict: 'APPROVE' | 'REJECT' | 'HALT';
  rule_fired: string;
  reason_code: string;
  inputs_snapshot: Record<string, unknown>;
  approval_token: string | null;
  message: string;
  correlation_id: string;
  evaluated_at: string;
}

export interface RiskAuditResponse {
  total: number;
  limit: number;
  offset: number;
  items: RiskAuditItem[];
}

export interface SystemHealth {
  status: 'HEALTHY' | 'DEGRADED';
  version: string;
  trading_mode: string;
  services: {
    postgres: boolean;
    redis: boolean;
  };
  feed_heartbeats: Record<string, unknown>;
}

// --- Extended Feature Parity Types ---

export interface TechnicalAnalysisResponse {
  symbol: string;
  interval: string;
  indicators: Record<string, string>;
  signal: {
    indicator?: string;
    action?: string;
    confidence?: number;
    reasoning?: string;
    [key: string]: unknown;
  } | null;
  explanation: Record<string, unknown> | null;
}

export interface QuantStrategyResponse {
  symbol: string;
  interval: string;
  registered_strategies: string[];
  latest_signal: Record<string, unknown> | null;
  sizing_proposal: Record<string, unknown> | null;
}

export interface BacktestReportItem {
  report_id: string;
  strategy_name: string;
  strategy_version: string;
  symbol: string;
  date_range: string;
  data_source: string;
  edge_basis: string;
  is_out_of_sample: boolean;
  path: string;
  summary_markdown: string;
}

export interface BacktestRegistryResponse {
  runs_evaluated: number;
  reports: BacktestReportItem[];
}

export interface LedgerPosting {
  account_code: string;
  account_name: string;
  account_type: string;
  direction: 'DEBIT' | 'CREDIT';
  amount: string;
  sequence_num: number;
}

export interface LedgerEntryItem {
  entry_id: string;
  event_id: string;
  fill_id: string | null;
  narration: string;
  posted_at: string;
  correlation_id: string;
  postings: LedgerPosting[];
}

export interface LedgerJournalResponse {
  total: number;
  limit: number;
  offset: number;
  current_revision: number;
  items: LedgerEntryItem[];
}

export interface AgentWorkflowState {
  stage: string;
  trading_mode: string;
  symbol: string;
  last_event_correlation_id: string;
  agents: Record<string, string>;
  pipeline_steps: Array<{
    agent: string;
    step: string;
    status: string;
    description: string;
  }>;
}

export interface AuditChainVerificationResponse {
  algorithm: string;
  total_records_verified: number;
  is_unbroken: boolean;
  genesis_hash: string;
  latest_hash: string;
  message: string;
}

export interface SystemConfigResponse {
  app_env: string;
  trading_mode: string;
  live_trading_enabled: boolean;
  market_timezone: string;
  nse_market_feed_mode: string;
  risk_limits: ConfiguredLimits;
  schema_version: string;
  app_version: string;
  adrs: Array<{
    id: string;
    title: string;
    status: string;
  }>;
}
