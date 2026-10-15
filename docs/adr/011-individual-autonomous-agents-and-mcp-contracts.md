# ADR-011: Individual Autonomous Trading Floor Agents & Structured MCP Contracts

## Status
Accepted

## Context
Sprint 11 introduces the four specialized autonomous agent wrappers (`src/agents/`):
1. **Analyst Agent (`AnalystAgent`):** Consumes MCP Server 2 (`technical-analyst`) to evaluate market indicators, regime, and generate structured `TechnicalSignal`s.
2. **Quant Agent (`QuantAgent`):** Consumes MCP Server 3 (`quant-strategy-engine`) to score multi-factor alpha models, apply ATR bracket stops and fractional-Kelly sizing, and construct candidate `OrderRequest` proposals.
3. **Risk Agent (`RiskAgent`):** Consumes MCP Server 4 (`risk-guardrail`) to evaluate pre-trade guardrails, concentration limits, and circuit breakers, retrieving signed HMAC `RiskApprovalToken`s.
4. **Execution Agent (`ExecutionAgent`):** Consumes MCP Server 5 (`paper_execution_broker`) to match paper orders, compute exact Indian statutory taxes, and handle ambiguous execution timeout reconciliations.

## Architectural Invariants & Decisions

1. **Strict Provenance & Event Tracing:**
   - The platform root `correlation_id` is immutable and carried across all four agents.
   - `causation_id` strictly references the immediate preceding event's `event_id`.
   - `OrderRequest.source_signal_ids` explicitly links back to the originating `TechnicalSignal.signal_id`.
   - Tool calls are synchronous RPCs, not durable events; agents never fabricate artificial `event_id`s for intermediate RPC responses.

2. **Server-Side Security & Zero-LLM Risk Overrides:**
   - Pre-trade risk approval authority remains 100% owned by MCP Server 4.
   - Agents never forge, infer, or modify `approval_token`.
   - LLMs (if enabled) are strictly restricted to narrative rationale generation and cannot alter orders, bypass risk rejections, or synthesize tokens.

3. **Ambiguous Outcome Reconciliation & Safe Retries:**
   - In-memory sets are insufficient for duplicate prevention across restarts.
   - On dispatch timeout or connection failure, `ExecutionAgent` treats the outcome as ambiguous and reconciles via `get_paper_order_status` using `order_request_id` before attempting any resubmission. Blind order resubmission is strictly prohibited.

4. **Deterministic Behavior Parity:**
   - All financial metrics are strictly typed as `Decimal` with `ROUND_HALF_UP` precision.
   - Prohibits binary float conversion.
   - Agents execute with 100% determinism when LLM is unavailable or unconfigured.

## Consequences
- **Positive:** Enables multi-agent modular composition in LangGraph (Sprint 12) while preserving cryptographic security, auditability, and exact mathematical consistency.
- **Negative:** Requires strict Pydantic model validation and structured error handling at each agent boundary.
