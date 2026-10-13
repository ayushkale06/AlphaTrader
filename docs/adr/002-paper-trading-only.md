# ADR-002: Strict Paper-Trading-Only Safety Boundary and Code-Level Enforcement

- **Status:** Accepted (Non-Negotiable Invariant)
- **Date:** 2026-10-13
- **Deciders:** Ayush Kale (Repository Owner & Lead Engineer)
- **Sprint:** Sprint 1 (Foundation)

---

## 1. Context

AlphaTrader is an academic and quantitative engineering platform designed to research autonomous multi-agent decision workflows, deterministic risk guardrails, and cloud-native observability on **NIFTY 50 (NSE)** market data.

Autonomous agentic systems that interact with financial markets carry severe regulatory, financial, and operational risk if accidentally misconfigured to route orders to a live brokerage account. Relying solely on a documentation disclaimer ("please only use paper trading") is insufficient engineering practice: environment variable typos, misconfigured deployment manifests, or rogue dependencies could still expose a system to unintended live order routing.

---

## 2. Decision

AlphaTrader is architected with a **zero-trust, defense-in-depth Paper-Trading-Only boundary**. Nothing in this repository may ever call a real-money or live brokerage order-placement endpoint.

This invariant is enforced across **four independent layers**:

### Layer 1: Startup Configuration Hard-Lock (`src/common/config.py`)
`AlphaTraderSettings` enforces two strict Pydantic v2 validators at process startup before any agent, MCP server, or network client initializes:
1. **`enforce_paper_trading_mode`**: Validates `TRADING_MODE`. Any value other than `"PAPER"` (such as `"LIVE"`, `"REAL"`, `"PRODUCTION"`, or non-string values) immediately raises a fatal `ValidationError` containing `"SAFETY VIOLATION (ADR-002)"`.
2. **`enforce_live_trading_disabled`**: Validates `LIVE_TRADING_ENABLED`. If set to `True` via environment variable or keyword argument, startup immediately aborts with `"SAFETY VIOLATION (ADR-002): LIVE_TRADING_ENABLED=True is prohibited."`
3. **`frozen=True`**: `AlphaTraderSettings` instances are immutable after instantiation, preventing runtime mutation of `trading_mode` or `live_trading_enabled`.

### Layer 2: Healthcheck & CI Gate Verification (`src/common/healthcheck.py`, `tests/unit/test_config.py`)
- The CLI entry point `python -m src.common.healthcheck` verifies both `settings.trading_mode == "PAPER"` and `settings.live_trading_enabled is False`. If violated, the CLI exits with non-zero code `1` and emits an `UNHEALTHY` diagnostic payload to `stderr`.
- Unit tests in `tests/unit/test_config.py` and `tests/unit/test_healthcheck.py` explicitly fuzz `TRADING_MODE` (`LIVE`, `REAL`, `PRODUCTION`, `BROKER`, `""`) and `LIVE_TRADING_ENABLED=true` on every commit and CI run to guarantee the safety lock can never regress.

### Layer 3: Dependency & Network Isolation (`pyproject.toml`, `src/mcp_servers/paper_execution_broker`)
- `pyproject.toml` prohibits third-party live brokerage order-execution SDKs (e.g., Zerodha KiteConnect order placement clients, AngelOne SmartAPI live order routers, Upstox live order clients).
- Market data ingestion (`NSE_MARKET_FEED_MODE`) is restricted to `Literal["SIMULATED", "DELAYED_PUBLIC", "LIVE_READONLY"]`—strictly read-only market quote/tick streams with zero order-submission capability.
- The `paper_execution_broker` MCP server matches orders exclusively against an internal simulated limit-order book / slippage model and publishes only to the internal `orders.filled.paper` Redpanda topic.

### Layer 4: Structured Audit Tagging (`src/common/logging.py`, `deploy/k8s/`)
- Every structured JSON log line emitted by `StructuredJsonFormatter` stamps `"trading_mode": "PAPER"`.
- Kubernetes Helm templates (`deploy/k8s/templates/_helpers.tpl`) label all workloads with `alphatrader.io/trading-mode: "PAPER"`.

---

## 3. Consequences

### Positive
- **Fail-Closed Startup**: Misconfiguring `TRADING_MODE=LIVE` or `LIVE_TRADING_ENABLED=true` in `.env`, Docker Compose, or Kubernetes ConfigMaps terminates the process in `< 10 ms` before any network socket is opened.
- **Verifiable Compliance**: Auditors and reviewers can inspect `src/common/config.py` and run `pytest tests/unit/test_config.py` to verify that paper-only enforcement is tested and active.

### Negative / Trade-offs
- **Cannot Be Repurposed for Live Trading via Config Toggle**: Converting this codebase to trade real capital would require deliberate code modifications, removal of ADR-002 validators, and replacement of the `paper_execution_broker` MCP server—which is the exact friction intended by this decision.

---

## 4. Alternatives Rejected

| Alternative | Reason for Rejection |
|---|---|
| **Runtime Feature Flag (`if settings.live_trading_enabled: call_live_broker()`)** | Dangerous: a single environment variable misconfiguration in Docker/K8s could activate real-money order routing. |
| **Broker Sandbox API Keys for Order Execution** | External broker sandboxes suffer from frequent downtime, rate limits, and share the same SDK client class as live accounts, creating credential-swap risk. Internal deterministic paper simulation is faster, reproducible, and 100% air-gapped from live order routing. |
