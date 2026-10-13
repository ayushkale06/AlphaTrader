# MCP Server 3: Quant Strategy Engine

## Single Responsibility
Evaluates quantitative factor models (mean-reversion, momentum, volatility breakout), computes expected value, Sharpe/Sortino projections, and fractional-Kelly position sizing recommendations for NIFTY 50 constituents, returning structured candidate trade proposals via MCP tools.

## Non-Responsibilities
- Does **not** bypass or override the Risk Guardrail MCP server.
- Does **not** execute trades or mutate ledger balances.

## Planned Sprint
Scheduled for implementation in **Sprint 6**.
