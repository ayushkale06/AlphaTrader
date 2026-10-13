# MCP Server 4: Deterministic Risk Guardrail (NON-LLM)

## Single Responsibility
Enforces hard-coded, deterministic, **non-LLM** pre-trade and intraday risk invariants across the entire trading floor. Validates every candidate order against maximum daily drawdown (`MAX_DAILY_DRAWDOWN_PCT`), single-position concentration (`MAX_POSITION_SIZE_PCT`), sector exposure, order rate limits, and NSE market-hours constraints. Possesses unilateral authority to reject orders or trigger an immediate trading-floor circuit-breaker halt (`risk.halt` event + Redis kill-switch flag).

## Why Non-LLM
Large Language Models are probabilistic and susceptible to prompt injection, hallucination, and non-deterministic reasoning under distribution shift. Capital protection invariants must execute in deterministic sub-millisecond code with zero LLM dependencies.

## Non-Responsibilities
- Never uses an LLM or probabilistic model to approve or override risk limits.
- Does **not** generate alpha signals or execute fills.

## Planned Sprint
Scheduled for implementation in **Sprint 7–8**.
