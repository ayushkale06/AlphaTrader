# ADR-009: Paper Execution Broker, Realistic Matching & Indian Statutory Costs

## Status
Accepted

## Context
In Sprint 9, AlphaTrader introduces **MCP Server 5: Paper Execution Broker** (`src/mcp_servers/paper_execution_broker/`). The system operates strictly under simulated paper trading mode (`ADR-002: Paper Trading Safe Mode and Execution Guardrails`).

To maintain quantitative fidelity and structural realism without real market connectivity, the execution broker must satisfy five primary architectural requirements:
1. **Cryptographic Risk Token Enforcement:** Every incoming candidate order proposal MUST include a valid `RiskApprovalToken` issued by the Pre-Trade Risk Guardrail (`src/mcp_servers/risk_guardrail/token.py`). Orders with missing, malformed, forged, mismatched, or expired tokens must fail immediately with a deterministic rejection status.
2. **Realistic Fill Simulation (Matching Engine):**
   - **Market Orders:** Executed by crossing the bid-ask half-spread, plus a volume/volatility-proportional slippage model.
   - **Limit Orders:** Compared against current market price (LTP / ask / bid). Buy limit orders execute only if the effective ask price $\le \text{limit\_price}$; sell limit orders execute only if effective bid price $\ge \text{limit\_price}$. Otherwise, they are rejected or remain unfillable.
3. **Exact Indian Statutory Cost Accounting:**
   - Transaction friction must accurately compute Indian regulatory and statutory levies:
     - **STT (Securities Transaction Tax):** $0.10\%$ on both BUY and SELL for delivery trades; $0.025\%$ on SELL for intraday.
     - **Exchange Turnover Charges:** $0.00325\%$ ($0.0000325$) on total turnover.
     - **SEBI Turnover Charges:** $\text{INR } 10 \text{ per crore}$ ($0.000001$) on turnover.
     - **Stamp Duty:** $0.015\%$ ($0.00015$) levied strictly on BUY orders.
     - **GST:** $18\%$ on taxable service components (exchange fees + SEBI fees; brokerage is tracked in the ledger).
     - Invariant: `Fill.total_fees == stt_tax + exchange_fees + gst_tax + stamp_duty`, evaluated with exact `Decimal` precision rounded half-up to 2 decimal places.
4. **Publish-Before-Return Verification:** Fills must be published to Kafka topic `orders.fills` as `EventEnvelope[Fill]` conforming to `contracts.py` with synchronous flush verification before responding to the MCP client.
5. **FastMCP Server Interface:** Standard JSON-RPC FastMCP server providing tools:
   - `execute_paper_order(order_request_id, symbol, side, quantity, order_type, limit_price, stop_loss_price, approval_token, current_market_price)`
   - `get_paper_order_status(order_id)`
   - `get_broker_health()`

## Consequences
- **Positive:** Guarantees zero execution can bypass risk approval tokens. Accurately simulates the high friction drag of Indian equity delivery and cash trading.
- **Negative:** Orders without active real-time ticks or reference market prices cannot simulate fill pricing and must be explicitly provided with reference market quote or anchor candle.
