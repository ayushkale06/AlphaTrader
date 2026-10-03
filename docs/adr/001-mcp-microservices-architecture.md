# ADR 001: Adoption of Model Context Protocol (MCP) Microservices Architecture

* **Status:** Approved  
* **Date:** 2026-10-03  
* **Author:** Technical Lead & Senior Software Architect  

## Context & Problem Statement
In building an autonomous financial trading engine, typical AI projects wrap all logic inside a monolithic Python script that directly calls an LLM API. This monolithic pattern creates severe production vulnerabilities:
1. Hard coupling between trading logic, data fetching, and order execution.
2. Impossibility of enforcing hard security boundaries or deterministic circuit breakers.
3. Lack of modularity and scaling limitations under high tick throughput.

## Decision Drivers
- **Standardization:** Adopt Anthropic's Model Context Protocol (MCP) to standardize tool definitions across multi-agent AI systems.
- **Fault Isolation:** If the Quant Strategy microservice experiences high latency, the Risk Guardrail service must continue enforcing account protection limits independently.
- **DevOps Readiness:** Decoupled MCP microservices can be packaged into separate Docker containers and deployed onto Kubernetes clusters with targeted auto-scaling.

## Considered Options
1. **Monolithic Python Script with LangChain tools.** (Rejected: Lacks isolation, fragile, poor engineering differentiation).
2. **REST API Microservices.** (Viable, but lacks standard tool discovery protocols for LLM agents).
3. **Decoupled MCP Microservices over FastAPI/AsyncIO.** (Chosen).

## Decision & Consequences
We choose **Option 3: Decoupled MCP Microservices**.
- **Pros:** Full isolation of concerns, standard schema discovery, clean integration with LangGraph, direct deployment compatibility with Kubernetes and Helm.
- **Cons:** Slightly higher initial infrastructure setup complexity (managed via Docker Compose in local development).
