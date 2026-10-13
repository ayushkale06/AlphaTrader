# MCP Server 2: Technical Analyst

## Single Responsibility
Consumes normalized NIFTY 50 OHLCV streams and computes deterministic technical indicators (EMA, RSI, ATR, VWAP, Bollinger Bands, MACD) and volatility regime classifications, exposing them as pure, side-effect-free MCP tools to the Analyst Agent.

## Non-Responsibilities
- Does **not** size positions, generate portfolio allocations, or place orders.
- Does **not** mutate portfolio state.

## Planned Sprint
Scheduled for implementation in **Sprint 5**.
