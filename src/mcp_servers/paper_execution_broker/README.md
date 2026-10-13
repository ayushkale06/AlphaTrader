# MCP Server 5: Paper Execution Broker

## Single Responsibility
Simulates realistic NSE order execution strictly in **PAPER TRADING** mode. Accepts only orders carrying a valid cryptographic/deterministic approval token from the Risk Guardrail MCP Server, models realistic bid-ask spread crossing, volume-weighted slippage, and Indian statutory transaction costs (STT, NSE exchange turnover charges, SEBI fees, GST, stamp duty), and emits immutable `orders.filled.paper` events.

## Safety Boundary
- **PAPER TRADING ONLY**: Contains zero dependencies, SDKs, or network routes for real-money brokerage order placement.

## Non-Responsibilities
- Never accepts unapproved orders lacking a Risk Guardrail signature.
- Never routes orders to an external live broker.

## Planned Sprint
Scheduled for implementation in **Sprint 9**.
