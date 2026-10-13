# AlphaTrader System Architecture Specification

> **Document Version:** `0.1.0-alpha` (Sprint 1 Foundation)  
> **Safety Classification:** **PAPER TRADING ONLY** ([ADR-002](adr/002-paper-trading-only.md))  
> **Target Universe:** NSE NIFTY 50 Constituents (`Asia/Kolkata` / IST)

---

## 1. Architectural Overview

AlphaTrader is a distributed, event-driven, multi-agent algorithmic paper-trading and risk orchestration platform. It separates **probabilistic agentic analysis** from **deterministic risk enforcement and execution** using two decoupled planes:

1. **Cognitive Orchestration Plane (`src/orchestrator`, `src/agents`)**: A stateful **LangGraph** workflow coordinating four specialized agents (**Analyst**, **Quant**, **Risk**, **Execution**).
2. **Domain Capability & Safety Plane (`src/mcp_servers`)**: Six isolated **Model Context Protocol (MCP)** microservices communicating over typed schemas, backed by **Redpanda (Kafka API)** for high-throughput event streaming, **PostgreSQL** for double-entry ledger persistence, and **Redis** for sub-millisecond state caching and circuit-breaker flags.

---

## 2. System Component Diagram

```mermaid
flowchart TB
    subgraph External["Read-Only Market Data Sources"]
        NSE["NSE NIFTY 50 Feed (Simulated / Replay / Read-Only Live)"]
    end

    subgraph EventBus["Event Streaming Backbone"]
        RP["Redpanda Cluster (Kafka API)\nTopics: market.ticks.nifty50 | signals.candidate | risk.decisions | risk.halt | orders.filled.paper"]
    end

    subgraph MCPPlane["Six Isolated Model Context Protocol (MCP) Microservices"]
        MCP1["MCP 1: market_data_streamer\n(Tick Normalization & Ingestion)"]
        MCP2["MCP 2: technical_analyst\n(EMA, RSI, ATR, VWAP, Regime)"]
        MCP3["MCP 3: quant_strategy_engine\n(Factor Alpha & Fractional Kelly Sizing)"]
        MCP4["MCP 4: risk_guardrail [NON-LLM]\n(Deterministic Invariants & Floor Halt)"]
        MCP5["MCP 5: paper_execution_broker\n(Simulated Matching, Slippage & NSE Taxes)"]
        MCP6["MCP 6: portfolio_ledger\n(Double-Entry Accounting & PnL State)"]
    end

    subgraph AgentPlane["LangGraph Stateful Multi-Agent Orchestrator"]
        A1["1. Analyst Agent\n(src/agents/analyst)"]
        A2["2. Quant Agent\n(src/agents/quant)"]
        A3["3. Risk Agent\n(src/agents/risk)"]
        A4["4. Execution Agent\n(src/agents/execution)"]
        A1 --> A2 --> A3 --> A4
    end

    subgraph DataPlane["State & Persistence Stores"]
        PG[("PostgreSQL\nDouble-Entry Ledger & Audit Trail")]
        RD[("Redis\nIntraday PnL, High-Water Mark & Kill-Switch")]
    end

    subgraph ObsPlane["Observability & Audit Telemetry"]
        OTEL["OpenTelemetry Collector"]
        PROM["Prometheus Metrics"]
        GRAF["Grafana Dashboards"]
        OTEL --> PROM --> GRAF
    end

    NSE --> MCP1
    MCP1 --> RP
    RP --> MCP2 & MCP3 & MCP6

    A1 <-->|"MCP Tools"| MCP2
    A2 <-->|"MCP Tools"| MCP3
    A3 <-->|"MCP Tools"| MCP4
    A4 <-->|"MCP Tools (Requires Risk Token)"| MCP5

    MCP4 <-->|"Read State / Set Halt Flag"| RD
    MCP4 <-->|"Read Exposure"| MCP6
    MCP5 -->|"orders.filled.paper"| RP
    MCP6 <--> PG
    MCP6 <--> RD

    MCP1 & MCP2 & MCP3 & MCP4 & MCP5 & MCP6 -.->|"JSON Logs (correlation_id) + Metrics"| OTEL
```

---

## 3. End-to-End Event Flow: Tick to Paper Fill

Every market tick ingested by the system is assigned a UUIDv4 `correlation_id` (implemented in [`src/common/logging.py`](../src/common/logging.py)) that propagates across every Redpanda event header, LangGraph state transition, MCP tool call, and structured JSON log line.

```mermaid
sequenceDiagram
    autonumber
    participant Feed as NSE Market Feed
    participant Streamer as MCP 1: Market Data Streamer
    participant Bus as Redpanda (Event Bus)
    participant Orch as LangGraph Orchestrator
    participant Tech as MCP 2: Technical Analyst
    participant Quant as MCP 3: Quant Engine
    participant Guard as MCP 4: Risk Guardrail (Non-LLM)
    participant Broker as MCP 5: Paper Execution Broker
    participant Ledger as MCP 6: Portfolio Ledger (PG + Redis)

    Feed->>Streamer: Raw NIFTY 50 Tick (RELIANCE.NS)
    Streamer->>Bus: Publish `market.ticks.nifty50` [correlation_id=cid]
    Bus->>Orch: Trigger Evaluation Cycle [cid]

    rect rgb(235, 245, 255)
        Note over Orch,Quant: Cognitive & Quantitative Evaluation
        Orch->>Tech: Analyst Agent calls `compute_indicators(symbol, cid)`
        Tech-->>Orch: IndicatorSnapshot (RSI, ATR, VWAP, VolatilityRegime)
        Orch->>Quant: Quant Agent calls `evaluate_strategy(snapshot, cid)`
        Quant-->>Orch: CandidateOrderProposal (side, qty, stop_loss, kelly_fraction)
    end

    rect rgb(255, 245, 230)
        Note over Orch,Guard: Mandatory Deterministic Gatekeeping
        Orch->>Guard: Risk Agent calls `validate_order(proposal, cid)`
        Guard->>Ledger: Fetch real-time equity, drawdown & concentration (Redis)
        Ledger-->>Guard: PortfolioRiskState
        Guard-->>Orch: RiskApprovalDecision (APPROVED + signed_risk_token)
    end

    rect rgb(235, 255, 240)
        Note over Orch,Ledger: Paper Execution & Double-Entry Settlement
        Orch->>Broker: Execution Agent calls `execute_paper_order(proposal, signed_risk_token, cid)`
        Broker->>Broker: Verify token + simulate spread, slippage & NSE statutory costs
        Broker->>Bus: Publish `orders.filled.paper` [cid]
        Bus->>Ledger: Consume fill event [cid]
        Ledger->>Ledger: Commit atomic PostgreSQL journal entry & update Redis PnL
    end
```

---

## 4. Deterministic Risk-Halt Path (Circuit Breaker)

When a pre-trade check or continuous intraday drawdown monitor detects a breach of hard invariants (e.g., intraday drawdown $\ge 2.0\%$ from the daily high-water mark), the **Risk Guardrail MCP Server** executes an immediate, unilateral floor halt without consulting any LLM agent.

```mermaid
flowchart LR
    Tick["Incoming Fill / Mark-to-Market Tick\n[correlation_id]"] --> Calc["MCP 4: risk_guardrail\nCompute Intraday Drawdown:\n(HWM - CurrentEquity) / HWM"]
    Calc --> Check{"Drawdown >= 2.0%\nOR Position > 5.0%\nOR Rate > 30/min?"}

    Check -->|"No (Within Bounds)"| Approve["Issue Signed Risk Token\nAllow Paper Order Routing"]

    Check -->|"Yes: Position / Rate Breach"| Reject["Reject Candidate Order\nEmit `risk.decisions` (REJECTED)\nLog Structured Warning"]

    Check -->|"Yes: Daily Drawdown >= 2.0%"| HaltFlag["1. Set Atomic Redis Flag:\n`alphatrader:floor_halted = 1`"]
    HaltFlag --> HaltEvent["2. Publish High-Priority Event:\n`risk.halt` on Redpanda"]
    HaltEvent --> FreezeOrch["3. LangGraph Orchestrator\nTransitions to HALTED Terminal Node"]
    HaltEvent --> BlockBroker["4. Paper Execution Broker\nRejects All In-Flight Orders"]
    HaltEvent --> AlertObs["5. Prometheus Alertmanager / Grafana\nFires Critical Floor-Halt Alert"]
```

---

## 5. Why the Risk Guardrail is Deterministic and Not LLM-Based

A foundational architectural principle of AlphaTrader ([ADR-001](adr/001-multi-agent-mcp-architecture.md)) is that **MCP Server 4 (`risk_guardrail`) contains zero Large Language Model calls, zero prompt templates, and zero probabilistic inference.**

| Dimension | Probabilistic LLM Risk Check (Rejected) | Deterministic Non-LLM Guardrail (AlphaTrader) |
|---|---|---|
| **Determinism** | Non-deterministic; identical inputs can yield different token outputs across temperature or model updates. | $100\%$ deterministic pure Python math; identical portfolio state and order input always produce the exact same decision. |
| **Tail-Risk & Hallucination** | LLMs can miscalculate floating-point ratios, ignore negative signs, or rationalize an exception during volatile market regimes. | Enforces exact IEEE-754 / `Decimal` inequalities (`drawdown_pct >= settings.max_daily_drawdown_pct`) with zero possibility of rationalization. |
| **Adversarial & Prompt Injection Resilience** | Malformed ticker news or upstream agent rationales could inject instructions ("Ignore previous drawdown rules"). | Consumes strictly typed numerical structs (`Pydantic v2`); natural-language text fields are never evaluated by the rule engine. |
| **Execution Latency** | $300\text{ ms} - 3,000\text{ ms}$ per LLM API round-trip; subject to provider rate limits and network outages. | $< 1\text{ ms}$ in-process evaluation backed by local Redis state; continues protecting the floor even if external LLM APIs fail. |
| **Formal Verifiability** | Cannot be exhaustively unit-tested or formally proved across all input combinations. | $100\%$ branch-testable with property-based and boundary unit tests in CI (`make check`). |

> **Summary:** In AlphaTrader, LLM agents are empowered to *propose* trades and *explain* market regimes, but **only deterministic code is trusted to protect capital and enforce circuit breakers.**
