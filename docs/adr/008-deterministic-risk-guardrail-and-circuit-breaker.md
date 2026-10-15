# ADR-008: Deterministic Risk Guardrail, Intraday Drawdown Monitor, and Circuit Breaker

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** AlphaTrader Architecture Team, Senior Engineering, Risk Officer
- **Technical Context:** Sprint 7 & 8 — MCP Server 4 (Deterministic Risk Guardrail & Floor Circuit Breaker)

---

## 1. Context and Problem Statement

AlphaTrader operates an autonomous multi-agent trading floor for Indian equities (NSE NIFTY 50). While quant strategies propose order candidates (`OrderRequest`), capital preservation invariants must be enforced unconditionally.

Large Language Models (LLMs) are probabilistic and vulnerable to hallucination, prompt injection, and non-deterministic degradation under distribution shifts. Therefore, pre-trade risk evaluation, intraday drawdown monitoring, and emergency floor halting must execute in **strictly deterministic, sub-millisecond, pure-Python and Lua code with zero LLM imports**.

---

## 2. Decision Drivers

- **Zero LLM Dependency:** No probabilistic models in the critical path of capital protection.
- **Fail-Closed Design:** If risk state in Redis is unavailable, corrupt, or stale, candidate orders are rejected unconditionally.
- **Latching Circuit Breaker:** An emergency floor halt (`alphatrader:floor_halted`) can **NEVER be cleared automatically** by session rollover, date changes, or service restarts. Unlatching requires an explicit, authenticated operator override.
- **Monotonic High-Water Mark (HWM):** Out-of-order or duplicate portfolio snapshot events must never artificially raise HWM or erase an intraday drawdown breach.
- **Microsecond State Transitions:** Redis Lua scripts ensure atomic evaluation and mutation of equity, HWM, and drawdown.
- **Cryptographic Approval Tokens:** Orders passing risk checks receive an HMAC-SHA256 signed `RiskApprovalToken` binding `(order_request_id, symbol, side, quantity, limit_price, expires_at)`. Paper execution brokers reject unapproved or expired orders.

---

## 3. Risk State Architecture & Invariants

### 3.1 State Representation & Redis Keys
All risk state is persisted in Redis under the `alphatrader:risk:` namespace:
- `alphatrader:risk:hwm`: Global High-Water Mark equity in INR (stringified `Decimal`).
- `alphatrader:risk:equity`: Current portfolio equity in INR.
- `alphatrader:risk:start_of_day_equity`: Equity baseline recorded at the open of the current trading session.
- `alphatrader:risk:session_date`: Current trading date (`YYYY-MM-DD`) in `Asia/Kolkata`.
- `alphatrader:risk:last_event_seq`: Monotonically increasing sequence number / timestamp of the latest applied portfolio update.
- `alphatrader:risk:positions`: Redis Hash mapping `symbol -> notional_inr`.
- `alphatrader:floor_halted`: Global circuit-breaker latch (`"1"` for halted, `"0"` or absent for normal).

### 3.2 Drawdown Calculation Formula
Daily drawdown percentage is computed relative to the **session start-of-day baseline**, whereas the global drawdown is computed relative to **HWM**:
$$\text{drawdown\_val} = \max(0, \text{HWM} - \text{equity})$$
$$\text{current\_drawdown\_pct} = \left(\frac{\text{drawdown\_val}}{\text{HWM}}\right) \times 100$$
If $\text{current\_drawdown\_pct} \ge \text{MAX\_DAILY\_DRAWDOWN\_PCT}$ ($2.0\%$), the evaluation triggers an immediate **HALT** verdict and latches `alphatrader:floor_halted = "1"`.

### 3.3 Trading Session Rollover Semantics
- Evaluated strictly in `Asia/Kolkata` exchange timezone.
- When `session_date` changes:
  1. `start_of_day_equity` is updated to current `equity`.
  2. `session_date` is updated to the new date.
  3. **LATCH INVARIANT:** If `alphatrader:floor_halted == "1"`, the halt status is **PRESERVED**. Rollover does not unlatch a circuit breaker.

### 3.4 Event Ordering, Clock Skew, and Concurrency Semantics
- Every portfolio equity update carries an ISO UTC event timestamp (`event_ts`).
- The atomic Redis Lua script (`LUA_UPDATE_RISK_STATE`) enforces strict monotonicity against `alphatrader:risk:last_event_ts`:
  1. **Duplicate Events (`event_ts == last_event_ts`)**: Rejected with `STALE_OR_DUPLICATE_EVENT` without mutating equity, HWM, or halt state.
  2. **Clock Skew / Out-of-Order Deliveries (`event_ts < last_event_ts`)**: Rejected without mutating state to prevent delayed or replayed packets from corrupting the high-water mark or concealing an intraday breach.
  3. **Strictly Monotonic Events (`event_ts > last_event_ts`)**: Accepted, updating equity, advancing HWM monotonically, and latching floor halt if drawdown $\ge 2.0\%$.
- Concurrency across multiple workers or processes is handled atomically by Redis single-threaded Lua script execution.

### 3.5 Circuit Breaker Kafka Failure, Event Identity, and Limitations
- Circuit breaker trip is a dual-write: Redis latch (`alphatrader:floor_halted = "1"`) followed by `SystemEvent` publish to `system.events`.
- **Failure Handling**:
  1. Redis latch is written **first**. If Kafka publish fails or times out, the halt latch in Redis remains active (`"1"`).
  2. The failure does not unlatch Redis or clear the breach state.
  3. **Scope Limitation**: Durable post-restart Kafka publication recovery and transactional outbox mechanisms are **explicitly outside the scope of Sprint 8**. No claims of guaranteed eventual delivery to Kafka or exactly-once delivery across broker crashes are made.
  4. **Fail-Closed Protection**: The independent Redis latch guarantees that even if Kafka delivery fails completely and the process restarts, the trading floor remains halted and subsequent candidate orders are rejected unconditionally.
  5. When retry is performed within the active session, callers must supply a stable `correlation_id` to prevent duplicate event identity.

---

## 4. MCP Server 4 FastMCP Tools

1. `validate_order_proposal(order_json: str) -> str`:
   - Validates candidate `OrderRequest` against floor halt, drawdown, concentration ($\le 5\%$), open positions ($\le 10$), rate limits ($\le 30/\text{min}$), and stop-loss bounds.
   - Emits `RiskDecision` to `risk.decisions` topic.
   - Returns JSON `RiskDecision`.
2. `get_floor_risk_status() -> str`:
   - Returns read-only operational telemetry: `is_floor_halted`, `current_drawdown_pct`, `hwm`, `equity`, `start_of_day_equity`, and `open_positions_count`.
   - Never exposes secret keys or signing material.
3. `trigger_manual_floor_halt(reason: str, operator_id: str) -> str`:
   - **STATUS: DISABLED**.
   - Manual halt via caller-supplied identity string is disabled because MCP STDIO/SSE transport lacks cryptographically verified caller authentication.
   - Invocations return `{"success": false, "error": "OPERATION_DISABLED: Manual floor halt is disabled pending trusted authentication architecture."}` and do not mutate Redis or publish Kafka events.
   - **Authentication Architecture Proposal (Smallest Safe Design)**:
     - Require caller to provide an Ed25519/HMAC signed payload: `(operator_id, timestamp, nonce, reason, signature)` verified against an authorized operator public key registry or mutual TLS client certificate identity at the transport layer.
     - Until signed operator tokens or mTLS are introduced, manual halt operations must be executed directly via internal operational control CLI with local OS-level privilege verification.

---

## 5. Security and Compliance

- **Zero LLM Imports:** Enforced by static AST lint tests (`test_zero_llm_imports_static_ast_check`).
- **Paper Trading Only (ADR-002):** Rejects any live execution configuration. Zero broker credentials exist.
- **Fail-Closed:** Missing Redis state results in immediate `RiskVerdict.REJECT` with reason `FLOOR_ALREADY_HALTED` or `MISSING_RISK_STATE`.
