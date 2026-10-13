# ADR-001: Multi-Agent LangGraph Orchestration over Model Context Protocol (MCP) Microservices

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Ayush Kale (Repository Owner & Lead Engineer)
- **Sprint:** Sprint 1 (Foundation)

---

## 1. Context

AlphaTrader is an autonomous algorithmic paper-trading and risk orchestration platform targeting the **NIFTY 50 (NSE)** equity universe. An automated trading system must coordinate four distinct cognitive and operational responsibilities on every market cycle:

1. **Market & Technical Analysis**: Evaluating multi-timeframe price action, momentum, and volatility regimes.
2. **Quantitative Signal & Sizing Synthesis**: Computing expected value, statistical edge, and fractional-Kelly position sizing.
3. **Pre-Trade & Intraday Risk Auditing**: Enforcing capital preservation invariants (drawdown limits, concentration caps, rate limits).
4. **Order Execution & Accounting**: Routing approved paper orders, modeling realistic NSE transaction costs and slippage, and recording double-entry ledger updates.

In naive LLM trading prototypes, these responsibilities are collapsed into a single monolithic agent loop with direct database credentials, shared in-memory state, and ad-hoc Python function tool calls. That design exhibits three fatal production failure modes:

- **Blast-Radius Coupling**: A hallucinated tool argument or unhandled exception in an analytical step can corrupt ledger state or bypass pre-trade risk checks.
- **Non-Deterministic Safety Enforcement**: Allowing an LLM agent to self-police portfolio drawdown or position sizing inside a prompt exposes capital protection to prompt injection, context truncation, and reasoning drift.
- **Lack of Auditability**: Monolithic agent loops lack standardized request/response boundaries, making it impossible to trace a specific market tick (`correlation_id`) through indicator computation, risk approval, and paper fill.

---

## 2. Decision

We adopt a **two-plane architecture** separating **Stateful Agentic Reasoning (LangGraph)** from **Isolated Domain Capability Execution (6 Model Context Protocol Microservices)**, connected asynchronously via **Redpanda (Kafka API)** and backed by **PostgreSQL** and **Redis**:

### Plane 1: LangGraph Multi-Agent Orchestrator (`src/orchestrator`, `src/agents`)
Four specialized agents operate as nodes in a typed, deterministic state machine:
- **Analyst Agent** (`src/agents/analyst`)
- **Quant Agent** (`src/agents/quant`)
- **Risk Agent** (`src/agents/risk`)
- **Execution Agent** (`src/agents/execution`)

Agents do not hold database credentials or execute domain math directly. They interact with the outside world exclusively through strongly typed **Model Context Protocol (MCP)** tool and resource calls carrying a mandatory `correlation_id`.

### Plane 2: Six Isolated MCP Servers (`src/mcp_servers`)
Each domain boundary is encapsulated in an independent MCP server with a single responsibility:
1. **`market_data_streamer`**: Normalizes NIFTY 50 ticks/bars (`Asia/Kolkata`) and publishes to Redpanda.
2. **`technical_analyst`**: Computes pure, side-effect-free technical indicators and volatility regimes.
3. **`quant_strategy_engine`**: Evaluates factor alpha models and fractional-Kelly sizing proposals.
4. **`risk_guardrail`**: A **100% deterministic, non-LLM** rule engine and trading-floor circuit breaker that issues signed approval tokens or triggers a global floor halt.
5. **`paper_execution_broker`**: Simulates NSE order matching, bid-ask spread, slippage, and statutory Indian transaction taxes strictly in paper mode; rejects any order lacking a valid `risk_guardrail` approval token.
6. **`portfolio_ledger`**: Maintains the PostgreSQL double-entry system of record and Redis real-time intraday PnL / high-water-mark state.

---

## 3. Consequences

### Positive
- **Hard Fault Isolation**: The `paper_execution_broker` and `portfolio_ledger` are unreachable for state mutation without passing through `risk_guardrail`.
- **Deterministic Capital Protection**: Even if all four LLM agents hallucinate simultaneously, `risk_guardrail` evaluates invariant math in pure Python (`MAX_DAILY_DRAWDOWN_PCT <= 2.0%`, `MAX_POSITION_SIZE_PCT <= 5.0%`) and halts the floor deterministically.
- **Independent Testability & Scalability**: Every MCP server can be unit-tested, benchmarked, and deployed independently via Docker Compose or Kubernetes Helm charts.
- **End-to-End Traceability**: Every MCP request, Redpanda event, and JSON log line carries a unified `correlation_id` managed via `src/common/logging.py`.

### Negative / Trade-offs
- **Higher Infrastructure Footprint**: Running 6 MCP servers plus Redpanda, PostgreSQL, Redis, Prometheus, and Grafana requires higher local memory and orchestration discipline than a single-process script.
- **Serialization Overhead**: Crossing MCP and Redpanda boundaries adds ~1–3 ms of JSON/Pydantic validation latency per hop, which is acceptable for intraday/swing NIFTY 50 paper trading (as opposed to sub-microsecond co-located HFT).

---

## 4. Alternatives Rejected

| Alternative | Reason for Rejection |
|---|---|
| **Single Autonomous Agent with Direct Function Calling** | Violates separation of duties; no hard boundary between signal generation, risk validation, and execution. |
| **LLM-Based Risk Manager (Prompt-Enforced Risk Limits)** | Probabilistic models cannot guarantee zero false negatives on hard numerical invariants such as a 2.0% daily drawdown circuit breaker. |
| **Unstructured Agent-to-Agent Chat (e.g., Free-Form GroupChat)** | Produces non-deterministic conversation loops, unbounded token usage, and untyped intermediate state compared to a directed LangGraph state graph. |
| **In-Memory SQLite / Local Dict State** | Cannot support concurrent microservice consumers, crash recovery, or production-grade double-entry audit trails required by PostgreSQL + Redis + Redpanda. |
