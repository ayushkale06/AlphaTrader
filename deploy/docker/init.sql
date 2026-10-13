-- =============================================================================
-- AlphaTrader System of Record Database Initialization (PostgreSQL 16)
-- System: Paper Trading Portfolio Ledger & Immutable Risk Audit Trail
-- Governance: ADR-002 (Paper Only), ADR-003 (Typed Schemas), ADR-004 (System of Record)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- 1. Schema Migrations Ledger
-- Tracks schema version history and ensures repeatable, idempotent migrations.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version, description)
VALUES (
    '001_initial_schema',
    'Baseline double-entry ledger, paper orders, fills, positions, snapshots, and immutable risk audit log'
)
ON CONFLICT (version) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. Orders Table (Paper Order Lifecycle & State Machine)
-- Maps directly to src/common/schemas/orders.py (Order / OrderRequest).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_request_id UUID NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE, -- Deterministic idempotency key preventing duplicate order submission.
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type VARCHAR(16) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_LOSS')),
    status VARCHAR(32) NOT NULL CHECK (
        status IN (
            'PENDING_RISK',
            'APPROVED',
            'REJECTED',
            'SUBMITTED',
            'PARTIALLY_FILLED',
            'FILLED',
            'CANCELLED',
            'EXPIRED'
        )
    ),
    quantity NUMERIC(20, 6) NOT NULL CHECK (quantity > 0),
    filled_quantity NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0 AND filled_quantity <= quantity),
    average_fill_price NUMERIC(20, 6) CHECK (average_fill_price IS NULL OR average_fill_price > 0),
    limit_price NUMERIC(20, 6) CHECK (limit_price IS NULL OR limit_price > 0),
    stop_loss_price NUMERIC(20, 6) NOT NULL CHECK (stop_loss_price > 0),
    take_profit_price NUMERIC(20, 6) CHECK (take_profit_price IS NULL OR take_profit_price > 0),
    strategy_name VARCHAR(64) NOT NULL,
    approval_token TEXT,
    correlation_id VARCHAR(128) NOT NULL,
    trading_mode VARCHAR(16) NOT NULL DEFAULT 'PAPER' CHECK (trading_mode = 'PAPER'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT check_order_updated_ge_created CHECK (updated_at >= created_at),
    CONSTRAINT check_limit_price_required_on_limit_order CHECK (
        order_type != 'LIMIT' OR (limit_price IS NOT NULL AND limit_price > 0)
    )
);

-- Fast lookup of order history per instrument ordered by recency.
CREATE INDEX IF NOT EXISTS idx_orders_symbol_created_at ON orders (symbol, created_at DESC);
-- Fast polling and filtering for active open/in-flight orders.
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
-- End-to-end trace auditing linking market ticks to order lifecycle.
CREATE INDEX IF NOT EXISTS idx_orders_correlation_id ON orders (correlation_id);
-- Fast lookup of orders by strategy for quantitative performance attribution.
CREATE INDEX IF NOT EXISTS idx_orders_strategy_name ON orders (strategy_name);

-- -----------------------------------------------------------------------------
-- 3. Fills Table (Simulated Paper Trade Executions & Statutory Costs)
-- Maps directly to src/common/schemas/orders.py (Fill).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fills (
    fill_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(order_id) ON DELETE RESTRICT,
    order_request_id UUID NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    fill_price NUMERIC(20, 6) NOT NULL CHECK (fill_price > 0),
    fill_quantity NUMERIC(20, 6) NOT NULL CHECK (fill_quantity > 0),
    slippage_bps NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (slippage_bps >= 0),
    stt_tax NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (stt_tax >= 0),
    exchange_fees NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (exchange_fees >= 0),
    gst_tax NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (gst_tax >= 0),
    stamp_duty NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (stamp_duty >= 0),
    total_fees NUMERIC(20, 6) NOT NULL DEFAULT 0 CHECK (total_fees >= 0),
    correlation_id VARCHAR(128) NOT NULL,
    trading_mode VARCHAR(16) NOT NULL DEFAULT 'PAPER' CHECK (trading_mode = 'PAPER'),
    filled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT check_fill_total_fees_sum CHECK (
        total_fees = stt_tax + exchange_fees + gst_tax + stamp_duty
    )
);

-- Fast retrieval of trade executions per symbol for position recalculation and tax reporting.
CREATE INDEX IF NOT EXISTS idx_fills_symbol_filled_at ON fills (symbol, filled_at DESC);
-- Rapid join of executions against parent orders for partial-fill tracking.
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills (order_id);
-- Trace correlation linking broker fill events to originating analysis and risk decisions.
CREATE INDEX IF NOT EXISTS idx_fills_correlation_id ON fills (correlation_id);

-- -----------------------------------------------------------------------------
-- 4. Positions Table (Aggregated Open/Flat Paper Positions)
-- Maps directly to src/common/schemas/portfolio.py (Position).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(32) NOT NULL UNIQUE,
    quantity NUMERIC(20, 6) NOT NULL, -- Signed quantity (positive=long, negative=short, 0=flat).
    average_entry_price NUMERIC(20, 6) NOT NULL CHECK (average_entry_price > 0),
    mark_price NUMERIC(20, 6) NOT NULL CHECK (mark_price > 0),
    market_value NUMERIC(20, 6) NOT NULL CHECK (market_value >= 0),
    unrealized_pnl NUMERIC(20, 6) NOT NULL,
    realized_pnl NUMERIC(20, 6) NOT NULL DEFAULT 0,
    trading_mode VARCHAR(16) NOT NULL DEFAULT 'PAPER' CHECK (trading_mode = 'PAPER'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT check_position_market_value CHECK (market_value = ABS(quantity) * mark_price)
);

-- Primary lookup key for real-time position updates and risk exposure calculations.
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (symbol);
-- Fast query of recently modified positions for portfolio snapshot generation.
CREATE INDEX IF NOT EXISTS idx_positions_updated_at ON positions (updated_at DESC);

-- -----------------------------------------------------------------------------
-- 5. Portfolio Snapshots Table (Point-in-Time Ledger State)
-- Maps directly to src/common/schemas/portfolio.py (PortfolioSnapshot).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    as_of TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cash NUMERIC(20, 6) NOT NULL CHECK (cash >= 0),
    equity NUMERIC(20, 6) NOT NULL CHECK (equity > 0),
    exposure NUMERIC(20, 6) NOT NULL CHECK (exposure >= 0),
    realized_pnl NUMERIC(20, 6) NOT NULL,
    unrealized_pnl NUMERIC(20, 6) NOT NULL,
    drawdown NUMERIC(10, 6) NOT NULL CHECK (drawdown >= 0 AND drawdown <= 100),
    high_water_mark NUMERIC(20, 6) NOT NULL CHECK (high_water_mark > 0),
    positions JSONB NOT NULL DEFAULT '[]'::jsonb,
    correlation_id VARCHAR(128) NOT NULL,
    trading_mode VARCHAR(16) NOT NULL DEFAULT 'PAPER' CHECK (trading_mode = 'PAPER'),
    CONSTRAINT check_snapshot_hwm_ge_equity CHECK (high_water_mark >= equity)
);

-- High-performance time-series retrieval of historical portfolio equity and drawdown.
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_as_of ON portfolio_snapshots (as_of DESC);
-- Audit trace linking portfolio state mutations back to market events.
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_correlation_id ON portfolio_snapshots (correlation_id);

-- -----------------------------------------------------------------------------
-- 6. Risk Audit Log Table (IMMUTABLE, APPEND-ONLY Risk Decisions)
-- Maps directly to src/common/schemas/risk.py (RiskDecision).
-- A PostgreSQL trigger strictly raises an exception on any UPDATE or DELETE attempt.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_audit_log (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_request_id UUID NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    verdict VARCHAR(16) NOT NULL CHECK (verdict IN ('APPROVE', 'REJECT', 'HALT')),
    rule_fired VARCHAR(128) NOT NULL,
    reason_code VARCHAR(64) NOT NULL CHECK (
        reason_code IN (
            'WITHIN_LIMITS',
            'MAX_DAILY_DRAWDOWN_BREACH',
            'MAX_POSITION_SIZE_BREACH',
            'MAX_OPEN_POSITIONS_BREACH',
            'ORDER_RATE_LIMIT_EXCEEDED',
            'MARKET_CLOSED',
            'FLOOR_ALREADY_HALTED',
            'MISSING_STOP_LOSS',
            'MANUAL_KILL_SWITCH'
        )
    ),
    inputs_snapshot JSONB NOT NULL,
    approval_token TEXT,
    message TEXT NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    trading_mode VARCHAR(16) NOT NULL DEFAULT 'PAPER' CHECK (trading_mode = 'PAPER'),
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast inspection of risk verdicts per symbol over time.
CREATE INDEX IF NOT EXISTS idx_risk_audit_symbol_evaluated_at ON risk_audit_log (symbol, evaluated_at DESC);
-- Rapid filtering of risk rejections and floor-halt incidents.
CREATE INDEX IF NOT EXISTS idx_risk_audit_verdict ON risk_audit_log (verdict);
-- Trace linkage for compliance audits connecting proposals to risk gate decisions.
CREATE INDEX IF NOT EXISTS idx_risk_audit_correlation_id ON risk_audit_log (correlation_id);
-- Lookup of pre-trade risk evaluation for any candidate order request.
CREATE INDEX IF NOT EXISTS idx_risk_audit_order_request_id ON risk_audit_log (order_request_id);

-- Enforce APPEND-ONLY immutability: block UPDATE and DELETE on risk_audit_log.
CREATE OR REPLACE FUNCTION prevent_risk_audit_log_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'risk_audit_log is append-only: % operations are prohibited (ADR-004)', TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_risk_audit_log_modification ON risk_audit_log;
CREATE TRIGGER trg_prevent_risk_audit_log_modification
    BEFORE UPDATE OR DELETE ON risk_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_risk_audit_log_modification();
