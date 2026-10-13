# MCP Server 6: Portfolio Ledger

## Single Responsibility
Maintains the system-of-record double-entry paper portfolio ledger in PostgreSQL and low-latency intraday position, cash, realized/unrealized PnL, and high-water-mark state in Redis. Consumes verified paper fills to atomically update positions and exposes read-only portfolio state snapshots via MCP tools to the Risk Guardrail and orchestrator agents.

## Non-Responsibilities
- Does **not** make trading decisions or execute orders.
- Never allows un-audited direct mutation of balances outside recorded fill events.

## Planned Sprint
Scheduled for implementation in **Sprint 10**.
