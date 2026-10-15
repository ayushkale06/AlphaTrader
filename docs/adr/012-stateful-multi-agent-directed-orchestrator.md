# ADR-012: Stateful Multi-Agent Directed Orchestrator and Checkpoint Protocol

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** Ayush Kale (Lead Engineer)
- **Sprint:** Sprint 12 (Stateful Multi-Agent Orchestrator)

---

## 1. Context

AlphaTrader's autonomous trading floor coordinates four specialized agents:
- `AnalystAgent` (Server 2: Technical Analyst)
- `QuantAgent` (Server 3: Quant Strategy Engine)
- `RiskAgent` (Server 4: Risk Guardrail)
- `ExecutionAgent` (Server 5: Paper Execution Broker)

Sprint 12 requires a deterministic, stateful workflow coordinating these agents, with explicit early-exit paths for floor circuit-breaker halts, neutral/flat market signals, zero-sizing proposals, risk rejections, and execution failures. The workflow must maintain unbroken event lineage (`correlation_id`, `trigger_event_id`, `causation_id`) and provide durable checkpointing without introducing heavyweight third-party graph dependencies.

---

## 2. Decision

We implement `TradingFloorOrchestrator` in `src/orchestrator/graph.py` accompanied by `TradingFloorState` (`src/orchestrator/state.py`) and `OrchestratorCheckpointer` (`src/orchestrator/checkpoint.py`):

1. **Typed State Machine:**
   `TradingFloorState` is a strictly typed Python dataclass capturing the lifecycle stage (`OrchestratorStage`), market inputs, agent artifacts, rejection reasons, error traces, and timestamps.
2. **Directed Workflow & Short-Circuit Gates:**
   - **Floor Circuit Breaker Guard:** Checks Redis key `alphatrader:floor_halted`. If active, immediately halts the traversal without invoking analytical agents.
   - **Analyst Node:** Invokes `AnalystAgent`. If `Direction.FLAT`, immediately transitions to `COMPLETED` without placing an order.
   - **Quant Node:** Invokes `QuantAgent`. If allocation is zero or no order proposal is generated, concludes traversal without invoking risk checks.
   - **Risk Node:** Invokes `RiskAgent`. Verifies server-issued `RiskApprovalToken`. If rejected, transitions to `REJECTED` and preserves rejection reasons.
   - **Execution Node:** Invokes `ExecutionAgent` with the HMAC approval token. Dispatches to `PaperExecutionBroker` and updates state to `COMPLETED` on fill or `REJECTED` on broker decline.
3. **Audit Trail & Lineage:**
   `correlation_id` is immutable across the run. `causation_id` dynamically tracks the specific event/decision ID causing each hop.
4. **Resilient Checkpointing:**
   `OrchestratorCheckpointer` serializes state snapshots to Redis under `alphatrader:orchestrator:state:<correlation_id>` with TTL expiration (default 86,400s), with graceful in-memory fallback on Redis drop.
5. **Hard Invariant:**
   `TRADING_MODE` must strictly equal `"PAPER"`. Non-paper configurations raise `ValueError` immediately upon initialization.

---

## 3. Consequences

### Positive
- Zero external runtime dependency overhead (`langgraph` dependency avoided in favor of typed Python state machine).
- Deterministic execution path with 100% auditable checkpoints at each stage boundary.
- Complete branch coverage ($\ge 90\%$, achieved $98.74\%$).

### Trade-offs
- Custom state graph requires explicit node error-handling and step dispatch methods rather than relying on LangGraph DSL macros.
