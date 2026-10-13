# ADR-005: Market Data Provider Abstraction, Data Provenance, and MCP Exposure

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Ayush Kale (Repository Owner & Lead Engineer)
- **Sprint:** Sprint 4 (Market Data Streamer & First MCP Server)

---

## 1. Context

AlphaTrader operates as an autonomous multi-agent paper trading and risk orchestrator for the NSE NIFTY 50 universe. To simulate, analyze, and trade Indian equities accurately, the system requires continuous market data ingestion (quotes, order-book spreads, and historical candlestick bars).

As an academic project subject to strict code and architectural audits every 5–6 days by a university professor, three core architectural principles must be upheld:
1. **Vendor Independence**: Downstream agents, normalizers, and Kafka consumers must never be tightly coupled to a single exchange gateway or third-party data broker.
2. **Academic Integrity & Truth in Advertising**: No synthetic or delayed data may ever be presented as real-time. In particular, simulated walks and delayed public data feeds must be transparently labelled with rigorous data provenance attached to every message. No automated scraper may violate website Terms of Service or bypass robots.txt.
3. **Reproducibility**: Experiments, backtests, and oral audit demonstrations must run deterministically, reproducing exact tick-by-tick sequences on demand without requiring external network connectivity or paid subscriptions.

---

## 2. Market Data Source Evaluation for NIFTY 50 (Student Project Context)

We conducted a structured evaluation of three viable data ingestion options for Indian equities:

| Criterion | Option A: Public REST/Chart APIs (Yahoo Finance Delayed) | Option B: Registered Broker API (Upstox / Zerodha Kite Connect) | Option C: Exchange Tick Data Vendor (NSE Authorized Vendor) |
| :--- | :--- | :--- | :--- |
| **Direct Cost** | **Free ($0)** | ₹0 - ₹2,000 / month (Zerodha charges ₹2,000/mo for Kite Connect; Upstox offers a free developer tier with funded account) | ₹15,000 - ₹50,000+ / month (Enterprise exchange data license) |
| **Dissemination Delay** | **15 minutes delayed** (Disclosed publicly by exchange agreements) | Real-time (WebSocket / REST tick streaming) | Real-time tick-by-tick (Direct feed) |
| **Rate Limits** | ~2,000 requests / hour per IP (must rate-limit requests to 1 request/sec) | 3-10 requests / sec (REST); 1-3 active WebSocket connections | High throughput dedicated socket |
| **Terms of Service & Licensing** | Allowed for personal/educational non-commercial evaluation; prohibits commercial resale. | Requires developer account registration, TOTP login flow, and signed API agreement. | Strict commercial contract and compliance audit. |
| **Reliability** | Moderate (Endpoints occasionally experience schema quirks or Cloudflare throttles). | High (Production broker infrastructure). | Ultra-high enterprise SLA. |
| **API Key Requirement** | **None required** for basic v8 chart endpoints; optional token supported. | Mandatory API Key, API Secret, and daily OAuth2/TOTP login. | Mandatory IP whitelisting and credentials. |
| **Audit Suitability** | **Excellent for transparent educational paper trading**, provided 15m delay is explicitly labelled. | Good for live paper trading, but violates zero-credential out-of-the-box developer experience. | Prohibitive cost for student project. |

### Evaluation Outcome
For local testing and offline academic demonstration, neither external broker credentials nor paid vendor subscriptions should block a new developer or grading auditor from cloning the repository and running `make check` or `make stream`. Therefore:
1. `SIMULATED` is the default developer mode (deterministic, seeded random-walk).
2. `REPLAY` is the primary benchmark and reproducible demo mode (plays committed historical ticks).
3. `LIVE` connects to public delayed endpoints (Option A) with **mandatory delay disclosure (`is_delayed=True`, `delay_seconds=900`)**.

---

## 3. Decision

We establish an extensible provider abstraction, strict provenance metadata contracts, an asynchronous streaming pipeline, and an MCP server interface.

### 3.1 Typed Provider Abstraction (`src/mcp_servers/market_data_streamer/providers/`)
All market data sources implement the abstract interface `MarketDataProvider`:
- `connect() -> None`: Initializes network sockets or file descriptors.
- `subscribe(symbols: Sequence[str]) -> None`: Configures the active ticker subscription list.
- `stream_quotes() -> AsyncIterator[dict[str, Any] | Tick]`: Asynchronously yields raw quote dictionaries or validated `Tick` objects.
- `close() -> None`: Gracefully tears down connections.
- Properties: `name`, `feed_mode` (`SIMULATED | REPLAY | LIVE`), `is_delayed: bool`, `delay_seconds: int | None`.

Three concrete providers are implemented:
1. **`SimulatedProvider`**: Deterministic random-walk ticks adhering to NSE rules (tick size ₹0.05, ±10% operating circuit bands, realistic bid/ask spreads). Always labelled `feed_mode=FeedMode.SIMULATED`, `is_delayed=False`, `delay_seconds=0`.
2. **`ReplayProvider`**: Replays historical sample ticks committed in `data/historical/nifty50_sample_ticks.jsonl` at configurable speed (`speed_multiplier`). Preserves original historical market timestamps while annotating `ingested_at` receive time.
3. **`LiveProvider`**: Connects to public delayed REST endpoints with `feed_mode=FeedMode.LIVE`, `is_delayed=True`, and `delay_seconds=900`. Rate limits constituent polls with polite intervals.

The active provider is chosen via configuration (`NSE_MARKET_FEED_MODE`), defaulting to `SIMULATED`.

### 3.2 Data Provenance Schema & Timestamp Semantics
To prevent any possibility of presenting synthetic or delayed data as live quotes, every tick and candle carries a typed `DataProvenance` model:
- `provider: str` (e.g. `simulated_feed`, `replay_feed`, `yahoo_finance_delayed`)
- `feed_mode: FeedMode` (`SIMULATED`, `REPLAY`, `LIVE`)
- `is_delayed: bool`
- `delay_seconds: int | None` (Enforced validators: `is_delayed=False` requires `delay_seconds in (0, None)`; `delay_seconds > 0` requires `is_delayed=True`; `SIMULATED` can never be delayed).
- `ingested_at: UtcDatetime | None`

#### Timestamp Clarification
- **`Tick.timestamp` / `Candle.open_time`**: The authoritative **market event time** (when the trade occurred on the exchange floor). For `REPLAY`, this preserves historical event times.
- **`DataProvenance.ingested_at`**: The **local receive time** (when AlphaTrader's ingestion pipeline processed the packet).

### 3.3 Schema Evolution & Upgrade Policy
1. `DataProvenance` was introduced in Schema Version `1.1.0`.
2. On `Tick` and `Candle` models, `provenance: DataProvenance | None = None` is optional with a default to allow deserializing legacy `1.0.0` messages.
3. However, topic contract validation (`validate_topic_message`) strictly enforces `envelope.payload.provenance is not None` whenever `envelope.schema_version >= "1.1.0"`. Producers are prevented from publishing unannotated data.
4. Models use `extra="forbid"`. As producers and consumers are deployed together in this monolithic repository, schema versions are bumped synchronously.

### 3.4 Aggregation & Caching Pipeline
- **`TickNormalizer`**: Enforces positive prices, validates bid <= ask, verifies per-symbol sequence monotonicity, and routes invalid ticks to `market.ticks.dlq`.
- **`CandleAggregator`**: Aggregates continuous ticks into 1-minute and 5-minute OHLCV candlestick bars aligned to IST boundaries. Preserves mathematical invariants: $\text{high} \ge \max(\text{open}, \text{close}, \text{low})$, $\text{low} \le \min(\text{open}, \text{close}, \text{high})$, $\text{vwap} \in [\text{low}, \text{high}]$.
- **`MarketCalendar`**: Evaluates NSE operating sessions (`PRE_OPEN` 09:00–09:15, `OPEN` 09:15–15:30, `POST_CLOSE` 15:30–16:00, `CLOSED`) using `config/nse_holidays_2026.json`.
- **`MarketDataCache`**: Low-latency Redis cache storing quotes (`alphatrader:quote:{symbol}`), candles (`alphatrader:candle:{symbol}:{interval}` as Redis sorted sets scored by timestamp), and feed health (`alphatrader:feed_health:market_data_streamer`).

### 3.5 Model Context Protocol (MCP) Server Exposure
The streamer exposes 5 read-only tools to LLM agents using the official MCP Python SDK (`mcp.server.mcpserver.MCPServer`) over standard I/O (`stdio`):
1. `get_latest_quote(symbol: str) -> dict`: Returns the latest quote tick and its provenance.
2. `get_candles(symbol: str, interval: str, limit: int) -> list[dict]`: Returns recent OHLCV bars.
3. `get_market_status() -> dict`: Returns exchange session phase, `is_open` flag, and holiday notes in IST.
4. `list_instruments() -> dict`: Returns the versioned 50 NIFTY constituent symbols and rebalance warning.
5. `get_feed_health() -> dict`: Returns ingestion status, tick count, candle count, and DLQ rejections.

---

## 4. Consequences

### Positive
- **Auditable Provenance**: Any trading decision made downstream carries a clear trail indicating whether it was based on live, delayed, replayed, or simulated data.
- **Zero Cost & Zero Key Barrier**: Any student or professor can clone the repo and run the full streaming and MCP stack without purchasing API keys or broker accounts.
- **Agent Interoperability**: AI agents query market data via standardized MCP tools rather than custom REST wrappers or raw Redis calls.

### Negative / Trade-offs
- The free `LIVE` provider is delayed by 15 minutes, meaning live paper trading cannot test ultra-low latency execution strategies. (This is acceptable as AlphaTrader is a multi-agent swing/intraday risk orchestrator, not an HFT system).
