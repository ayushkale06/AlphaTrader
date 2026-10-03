# AlphaTrader: Autonomous Multi-Agent Algorithmic Trading & Risk Orchestrator

[![Architecture: MCP Microservices](https://img.shields.io/badge/Architecture-MCP%20Microservices-0E1D3F?style=for-the-badge)](https://modelcontextprotocol.io)
[![State Machine: LangGraph](https://img.shields.io/badge/Orchestrator-LangGraph-F0DC9C?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)
[![Infrastructure: Kubernetes](https://img.shields.io/badge/Infrastructure-Kubernetes-326CE5?style=for-the-badge)](https://kubernetes.io/)
[![Telemetry: Prometheus & Grafana](https://img.shields.io/badge/Observability-Grafana-F46800?style=for-the-badge)](https://grafana.com/)

**AlphaTrader** is a production-grade, multi-agent financial engineering platform that executes paper trading strategies on live stock market feeds (NIFTY 50). 

Unlike standard AI wrapper scripts, AlphaTrader uses a distributed **Model Context Protocol (MCP)** architecture where four autonomous AI agents work alongside a non-LLM **Deterministic Risk Guardrail** microservice to evaluate market technicals, calculate optimal risk-reward ratios, enforce strict circuit breakers, and log full execution audits to Prometheus and Grafana.

> **Disclaimer:** AlphaTrader is designed strictly for simulated paper trading, algorithm verification, and quantitative research. It does not execute live monetary trades on real brokerage accounts.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Market Data Layer
        A[Live Market WebSocket Feed / NSE API] --> B[Redpanda / Kafka Event Stream]
    end

    subgraph Microservice Layer - 6 Model Context Protocol (MCP) Servers
        B --> C[MCP Server 1: Market Data Streamer]
        C --> D[MCP Server 2: Technical Analyst]
        C --> E[MCP Server 3: Quant Strategy Engine]
        D & E --> F[MCP Server 4: Deterministic Risk Guardrail]
        F -- Approved Orders Only --> G[MCP Server 5: Execution Broker API]
        G --> H[MCP Server 6: Portfolio Ledger DB]
    end

    subgraph Autonomous Agentic Orchestrator (LangGraph)
        I[Analyst Agent] --> J[Quant Strategy Agent]
        J --> K[Risk Audit Agent]
        K --> L[Execution Agent]
    end

    subgraph Production Observability & Governance
        C & D & E & F & G & H --> M[Prometheus Telemetry Exporter]
        M --> N[Grafana Real-Time Audit Dashboard]
        F -- Floor Halt Trigger --> O[Admin Alert / Circuit Breaker]
    end
```

---

## 🌟 Key Technical Differentiators

1. **Protocol Standardization (MCP):** Every domain component (Market Data, Technicals, Risk, Execution, Ledger) is exposed as an isolated microservice adhering strictly to Anthropic's **Model Context Protocol (MCP)** standard.
2. **Deterministic Risk Circuit Breaker:** A non-LLM, hard-coded C++/Python risk engine overrides agent recommendations if daily drawdown exceeds 2%, volatility exceeds acceptable bands, or slippage risk is flagged.
3. **Multi-Agent State Machine (LangGraph):** Coordinated asynchronous agent graphs (Analyst, Quant, Risk, Executor) with explicit fallback paths and state persistence.
4. **Cloud-Native & Production Ready:** Packaged into Docker containers, orchestrated via Kubernetes Helm charts, and monitored live using Prometheus metrics and Grafana dashboards.

---

## 🛠️ Technology Stack

| Domain | Technologies |
|---|---|
| **Orchestration & State** | LangGraph, AutoGen, Python 3.11+ |
| **Protocol Standards** | Model Context Protocol (FastMCP / AsyncMCP) |
| **Streaming & Messaging** | Redpanda / Apache Kafka, WebSockets |
| **Databases** | PostgreSQL (Ledger & Audit), Redis (State Cache & Rate Limits) |
| **Backend & APIs** | FastAPI, Pydantic v2, gRPC |
| **DevOps & Containers** | Docker, Kubernetes (Minikube / Helm), GitHub Actions |
| **Observability** | Prometheus, Grafana, OpenTelemetry |
| **Frontend Dashboard** | Next.js 14 / React, TailwindCSS, Chart.js / TradingView Lightweight Charts |

---

## 🚀 Repository Navigation & Documentation

- [System Architecture Specification](file:///C:/Users/ayush/.gemini/antigravity/scratch/AlphaTrader/docs/architecture.md)
- [Architectural Decision Records (ADRs)](file:///C:/Users/ayush/.gemini/antigravity/scratch/AlphaTrader/docs/adr/)
- [6-Month Development Roadmap](file:///C:/Users/ayush/.gemini/antigravity/scratch/AlphaTrader/ROADMAP.md)
- [Bi-Weekly Development Logs (5-6 Day Evaluation Cadence)](file:///C:/Users/ayush/.gemini/antigravity/scratch/AlphaTrader/logs/DEV_LOG.md)

---

## ⚙️ Local Development Setup

```bash
# 1. Clone repository
git clone https://github.com/your-username/AlphaTrader.git
cd AlphaTrader

# 2. Configure environment variables
cp .env.example .env

# 3. Spin up local microservices via Docker Compose
docker-compose up -d

# 4. Verify system health endpoints
curl http://localhost:8000/health
```

---

## 📜 Development Cadence & Quality Assurance

This project is actively developed over a 6-month period with continuous evaluations every 5–6 days. All code changes adhere to strict **Conventional Commits** (`feat:`, `fix:`, `docs:`, `test:`, `ci:`) and pass automated unit/integration tests before merge.
