# MCP Server 1: Market Data Streamer

## Single Responsibility
Ingests live and replayed NIFTY 50 (NSE) market ticks and OHLCV bars, normalizes timestamps to `Asia/Kolkata` (IST), validates tick schema integrity, publishes canonical market events to the `market.ticks.nifty50` Redpanda topic, and exposes read-only MCP resources/tools for latest quote and order-book snapshots.

## Non-Responsibilities
- Does **not** compute technical indicators or alpha signals.
- Does **not** evaluate risk limits or submit orders.
- Never connects to any live order-execution endpoint.

## Planned Sprint
Scheduled for implementation in **Sprint 4**.
