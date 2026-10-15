# ADR-010: PostgreSQL Double-Entry Portfolio Ledger & Redis Projection

## Status
Accepted

## Context
Sprint 10 introduces **MCP Server 6: Portfolio Ledger** (`src/mcp_servers/portfolio_ledger/`). Under `ADR-002: Paper Trading Safe Mode and Execution Guardrails`, AlphaTrader operates strictly in simulated paper mode. 

To maintain quantitative integrity, auditability, and mathematical consistency across multi-agent executions, portfolio accounting must satisfy six fundamental architectural requirements:

1. **PostgreSQL as System-of-Record (Double-Entry Bookkeeping):**
   - Every economic event (genesis funding, execution fill, fees) creates an immutable `journal_entries` header and $N \ge 2$ balanced `journal_postings` lines.
   - Core accounting invariant:
     $$\sum \text{Debits} \equiv \sum \text{Credits}$$
   - Database-level deferred constraint triggers enforce at commit time:
     - No entry can commit with 0 postings.
     - An entry must have $\ge 2$ postings with at least one `DEBIT` and at least one `CREDIT`.
     - Exact equality of debit and credit sums.
     - Row- and statement-level triggers block `UPDATE`, `DELETE`, and `TRUNCATE` operations on posted records.

2. **Immediate Fee Expensing & Authoritative Inventory Cost Allocation:**
   - Transaction fees and statutory taxes (STT, exchange turnover fees, SEBI charges, stamp duty, GST) are expensed immediately to expense accounts (`5101`, `5102`, `5103`, `5104`) and excluded from inventory Weighted Average Cost (WAC).
   - Inventory book cost for symbol $S$ is authoritatively defined as the net balance of ledger account `Asset:Inventory:{SYMBOL}` (`1100:{SYMBOL}`).
   - Partial sales allocate cost proportionally:
     $$\text{Inventory Credit} = \left(B_{\text{inv}}(S) \times \frac{Q_{\text{sell}}}{Q_{\text{rem}}(S)}\right).\text{quantize}(\text{Decimal}('0.0001'), \text{ROUND\_HALF\_UP})$$
   - Full liquidation credits the **exact remaining book balance** of `1100:{SYMBOL}`, ensuring the inventory account returns identically to `0.0000` with zero rounding residual or balance divergence.

3. **Strict No-Short Policy Enforcement:**
   - In accordance with project cash delivery rules, `quantity < 0` is strictly forbidden.
   - Any sell fill that exceeds available inventory raises `InsufficientInventoryError`, aborts the database transaction, and routes the message to `orders.fills.dlq`.

4. **Event Ingestion Idempotency & Tamper Detection:**
   - `processed_events` records `(event_id, topic, correlation_id, payload_hash)`.
   - Replay duplicates with identical payload hash are cleanly acknowledged without duplicate postings.
   - Replay collisions with different payload hashes are rejected as poison pills and routed to DLQ.

5. **Redis Read Projection with Monotonic Revision Ordering:**
   - PostgreSQL is the sole authoritative source of truth.
   - Redis holds an ephemeral read projection under `alphatrader:portfolio:*`.
   - Revision ordering is serialized via PostgreSQL transaction lock and recorded as a 20-digit zero-padded string to prevent sequence inversion and unsafe Lua floating-point comparisons.
   - Closed positions are atomically deleted from Redis.
   - Rebuild operations read PostgreSQL under `REPEATABLE READ` snapshot isolation and will not overwrite newer projections.

6. **FastMCP Server 6 Interface:**
   - `get_portfolio_summary`: Returns cash, equity, exposure, realized/unrealized PnL, drawdown, HWM, and stale marks count.
   - `get_open_positions`: Returns open positions with quantity, entry WAC, mark price, market value, and freshness.
   - `get_audit_trail`: Paginated, descending audit log of journal entries and postings.

## Consequences
- **Positive:** Mathematically proves zero balance drift between inventory accounts and positions. Completely eliminates phantom gains and rounding drift. Survives process crashes and Redis outages safely.
- **Negative:** Requires strict database constraints and commit-time verification, adding sub-millisecond overhead to fill processing.
