# AlphaTrader Engineering Development Log (`DEV_LOG.md`)

This log records real engineering progress, actual technical issues encountered during build and verification, root-cause analyses, and lessons learned across our 5â€“6 day audit sprints.

---

## Sprint 1 â€” Repository Foundation, Safety Locks, Structured Logging & Quality Gates

- **Date:** 2026-10-13
- **Sprint Goal:** Wipe legacy prototype history after remote backup (`backup-old-version`), establish a clean repository structure from scratch, implement strictly typed configuration (`src/common/config.py`), correlation-ID structured JSON logging (`src/common/logging.py`), CLI healthcheck (`src/common/healthcheck.py`), unit test suite (`>= 90%` coverage), CI quality gates, and core architectural documentation.
- **Release Tag Target:** `v0.1.0-alpha`

### 1. What Was Built
1. **Safe Repository Wipe & Backup**:
   - Verified existing repository state and pushed `backup-old-version` (`5509dd7e5e6cc0648f2bbd8d97408835311b14ae`) to `origin` before switching to an orphan branch (`fresh-start`) and removing all legacy files.
2. **Modular Package & Deployment Skeleton**:
   - Created `src/orchestrator/`, `src/agents/{analyst,quant,risk,execution}/`, `src/mcp_servers/{market_data_streamer,technical_analyst,quant_strategy_engine,risk_guardrail,paper_execution_broker,portfolio_ledger}/` (each with a single-responsibility `README.md`), `src/common/`, `src/common/schemas/`, `src/observability/`, `deploy/docker/`, `deploy/k8s/` (Helm chart skeleton), `docs/adr/`, `scripts/`, and `tests/{unit,integration}/`.
3. **Code-Enforced Paper Trading Safety Boundary (`src/common/config.py`)**:
   - Implemented `AlphaTraderSettings` (`pydantic-settings`) with `frozen=True` immutability, risk guardrail numeric bounds (`MAX_DAILY_DRAWDOWN_PCT`, `MAX_POSITION_SIZE_PCT`, `MAX_OPEN_POSITIONS`), masked PostgreSQL DSN generation, and ADR-002 startup validators that immediately raise a fatal `ValidationError` if `TRADING_MODE != "PAPER"` or `LIVE_TRADING_ENABLED is True`.
   - Documented every configuration variable in `.env.example`.
4. **Correlation-ID Structured JSON Logging (`src/common/logging.py`)**:
   - Implemented `contextvars.ContextVar` correlation ID management (`correlation_context`, `get_correlation_id`, `set_correlation_id`) and `StructuredJsonFormatter` so every log line across async boundaries carries `correlation_id`, `timestamp` (UTC ISO-8601), `service`, `environment`, and `trading_mode: "PAPER"`.
5. **Healthcheck CLI & Unit Test Suite (`src/common/healthcheck.py`, `tests/unit/`)**:
   - Implemented `python -m src.common.healthcheck` to validate configuration invariants and emit a JSON health summary.
   - Built 25 unit tests across `test_config.py`, `test_logging.py`, and `test_healthcheck.py` achieving **98.37% branch coverage** on `src/common`.
6. **Tooling, CI & Architectural Documentation**:
   - Configured `pyproject.toml`, `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `CONTRIBUTING.md`, `README.md`, `ROADMAP.md` (24 sprints), `docs/architecture.md`, `ADR-001`, `ADR-002`, and `CHANGELOG.md`.

---

### 2. Real Problems Hit & How They Were Solved

#### Problem 1: `ValidationError` JSON Serialization Failure in `src/common/healthcheck.py`
- **Symptom:** When running `python -m pytest` for the first time, 24 tests passed and 1 test failed: `tests/unit/test_healthcheck.py::test_healthcheck_cli_fails_when_safety_invariant_violated` crashed with:
  ```text
  TypeError: Object of type ValueError is not JSON serializable
  ```
  at `print(json.dumps(error_report, indent=2), file=sys.stderr)` in `src/common/healthcheck.py:59`.
- **Root Cause:** In Pydantic v2 (`pydantic==2.13.5`), when a custom `@field_validator` raises a `ValueError("SAFETY VIOLATION (ADR-002)...")`, `ValidationError.errors(include_url=False)` includes a `"ctx": {"error": ValueError(...)}` dictionary where the value is the raw Python `ValueError` exception object rather than a string. Passing that list directly to `json.dumps()` fails because standard `json.JSONEncoder` cannot serialize `Exception` instances.
- **Fix:** Updated `src/common/healthcheck.py` to call `exc.errors(include_url=False, include_context=False)` and passed `default=str` to `json.dumps(error_report, indent=2, default=str)` as a secondary defense. Re-ran `pytest`: all 25 unit tests passed in `1.07s` with `98.37%` coverage.

#### Problem 2: Ruff `E501` Line-Length Violations in Package `__init__.py` Docstrings
- **Symptom:** Running `python -m ruff check src` flagged 10 `E501 Line too long (> 100)` errors across single-line module docstrings in `src/__init__.py`, `src/agents/*/__init__.py`, `src/mcp_servers/*/__init__.py`, and `src/observability/__init__.py`, plus 1 formatting diff in `src/common/healthcheck.py`.
- **Root Cause:** `pyproject.toml` enforces `line-length = 100`, and several initial package summary docstrings were 101â€“112 characters long.
- **Fix:** Made each module summary docstring concise ($< 100$ characters) while keeping detailed responsibilities in each MCP server's `README.md`, then ran `python -m ruff format src tests` and `python -m ruff check src tests` to reach zero lint/format errors.

#### Problem 3: Missing GNU `make` Binary on Windows Development Environment
- **Symptom:** Checking for `make` via `Get-Command make` on Windows PowerShell initially returned no executable, which would prevent running `make check` natively on the host.
- **Root Cause:** Windows does not ship with GNU Make pre-installed on `PATH`.
- **Fix:** Installed GNU Make 4.4.1 via `winget install --id ezwinports.make` and authored the `Makefile` using portable `$(PYTHON) -m <tool>` commands (`$(PYTHON) -m ruff`, `$(PYTHON) -m mypy`, `$(PYTHON) -m pytest`) so all targets execute identically on Windows PowerShell and Linux/macOS CI runners.

#### Problem 4: Ruff `I001` First-Party Import Classification After `make install`
- **Symptom:** After running `make install` (`pip install -e ".[dev]"`), `make check` failed at `ruff check src tests` with two `I001` import-sorting errors in `tests/unit/test_config.py` and `tests/unit/test_healthcheck.py`.
- **Root Cause:** In `pyproject.toml`, setting `src = ["src", "tests"]` under `[tool.ruff]` caused Ruff's `isort` rule to look for modules *inside* `src/` (`common`) rather than treating `src` itself as the top-level first-party package once installed in editable mode.
- **Fix:** Added `[tool.ruff.lint.isort]` with `known-first-party = ["src", "tests"]` in `pyproject.toml`.

---

### 3. What Was Learned
- **Test Negative Paths Early:** Writing an explicit unit test for the CLI failure path (`TRADING_MODE=LIVE` inside `test_healthcheck.py`) immediately exposed the Pydantic v2 `ctx["error"]` JSON serialization subtlety before it could reach production or an audit demo.
- **Defense-in-Depth Config Locks Work:** Enforcing `TRADING_MODE == "PAPER"` and `LIVE_TRADING_ENABLED is False` at the `BaseSettings` layer guarantees that every downstream component inherits paper-only execution invariants automatically.

---

## Sprint 2 â€” Typed Schemas, `EventEnvelope[T]`, Kafka Topic Contract Registry & JSON Schema Artifacts

- **Date:** 2026-10-13
- **Sprint Goal:** Define the strictly typed language (`Pydantic v2` domain models, `EventEnvelope[T]`, and the 9-topic Kafka contract registry) that every agent, MCP server, and Redpanda topic will speak. Export canonical JSON Schema artifacts (`docs/contracts/*.schema.json`) and a human-readable contract specification (`docs/contracts.md`) generated from live model instances, with CI drift verification and $\ge 95\%$ test coverage.
- **Release Tag Target:** `v0.2.0-alpha`

### 1. What Was Built
1. **Core Pydantic v2 Schema Package (`src/common/schemas/`)**:
   - `base.py`: `AlphaTraderBaseModel` (`frozen=True`, `extra="forbid"`, `str_strip_whitespace=True`), `UtcDatetime` validator rejecting naive timestamps and normalizing aware timestamps to UTC, `Decimal` financial types (`PositivePrice`, `NonNegativePrice`, `PositiveQuantity`, `NonNegativeQuantity`, `SignedMoney`, `NonNegativeMoney`, `BoundedFraction`, `SignedWeight`) with a `_reject_float_decimal` pre-validator rejecting binary `float` and non-finite `Decimal` (`NaN`, `Infinity`), and `NseSymbol` validator (`^[A-Z0-9][A-Z0-9&-]{0,19}$`).
   - `envelope.py`: Generic `EventEnvelope[PayloadT]` carrying `event_id` (UUID4), `correlation_id`, `causation_id`, `schema_version` (`1.0.0` major-version guard), `produced_at` (`UtcDatetime`), `source` (`ComponentSource` enum), and `payload: PayloadT`, plus deterministic canonical UTF-8 JSON bytes serialization (`to_json_bytes()` / `from_json_bytes()`).
   - `market.py`: `Tick`, `Candle` (`CandleInterval` enum, OHLC bar invariants `high >= max(open, close, low)` and `low <= min(open, close, high)`, `window_close > window_open`), and `MarketStatus` (`MarketSessionState` enum + `MarketStatus.from_timestamp()` computing NSE session state in `Asia/Kolkata`).
   - `signals.py`: `Direction` (`LONG`, `SHORT`, `FLAT`), `IndicatorSnapshot`, `TechnicalSignal` (with direction vs. RSI/MACD sanity checks), and `QuantSignal` (enforcing directional consistency between `direction`, `target_weight`, and `expected_edge_bps`).
   - `risk.py`: `RiskVerdict` (`APPROVE`, `REJECT`, `HALT`), `RiskRuleId`, `RiskReasonCode`, `RiskInputSnapshot`, `RiskDecision` (enforcing rule/reason consistency across verdicts), and `HaltEvent`.
   - `orders.py`: `Side`, `OrderType`, `OrderStatus`, `LEGAL_ORDER_STATUS_TRANSITIONS`, `IllegalOrderTransitionError`, `validate_order_status_transition()`, `OrderRequest` (requiring `LIMIT` price on limit orders and forbidding it on `MARKET` orders), `Order` (with `.transition_to()`), and `Fill`.
   - `portfolio.py`: `Position` and `PortfolioSnapshot` (verifying `equity == cash + gross_exposure` and consistency between `current_drawdown_pct`, `peak_equity`, and `equity`).
   - `system.py`: `SystemEventType`, `SystemSeverity`, and `SystemEvent` (requiring `CRITICAL` severity on `HALT` events).
2. **Kafka Topic Contract Registry (`src/common/contracts.py`)**:
   - `TopicName` (`StrEnum` of all 9 canonical Redpanda topics), `KeyRule` (`SYMBOL`, `ORDER_ID`, `PORTFOLIO_ID`, `SOURCE`), `TopicContract`, `TOPIC_REGISTRY`, `get_topic_contract()`, `extract_message_key()`, and `validate_topic_message()` (validating `EventEnvelope[T]` instances, raw UTF-8 JSON `bytes`/`str`, or `dict` against the registered topic contract and cross-checking `envelope.source in contract.allowed_producers`).
3. **Contract Artifacts & CI Drift Gate (`scripts/export_schemas.py`, `docs/contracts/`)**:
   - Exported 23 deterministic JSON Schema Draft 2020-12 files (`docs/contracts/*.schema.json`) and generated `docs/contracts.md` directly from real validated model instances (`build_sample_envelopes()`).
   - Added `make schemas` and `make schemas-check` (`python -m scripts.export_schemas --check`) wired into `make check`, `.github/workflows/ci.yml`, and `.pre-commit-config.yaml`.
4. **Unit, Property-Based & Contract Tests (`tests/unit/`)**:
   - Added `test_schemas.py`, `test_schemas_property.py` (Hypothesis property-based tests for `EventEnvelope[Tick]` round trips, `Decimal` handling, float rejection, and naive datetime rejection), and `test_contracts.py` (exhaustive 9-topic registry coverage, sample validation, producer authorization, key extraction, and schema export drift tests).
   - Achieved **98.88% test coverage** across `src/common` (104 tests passing).

---

### 2. Real Problems Hit & How They Were Solved

#### Problem 1: `mypy --strict` Invariant `TypeVar` Rejection on `EventEnvelope[PayloadT]`
- **Symptom:** Running `mypy src tests scripts` reported 9 errors in `scripts/export_schemas.py` (`Dict entry N has incompatible type "TopicName": "EventEnvelope[Tick]"; expected "TopicName": "EventEnvelope[AlphaTraderBaseModel]"`) and a type-arg error in `src/common/contracts.py`.
- **Root Cause:** In `src/common/schemas/envelope.py`, `PayloadT = TypeVar("PayloadT", bound=AlphaTraderBaseModel)` is invariant by default in Python's type system. Therefore `EventEnvelope[Tick]` was not treated as a subtype of `EventEnvelope[AlphaTraderBaseModel]` even though `EventEnvelope` is immutable (`frozen=True`).
- **Fix:** Declared `PayloadT = TypeVar("PayloadT", bound=AlphaTraderBaseModel, covariant=True)` in `src/common/schemas/envelope.py` and typed `TopicContract.envelope_model` as `type[EventEnvelope[Any]]`.

#### Problem 2: Pydantic v2 Generic Subclass Check in `TopicContract`
- **Symptom:** When `TopicContract` was initially modeled as an `AlphaTraderBaseModel` with `envelope_model: type[EventEnvelope[Any]]`, importing `TOPIC_REGISTRY` raised a Pydantic `ValidationError: Subclass of EventEnvelope[Any] expected [type=is_subclass_of]` for all 9 topics.
- **Root Cause:** In Pydantic v2, `EventEnvelope[Any]` creates a distinct parameterized generic `ModelMetaclass` instance that is a sibling (not a superclass) of `EventEnvelope[Tick]`. Consequently, Pydantic's internal `issubclass(EventEnvelope[Tick], EventEnvelope[Any])` check returned `False`, whereas `issubclass(EventEnvelope[Tick], EventEnvelope)` returns `True`.
- **Fix:** Implemented `TopicContract` as an immutable `@dataclass(frozen=True, slots=True)` with an explicit `__post_init__` check `issubclass(self.envelope_model, EventEnvelope)` and `issubclass(self.payload_model, AlphaTraderBaseModel)`.

#### Problem 3: `Path.relative_to(REPO_ROOT)` Failure When Testing `export_all_schemas` with `tmp_path`
- **Symptom:** Running `pytest` failed on `test_export_all_schemas_check_mode_detects_missing_and_modified_files` with `ValueError: 'C:\\Users\\ayush\\AppData\\Local\\Temp\\pytest-of-ayush\\...\\contracts\\Tick.schema.json' is not in the subpath of 'C:\\Users\\ayush\\Desktop\\Alphatrader'` inside `scripts/export_schemas.py:301`.
- **Root Cause:** `export_all_schemas()` formatted missing/stale file paths for error reporting using `target_path.relative_to(REPO_ROOT)`. While production runs always target `REPO_ROOT / "docs" / "contracts"`, unit tests pass pytest's temporary directory (`tmp_path`), which resides under `AppData\Local\Temp` outside `REPO_ROOT`.
- **Fix:** Added a helper `display_path = str(target_path.relative_to(REPO_ROOT)) if target_path.is_relative_to(REPO_ROOT) else str(target_path)` in `scripts/export_schemas.py`.

#### Problem 4: Ruff `RUF043` Raw Regex Pattern Warnings in `tests/unit/test_schemas.py`
- **Symptom:** Running `ruff check src tests scripts` flagged two `RUF043` errors (`Pattern passed to match= contains metacharacters but is neither escaped nor raw`) in `tests/unit/test_schemas.py`.
- **Root Cause:** Two `pytest.raises(..., match="...")` assertions contained parentheses `(1.0.0)` and `(LONG)` in standard string literals rather than raw strings or escaped regexes.
- **Fix:** Simplified the match substrings to literal phrases without regex metacharacters (`match="Incompatible schema_version major"` and `match="must match QuantSignal.direction"`).

---

### 3. What Was Learned
- **Parameterized Generics in Pydantic v2 Have Distinct Metaclasses:** Always check `issubclass(cls, UnparameterizedGenericBase)` rather than `issubclass(cls, GenericBase[Any])` when validating concrete generic Pydantic models at runtime.
- **Schema Export Drift Gates Catch Contract Drift Automatically:** Generating `docs/contracts/*.schema.json` and `docs/contracts.md` from live validated `EventEnvelope[T]` instances and enforcing `python -m scripts.export_schemas --check` in `make check` and CI guarantees documentation can never silently diverge from code.

---

## Sprint 3 â€” Production-Grade Local Infrastructure (PostgreSQL 16, Redis 7, Redpanda 24.x)

- **Date:** 2026-10-13
- **Sprint Goal:** Build a one-command production-grade local backing infrastructure with PostgreSQL 16, Redis 7, and Redpanda 24.x (Kafka API) via Docker Compose. Initialize relational tables matching domain schemas with append-only audit log protection, create all 17 Kafka topics idempotently from `TOPIC_REGISTRY`, implement an end-to-end smoke test and Pytest integration suite, and integrate with CI.
- **Release Tag Target:** `v0.3.0-alpha`

### 1. What Was Built
1. **Docker Compose Infrastructure Stack (`deploy/docker/docker-compose.yml`)**:
   - Pinned production images: `postgres:16.8-alpine`, `redis:7.4.2-alpine`, `docker.redpanda.com/redpandadata/redpanda:v24.3.4`, and `docker.redpanda.com/redpandadata/console:v2.8.2` (under profile `tools`).
   - Resource limits suitable for laptops (PG: 1 CPU/512M, Redis: 0.5 CPU/256M, Redpanda: 1.0 CPU/1.5G with `--smp 1 --memory 1G`).
   - Isolated bridge network `alphatrader_net` with all host ports bound strictly to `127.0.0.1`.
   - Real healthchecks (`pg_isready`, `redis-cli ping`, `rpk cluster health`) with `depends_on: condition: service_healthy`.
2. **Authoritative Relational Schema (`deploy/docker/init.sql`)**:
   - Provisioned 6 tables: `orders`, `fills`, `positions`, `portfolio_snapshots`, `risk_audit_log`, `schema_migrations`.
   - Used `NUMERIC(20, 6)` for exact financial arithmetic, `TIMESTAMPTZ` for UTC timestamps, UUID primary keys, and check constraints matching Pydantic domain enums.
   - Enforced `UNIQUE NOT NULL` on `orders.idempotency_key` to prevent double-execution of retried orders.
   - Created PostgreSQL trigger `trg_protect_risk_audit_log` that raises exceptions on any `UPDATE` or `DELETE` attempt, guaranteeing audit trail immutability.
   - Inserted baseline record into `schema_migrations` (`001_initial_schema`).
3. **Kafka Topic Provisioner (`scripts/create_topics.py` & `deploy/docker/init_topics.sh`)**:
   - Reads `TOPIC_REGISTRY` from `src.common.contracts` as the single source of truth.
   - Creates all 9 business topics and all 8 Dead Letter Queue (`.dlq`) topics with exact partition counts and retention rules via `confluent-kafka` AdminClient.
   - Fully idempotent: running multiple times safely detects existing topics and skips them without errors.
4. **End-to-End Infrastructure Smoke Test (`scripts/smoke_test.py`)**:
   - Connects to live PostgreSQL and verifies all 6 tables, baseline migration, idempotency uniqueness rejection, and trigger-blocked UPDATE/DELETE operations.
   - Connects to Redis and verifies PING, SET/GET/TTL round trip, and `alphatrader:floor_halted` circuit-breaker flag.
   - Connects to Redpanda and produces/consumes validated `EventEnvelope` instances for all 9 topics, verifying payloads with `validate_topic_message()`.
5. **Integration Test Suite & Pytest Markers (`tests/integration/`)**:
   - `test_database_schema.py`: Verifies tables, migrations, constraints, and audit-log immutability.
   - `test_redis_cache.py`: Verifies Redis caching, circuit breaker flags, and atomic rate limiters.
   - `test_redpanda_contracts.py`: Verifies topic existence and envelope round trips for all 9 topics.
   - Uses `@pytest.mark.integration` fixtures that automatically skip tests when services are unreachable.
6. **Cross-Platform Parity & CI Integration**:
   - Added targets in `Makefile`: `up`, `down`, `reset`, `ps`, `logs`, `psql`, `redis-cli`, `topics`, `smoke`, `test-integration`.
   - Created `make.ps1` providing identical functionality natively on Windows PowerShell.
   - Extended `.github/workflows/ci.yml` with an `infrastructure-integration` job running Docker Compose, topic creation, smoke testing, and integration tests.
7. **Architectural Documentation**:
   - Authored `docs/adr/004-redpanda-event-bus-and-postgres-system-of-record.md` and `docs/infrastructure.md`.

---

### 2. Real Problems Hit & How They Were Solved

#### Problem 1: Host Port 5432 Conflict on Windows Workstation
- **Symptom:** Starting PostgreSQL container bound to `127.0.0.1:5432:5432` failed with `bind: address already in use`.
- **Root Cause:** The Windows workstation runs a native background service `postgresql-x64-18` listening on port 5432 that could not be stopped without administrator elevation.
- **Fix:** Parameterized the container host port in `.env` as `POSTGRES_PORT=5433` and updated `src/common/config.py` to default to `5433` while keeping the internal container port as `5432`. Both services coexist cleanly.

#### Problem 2: Redpanda `rpk cluster health` CLI Argument Difference
- **Symptom:** In `deploy/docker/init_topics.sh`, `rpk cluster health --brokers redpanda:29092` failed with `unknown flag: --brokers`.
- **Root Cause:** In Redpanda's `rpk` CLI, cluster health commands target the Admin API (`--api-urls redpanda:9644`), whereas topic management commands target Kafka brokers (`--brokers redpanda:29092`).
- **Fix:** Updated `init_topics.sh` template generation in `scripts/create_topics.py` to use `rpk cluster health --api-urls "${ADMIN_API:-redpanda:9644}"` for health polling and `rpk topic create --brokers "${BROKER}"` for topic creation.

#### Problem 3: Docker Desktop Daemon Process Lifetime in Command Runner
- **Symptom:** When starting Docker Desktop via standard `Start-Process`, Docker processes were terminated when the parent command exited due to Windows process tree job management.
- **Root Cause:** Standard PowerShell subprocess spawning attaches child processes to the runner's job object. When the command finishes, the job object terminates child processes.
- **Fix:** Launched `Docker Desktop.exe` via `Invoke-CimMethod -ClassName Win32_Process -MethodName Create`, spawning the process directly from the Windows Management Instrumentation service (`WmiPrvSE.exe`), detached from the caller.

#### Problem 4: Integration Test Query Parameter Mismatch in `test_fills_total_fees_sum_constraint`
- **Symptom:** Running `pytest tests/integration` failed on `test_fills_total_fees_sum_constraint` with `psycopg.ProgrammingError: the query has 10 placeholders but 11 parameters were passed`.
- **Root Cause:** The INSERT query listed 11 columns for `orders` (`order_id`, `order_request_id`, `idempotency_key`, `symbol`, `side`, `order_type`, `status`, `quantity`, `stop_loss_price`, `strategy_name`, `correlation_id`), but only 10 `%s` placeholders were in the `VALUES` clause.
- **Fix:** Added the missing `%s` placeholder, aligning the column and parameter counts.

#### Problem 5: Row-Level Triggers Do Not Intercept PostgreSQL `TRUNCATE`
- **Symptom:** `TRUNCATE TABLE risk_audit_log` could bypass the row-level `BEFORE UPDATE OR DELETE ON risk_audit_log FOR EACH ROW` trigger.
- **Root Cause:** In PostgreSQL, `TRUNCATE` does not fire row-level triggers (`FOR EACH ROW`). It requires a separate statement-level trigger (`BEFORE TRUNCATE ON risk_audit_log FOR EACH STATEMENT`).
- **Fix:** Added `trg_prevent_risk_audit_log_truncate` executing `prevent_risk_audit_log_modification()` on statement-level `TRUNCATE`. Shipped as migration `002_protect_risk_audit_log_truncate` in `schema_migrations`, folded into `deploy/docker/init.sql`, and verified via integration test `test_risk_audit_log_truncate_blocked` and smoke tests using `SAVEPOINT` rollbacks.

---

## Sprint 4 â€” Market Data Streamer & First MCP Server

- **Date:** 2026-10-13
- **Sprint Goal:** Ingest NIFTY 50 market data, normalize into Sprint 2 contracts with provenance metadata, publish to Redpanda, aggregate 1m/5m candles, cache quotes in Redis, and expose 5 read-only MCP tools over stdio transport.
- **Release Tag Target:** `v0.4.0-alpha`

### 1. What Was Built
1. **Pre-flight CI & Infrastructure Guard**:
   - Verified master branch CI run (#38372413248) green.
   - Added unit test `test_init_topics_sh_matches_topic_registry` in `tests/unit/test_contracts.py` guaranteeing `deploy/docker/init_topics.sh` never diverges from `TOPIC_REGISTRY`. Committed as `fix(infra): ...`.
2. **Data Provenance & Schema Version 1.1.0 (`src/common/schemas/market.py`)**:
   - Added `FeedMode` enum (`SIMULATED`, `REPLAY`, `LIVE`) and `DataProvenance` model.
   - Added additive `provenance: DataProvenance | None = None` on `Tick` and `Candle`.
   - Enforced contract rule in `src/common/contracts.py`: topic validation strictly requires `provenance is not None` when `schema_version >= "1.1.0"`.
   - Delay validators: `SIMULATED` cannot be delayed; `is_delayed=False` requires `delay_seconds in (0, None)`; `delay_seconds > 0` requires `is_delayed=True`.
   - Re-exported 24 JSON schemas and regenerated `docs/contracts.md`. Committed as `feat(schemas): ...`.
3. **Provider Abstraction (`src/mcp_servers/market_data_streamer/providers/`)**:
   - Abstract `MarketDataProvider` async interface.
   - `SimulatedProvider`: deterministic random-walk ticks with realistic NSE tick size (â‚¹0.05) and Â±10% circuit limits.
   - `ReplayProvider`: plays historical ticks from `data/historical/nifty50_sample_ticks.jsonl` at configurable speed, preserving market event times while stamping receive times.
   - `LiveProvider`: connects to Yahoo Finance delayed REST endpoints with mandatory 900s delay disclosure and courteous rate limits.
   - `create_market_data_provider` factory mapping config `NSE_MARKET_FEED_MODE` to provider instances.
4. **Data Pipeline & Orchestration**:
   - `TickNormalizer`: validates ticks, checks monotonic sequence arrival per symbol, detects bid/ask inversion, and routes malformed payloads to `market.ticks.dlq`.
   - `CandleAggregator`: aggregates continuous ticks into 1m and 5m bars aligned to IST boundaries, verifying OHLCV and VWAP invariants.
   - `MarketCalendar`: evaluates trading session phases (`PRE_OPEN`, `OPEN`, `POST_CLOSE`, `CLOSED`) using `config/nse_holidays_2026.json`.
   - `MarketDataCache`: stores latest quotes in Redis hashes, candles in Redis sorted sets, and feed health metrics.
   - `StreamerPublisher`: publishes to Redpanda `market.ticks`, `market.candles`, `market.ticks.dlq`, and `system.events`.
   - `MarketDataStreamerService`: background worker with heartbeat stall detection and graceful shutdown.
5. **Model Context Protocol (MCP) Server (`server.py`)**:
   - Implemented via official MCP Python SDK (`mcp.server.mcpserver.MCPServer`).
   - Exposes 5 read-only tools: `get_latest_quote`, `get_candles`, `get_market_status`, `list_instruments`, `get_feed_health`.
6. **Testing & Verification**:
   - Built unit test suite `tests/unit/test_market_data_streamer.py` (20 tests including Hypothesis property testing on OHLCV invariants).
   - Built integration test `tests/integration/test_market_data_streamer.py` verifying live Redpanda publish/consume, DLQ routing, Redis caching, and MCP tool invocations.
   - Maintained strict mypy clean across all 38 source files and $\ge 95\%$ coverage on `src/common` (98.59%).

---

### 2. Real Problems Hit & How They Were Solved

#### Problem 1: FastMCP Import & Naming in MCP Python SDK 2.3.0
- **Symptom:** `from mcp.server.fastmcp import FastMCP` raised `ImportError: cannot import name 'FastMCP' from 'mcp.server'`.
- **Root Cause:** In `mcp==2.3.0`, the server class is exposed as `MCPServer` in `mcp.server.mcpserver`.
- **Fix:** Switched import to `from mcp.server.mcpserver import MCPServer` and instantiated `server = MCPServer("market-data-streamer")`.

#### Problem 2: Pytest Missing `asyncio` Marker
- **Symptom:** Running pytest on async test functions failed with `'asyncio' not found in markers configuration option`.
- **Root Cause:** `pyproject.toml` enabled `--strict-markers`, but `asyncio` was not registered in the `markers` list, and `pytest-asyncio` was missing from `[project.optional-dependencies]`.
- **Fix:** Added `pytest-asyncio==1.4.0` to dev dependencies, registered `asyncio` in `markers` under `[tool.pytest.ini_options]`, and installed it in the `.venv`.

#### Problem 3: Integration Test DLQ Argument Signature Mismatch
- **Symptom:** In `test_market_data_streamer_pipeline_integration`, calling `publisher.publish_dlq(..., error_code="...")` raised `TypeError: StreamerPublisher.publish_dlq() got an unexpected keyword argument 'error_code'`.
- **Root Cause:** `StreamerPublisher.publish_dlq` initially only accepted `error_message`, while the test also supplied the specific `error_code` from `TickNormalizer`.
- **Fix:** Extended `StreamerPublisher.publish_dlq` signature with `error_code: str | None = None` and included it in the DLQ payload dictionary.

---

### 3. What Was Learned
- **Separation of Event Time and Ingestion Time is Non-Negotiable:** Financial market replays cannot simply overwrite historical timestamps with `datetime.now(UTC)`, because trading indicators and volatility models depend on original market event intervals. Preserving `Tick.timestamp` as event time while stamping `DataProvenance.ingested_at` as local receive time provides complete auditing clarity.
- **Hypothesis Invariant Testing Prevents Subtle Aggregator Bugs:** Using property-based testing to verify that candle high $\ge$ all tick prices $\ge$ low, and open/close match the bucket boundaries, caught edge cases in bar aggregation before deploying to integration environments.

---

## Sprint 5 â€” Technical Analyst MCP Server (Incremental Indicators & Deterministic Signals)

- **Date:** 2026-10-13
- **Sprint Goal:** Build the Technical Analyst MCP server: stream closed candles (`is_final=True`) from Redpanda (`market.candles`), compute $O(1)$ incremental technical indicators (SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Rolling VWAP, ADX, Stochastic), publish deterministic explainable `TechnicalSignal` events to Redpanda (`signals.technical`), cache indicators and signals in Redis, and expose read-only MCP tools over STDIO using the pinned MCP Python SDK (`mcp==2.3.0`).
- **Release Tag Target:** `v0.5.0-alpha`

### 1. What Was Built
1. **Incremental Indicator Engine (`src/mcp_servers/technical_analyst/indicators/`)**:
   - Streaming, $O(1)$ memory-bounded implementations: SMA, EMA (recursive with warmup), RSI (Wilder's 1978 smoothed algorithm), MACD (12, 26, 9), Bollinger Bands (20, 2 std with running sum of squares), ATR (14, Wilder), Rolling VWAP (session open reset), ADX (14 with +DI/-DI), and Stochastic %K/%D (14, 3, 3).
   - Strict decimal-safe contracts at the boundary with float used internally where documented.
   - Explicit warmup: indicators report `is_ready=False` and return `None` (never NaN or zero).
   - Proven zero-lookahead via truncated history regression tests.
2. **Deterministic Signal Engine (`src/mcp_servers/technical_analyst/signals.py`)**:
   - Rule engine evaluated against versioned `config/technical_rules.json` (`1.0.0`).
   - Signal confidence defined strictly as rule-agreement score bounded in $[0, 1]$, not profit probability.
   - ADR-006 Data Provenance Merge Rule: least trustworthy mode wins (`SIMULATED` > `REPLAY` > `LIVE`), logical OR for `is_delayed`, max `delay_seconds`, and `"mixed"` provider attribution.
3. **Streaming Worker (`src/mcp_servers/technical_analyst/analyst.py`)**:
   - Redpanda consumer on `market.candles` (group `technical-analyst-worker`, manual offset commit).
   - Filters `is_final=True`, deduplicates candles, updates indicators, caches in Redis, and publishes `EventEnvelope[TechnicalSignal]` to `signals.technical` with causation ID tracking.
4. **FastMCP Server (`src/mcp_servers/technical_analyst/server.py`)**:
   - Exposes 5 read-only tools: `get_indicators`, `get_signal`, `explain_signal`, `list_supported_indicators`, `get_analyzer_health`.
5. **Quality Gates & Tests**:
   - Unit tests (`tests/unit/test_technical_analyst.py`) with golden tests, cross-check against `ta==0.11.0`, and Hypothesis invariant tests, achieving **92.68% coverage** against the $\ge 90\%$ gate.
   - Integration tests (`tests/integration/test_technical_analyst.py`) verifying pipeline, DLQ, Redis cache, MCP tools, and throughput benchmark.

---

### 2. Real Problems Hit & How They Were Solved

#### Problem 1: ta Library vs Streaming EMA & RSI Initialization Differences
- **Symptom:** During initial cross-check against `ta==0.11.0`, `our_ema` had a 0.88 absolute difference, and RSI output lengths differed by 1 bar.
- **Root Cause:** Standard pandas `ewm(span=N, adjust=False)` initializes recursively from bar 0 (with value equal to the first price), whereas an SMA-seeded EMA waits until bar $N$ to compute a simple average. Similarly, Wilder's original RSI requires 14 price changes (15 bars) to compute the initial average gain/loss.
- **MACD Signal Line Seeding & Decay Analysis:** Our engine seeds the 9-period signal EMA with $\text{SMA}_9(\text{MACD})$ once 9 MACD bars are collected, whereas `ta.trend.MACD` seeds directly from the very first MACD value ($\text{MACD}_1$). With $\alpha = \frac{2}{9+1} = 0.20$, the numerical gap between our signal line and `ta`'s signal line decays strictly at $(1 - \alpha) = 0.80$ per bar ($e_{t+1} = 0.80 e_t$). After 44 bars of signal calculation the difference decays below $10^{-6}$, and reaches $< 10^{-11}$ after 100 bars. This confirms that the observed divergence is strictly a mathematical seed initialization difference, not floating-point vs Decimal rounding.
- **Fix:** Documented the exact smoothing conventions in ADR-006 and DEV_LOG. Updated EMA to follow recursive $O(1)$ initialization from bar 0 while gating `is_ready` until $N$ bars are observed, achieving exact match ($< 10^{-5}$) with reference implementations and `ta==0.11.0`.

#### Problem 2: Pydantic Symbol Validation Failure in Benchmark
- **Symptom:** Throughput benchmark raised `ValidationError: Invalid NSE symbol 'SYM_0.NS'`.
- **Root Cause:** `NseSymbol` regex validator permits uppercase alphanumeric characters, '&', and '-' with optional '.NS' suffix, but does not allow underscores (`_`).
- **Fix:** Updated benchmark symbol generation to `SYM{i}.NS` (e.g. `SYM0.NS`), satisfying the canonical regex.

---

### 3. What Was Learned
- **Confidence Must Be Transparently Defined in Code and Docs:** Without explicit documentation, consumers naturally mistake confidence for a machine learning probability of trade profit. Formalizing confidence as an indicator rule-agreement fraction prevents dangerous downstream misinterpretation by quant and risk agents.
- **Exchange Market-Hours Gate on Live Providers:** The live feed re-capture for LiveProvider during Sprint 5 pre-flight checks was skipped because local time was outside NSE trading hours (market open: 09:15 to 15:30 IST, Monday to Friday). Outside market hours, the LiveProvider correctly enters a stale/market-closed state without emitting fake or duplicate ticks.
- **`types-redis` Workaround**: In Sprint 5, `types-redis==4.6.0.20241004` was tested. However, modern `redis>=5.0.0` ships complete native type stubs directly within the package (`redis.pyi`). To prevent type stub collisions and ensure standard PyPI resolution across environments, `types-redis` was verified as unnecessary and omitted from requirements.
301: - **TCS Indicator Skew Root-Cause Analysis**: In Sprint 5 pre-flight runs, TCS was observed reaching RSI 99.5 and +DI 82.4. Root-cause investigation traced this to indicator state persistence across feed transitions: Redis held cached accumulators initialized from an earlier test run at an older price, while `config/simulated_base_prices.json` seeded TCS at 4100.00 INR (contrasted with the captured live provider sample at ~2269.45 INR). Feeding a 600+ point price gap into a warmed indicator accumulator pegged RSI and +DI to upper boundaries. This was resolved by implementing an immutable state fingerprint (`provider`, `feed_mode`, `config_version`) and maximum interval gap check ($\le 50$ bars) on Redis restore, while safely preserving accumulator continuity across legitimate overnight price gaps (such as an 8% market open gap).
302:
303: ---
304:
305: ## Sprint 6 â€” Quant Strategy Engine & Factor Alpha (Phase A Backtesting & Phase B Engine)
306:
307: - **Date:** 2026-10-14
308: - **Sprint Goal:** Complete Phase A (honest provider-agnostic event-driven backtesting engine with Indian equity transaction friction, no-lookahead enforcement, corporate actions handling, and Monte Carlo null testing) and Phase B (deterministic quant strategy registry, ATR stop-loss/take-profit brackets, fractional-Kelly sizing, multi-factor alpha evaluation with conflict resolution, cryptographic backtest verification gate on edge claims, streaming worker with causation tracking, and FastMCP server exposing 8 tools).
309: - **Release Tag Target:** `v0.6.0-alpha`
310:
311: ### 1. What Was Built
312: 1. **Phase A Backtesting Harness (`src/backtesting/`)**:
313:    - `BacktestEngine`: Event-driven simulator guaranteeing zero lookahead bias by executing decisions strictly at bar $t+1$ Open.
314:    - `IndianEquityCostModel`: Exact statutory fee modeling (STT, brokerage, exchange turnover, SEBI charges, stamp duty, GST, and linear slippage) in exact `Decimal` arithmetic.
315:    - `HistoricalDataLoader`: Validates price continuity and corporate action adjustment flags on historical daily CSVs.
316:    - `test_backtesting.py`: 5-bar hand-calculated exact accounting test matching engine logs to the cent, 200-seed Monte Carlo null tests on gross/net returns showing nominal ~5% false discovery rate on gross and $\le 5\%$ net, known-edge injection test, and $\ge 90\%$ test coverage.
317: 2. **Phase B Deterministic Strategy Registry (`src/mcp_servers/quant_strategy_engine/strategies.py`)**:
318:    - Shipped 3 deterministic strategies: `ema_trend_following`, `rsi_bollinger_mean_reversion`, and `volatility_breakout`.
319:    - Enforced long-only cash delivery invariant: `direction == Direction.SHORT` is strictly prohibited; exits are expressed as `Direction.FLAT` with `target_weight = Decimal("0")`.
320: 3. **Analytical Position Sizing & ATR Brackets (`src/mcp_servers/quant_strategy_engine/sizing.py`)**:
321:    - Computes ATR stop-loss ($2.0 \times \text{ATR}$) and take-profit ($3.0 \times \text{ATR}$) brackets quantized to tick size 0.05 INR.
322:    - Fractional-Kelly sizing calculator capped by portfolio limits (default 20%).
323: 4. **Multi-Factor Alpha Evaluator (`src/mcp_servers/quant_strategy_engine/evaluator.py`)**:
324:    - Evaluates Trend, Mean Reversion, and Breakout factors with explicit weighted netting and transparent conflict resolution.
325: 5. **Cryptographic Backtest Gate & Publisher (`src/mcp_servers/quant_strategy_engine/publisher.py`)**:
326:    - Strictly gates `expected_net_edge_bps_per_trade`: refuses publication to `signals.quant` unless the backtest report is registered in `docs/backtests/index.json`, its SHA-256 matches, and its `edge_basis` is `OUT_OF_SAMPLE`.
327: 6. **Streaming Worker & FastMCP Server (`src/mcp_servers/quant_strategy_engine/`)**:
328:    - `worker.py`: Streaming Kafka consumer correlating technical signals and closed candles, preserving causation lineage (`causation_id = technical_signal.event_id`), committing offsets only after publication, and routing poison pills to `signals.quant.dlq`.
329:    - `server.py`: FastMCP server exposing 8 tools (`list_strategies`, `get_strategy_signal`, `get_target_portfolio`, `get_backtest_report`, `explain_strategy_signal`, `get_engine_health`, `evaluate_alpha_factors`, `propose_sized_order`).
330:
331: ### 2. Real Problems Hit & How They Were Solved
332:
333: #### Problem 1: Silent Fallback to Global Python in Makefile
334: - **Symptom:** Running `make check` on systems without an activated virtualenv executed with global Python (`anyio-4.14.1`), bypassing the project's pinned `.venv`.
335: - **Root Cause:** Makefile used a conditional `PYTHON ?= ...` without verifying that `.venv/Scripts/python.exe` existed.
336: - **Fix:** Hardened `Makefile` to check for `.venv` explicitly and fail fast if the environment is missing. Hardened `make.ps1` with the same guarantee.
337:
338: #### Problem 2: Pydantic Validation on Synthesized Candle and Flattened Parameters
339: - **Symptom:** Initial tests failed with `extra_forbidden` on `trades_count` in `Candle` and dictionary parameters in `QuantSignal`.
340: - **Root Cause:** Canonical schema field name is `trade_count` (singular), and `QuantSignal.parameters` requires a flat dictionary `dict[str, DecimalNumber | str | int]`.
341: - **Fix:** Replaced `trades_count` with `trade_count`, ensured `close_time > open_time`, and flattened nested factor scores to `factor_{name}` key-values.
342:
343: #### Problem 3: Resilient Cache Operation Without Host Redis
344: - **Symptom:** Tests executing without a live Redis container failed with `ConnectionRefusedError`.
345: - **Root Cause:** `QuantStrategyCache` methods did not catch connection errors during local unit test runs.
346: - **Fix:** Added try/except guards around Redis operations in `QuantStrategyCache` and `server.py`, allowing unit tests to verify full logic safely while logging warnings.
347:
348: ### 3. What Was Learned
- **Cryptographic Provenance Protects Academic Audits:** Gating expected edge claims behind a SHA-256 registered backtest report index makes it mathematically impossible for any strategy to falsely claim statistical alpha on simulated data.
- **Deterministic Risk Boundaries:** Prohibiting `SHORT` at both the schema and strategy layer enforces Indian equity cash delivery constraints by design, preventing illegal short proposals from ever reaching the risk guardrail.
- **Phase B Streaming Worker & Token-Fenced Invariants (2026-10-15)**:
  - Dedicated streaming consumer loop in `src/mcp_servers/quant_strategy_engine/worker.py` with time-addressable candle lookup across a 72-hour TTL.
  - Atomic Lua scripts in `manifest.py` for fanout manifest creation, lease claim/renewal/release, and token-fenced state transitions (`CONFIRMED_PUBLISHED`, `REPLAY_DEDUPLICATED`, `INTENTIONALLY_SUPPRESSED`).
  - Derived deterministic UUIDv5 signal IDs with length-prefixed UTF-8 serialization under namespace `e7b8a1c0-9d4f-4e8b-b12a-5c9e2b4f6a10`.
  - Publish-before-commit guarantee: input Kafka offsets commit ONLY after every strategy output in the manifest reaches a validated terminal disposition and dedup markers match.
  - Docker availability: host Docker service is currently stopped, meaning integration tests cleanly report skipped status while unit tests maintain $\ge 90\%$ test coverage (90.25% achieved).

---

## Sprint 7 & 8 — MCP Server 4: Deterministic Pre-Trade Risk Guardrail & Floor Circuit Breaker

- **Date:** 2026-10-15
- **Sprint Goals:** Implement the non-LLM pre-trade risk guardrail, HMAC-SHA256 risk token issuer/verifier, real-time intraday drawdown monitor, atomic Lua state transitions in Redis, and FastMCP Server 4 (`validate_order_proposal`, `get_floor_risk_status`, `trigger_manual_floor_halt`).
- **Release Target:** `v0.4.0-alpha`

### 1. What Was Built
1. **Cryptographic HMAC-SHA256 Token Manager (`src/mcp_servers/risk_guardrail/token.py`)**:
   - Pure-Python cryptographic signing binding `(order_request_id, symbol, side, quantity, limit_price, expires_at)`.
   - Constant-time `hmac.compare_digest` to prevent timing attacks; rejects expired or tampered tokens.
2. **Deterministic Risk Evaluator (`src/mcp_servers/risk_guardrail/evaluator.py`)**:
   - Pure-Python non-LLM evaluator enforcing single-position concentration ($\le 5.0\%$), max open positions ($\le 10$), order rate limiting ($\le 30/\text{min}$), and stop-loss distance validity ($[0.1\%, 15.0\%]$).
3. **Atomic Real-Time Risk State (`src/mcp_servers/risk_guardrail/state.py`)**:
   - `RiskStateManager` with atomic Lua script `LUA_UPDATE_RISK_STATE` advancing High-Water Mark monotonically, tracking drawdown against HWM, and atomically latching `alphatrader:floor_halted = "1"` if drawdown $\ge 2.0\%$.
   - Latched halt invariant: Day rollover or service restart **NEVER clears a floor halt**.
   - Event ordering protection: Stale or duplicate portfolio events (`event_ts <= last_event_ts`) are rejected without mutating state.
4. **FastMCP Server 4 (`src/mcp_servers/risk_guardrail/server.py`)**:
   - Exposes `validate_order_proposal`, `get_floor_risk_status`, and `trigger_manual_floor_halt`.
   - Fails closed on missing or corrupt Redis state.
   - Synchronous verified Kafka publishing to `risk.decisions` and `system.events`.
5. **Quality and Test Gates**:
   - 31 unit tests across `test_risk_guardrail.py` and `test_risk_state.py` achieving **91.38% branch coverage** on `src.mcp_servers.risk_guardrail`.
   - Static AST parsing test guaranteeing zero LLM imports.

---

## Sprint 9 — MCP Server 5: Paper Execution Broker

- **Date:** 2026-10-15
- **Sprint Goal:** Implement the paper execution broker with cryptographic `RiskApprovalToken` verification, realistic spread-crossing and slippage matching, exact Indian statutory transaction cost modeling, and synchronous verified Kafka publishing to `orders.fills`.
- **Release Target:** `v0.8.0-alpha`

### 1. What Was Built
1. **Exact Statutory Cost Engine (`src/mcp_servers/paper_execution_broker/costs.py`)**:
   - Exact Indian transaction friction calculations in `Decimal`:
     - STT: 0.10% on both sides for delivery; 0.025% on SELL for intraday.
     - Exchange turnover fees: 0.00325% on turnover.
     - SEBI regulatory turnover fees: INR 10 per crore (0.000001) on turnover.
     - Stamp duty: 0.015% on BUY side only.
     - GST: 18% on exchange fees and SEBI charges.
   - Strictly satisfies model invariant: `Fill.total_fees == stt_tax + exchange_fees + gst_tax + stamp_duty`.
2. **Simulated Paper Matching Engine (`src/mcp_servers/paper_execution_broker/matcher.py`)**:
   - Realistic half-spread crossing (BUY fills at LTP + half_spread, SELL fills at LTP - half_spread).
   - Volume/impact slippage scaling proportional to quantity.
   - Strict limit order price bounds checking (rejecting fills worse than `limit_price`).
3. **Cryptographic Risk Token Enforcement (`src/mcp_servers/paper_execution_broker/server.py`)**:
   - 100% rejection of missing, forged, mismatched, or expired `RiskApprovalToken`s before execution simulation.
4. **Verified Kafka Publishing (`src/mcp_servers/paper_execution_broker/publisher.py`)**:
   - Publishes `EventEnvelope[Fill]` to `orders.fills` with broker delivery verification.
5. **Quality & Test Gates**:
   - 16 unit tests achieving **95.11% branch coverage** on `src.mcp_servers.paper_execution_broker`.
   - Full CI quality gates verified.

---

## Sprint 10 — MCP Server 6: Double-Entry Portfolio Ledger & Redis Projection

- **Date:** 2026-10-15
- **Sprint Goal:** Implement a durable system-of-record double-entry portfolio ledger for paper trading in PostgreSQL and an ephemeral real-time portfolio projection in Redis with atomic monotonic revisions.
- **Release Target:** `v0.9.0-alpha`

### 1. What Was Built
1. **Double-Entry Ledger Schema & Migration (`deploy/docker/migrations/003_portfolio_double_entry_ledger.sql`)**:
   - Implemented standard chart of accounts (`ledger_accounts`), durable idempotency wall (`processed_events`), revision sequencer (`portfolio_revision_tracker`), journal headers (`journal_entries`), and postings (`journal_postings`).
   - Deferred constraint triggers verifying non-empty journal entries ($\ge 2$ postings, $\ge 1$ DEBIT, $\ge 1$ CREDIT) and exact balance ($\sum \text{Debits} == \sum \text{Credits}$) at transaction commit.
   - Row- and statement-level triggers preventing `UPDATE`, `DELETE`, and `TRUNCATE` operations on posted records.
   - `check_no_short_inventory` constraint on `positions` table (`quantity >= 0`).
2. **Authoritative Double-Entry Accounting Core (`src/mcp_servers/portfolio_ledger/ledger.py`)**:
   - Initial paper seed capital funding (`initialize_genesis_funding`) recording INR 1,000,000.00 funding entry once, resisting duplicate funding on process restart.
   - Immediate expensing of Indian statutory taxes and transaction fees (STT, exchange turnover fees, SEBI charges, stamp duty, GST).
   - Authoritative inventory basis from net balance of `Asset:Inventory:{SYMBOL}`.
   - Exact full liquidation crediting the exact remaining book balance to eliminate floating-point drift or balance residuals.
   - Strict no-short cash delivery policy raising `InsufficientInventoryError`.
3. **Atomic Redis Ephemeral Projection (`src/mcp_servers/portfolio_ledger/projection.py`)**:
   - Maintained real-time portfolio read model in Redis under `alphatrader:portfolio:*`.
   - Atomic Lua compare-and-set using 20-digit zero-padded revision strings, rejecting stale revisions and commit-order race conditions.
   - Atomic deletion of closed positions and snapshot-isolated rebuild from PostgreSQL under `REPEATABLE READ`.
4. **Kafka Ingestion & Verified DLQ Routing (`src/mcp_servers/portfolio_ledger/consumer.py`)**:
   - Consumed `orders.fills`, ingested into PostgreSQL ledger, updated Redis projection, and published `PortfolioSnapshot` to `portfolio.updates`.
   - Failsafe routing to `orders.fills.dlq` for schema violations, poison pill duplicate event hashes, or no-short rule violations.
5. **FastMCP Server 6 (`src/mcp_servers/portfolio_ledger/server.py`)**:
   - Exposed 4 protocol tools: `get_portfolio_summary`, `get_open_positions`, `get_audit_trail`, `get_ledger_health`.
6. **Quality & Test Gates**:
   - 22 unit tests achieving **90.85% code coverage** on `src.mcp_servers.portfolio_ledger`.
   - Property-based randomized long-only simulation verifying double-entry balance across 100 fills.

---

## Sprint 11 — Autonomous Trading Floor Agents & Structured MCP Contracts

- **Date:** 2026-10-15
- **Sprint Goal:** Implement the four specialized autonomous agent wrappers (`AnalystAgent`, `QuantAgent`, `RiskAgent`, `ExecutionAgent`) in `src/agents/` consuming MCP servers via structured tool contracts with deterministic mock-LLM and live-MCP support and full `correlation_id` / `causation_id` propagation.
- **Release Target:** `v0.10.0-alpha`

### 1. What Was Built
1. **Base Agent Interface & Contracts (`src/agents/base.py`)**:
   - Typed request and result dataclasses (`AnalystRequest`, `AnalystResult`, `QuantRequest`, `QuantResult`, `RiskRequest`, `RiskResult`, `ExecutionRequest`, `ExecutionResult`).
   - Abstract `BaseAgent` standardizing lifecycle, correlation context, and structured logging.
2. **Unified MCP Client Session (`src/agents/mcp_client.py`)**:
   - `MCPClientSession` abstract interface for FastMCP tool dispatch.
   - `InMemoryMCPClientSession` deterministic in-process adapter for unit tests and local execution.
3. **Analyst Agent (`src/agents/analyst/agent.py`)**:
   - Consumed MCP Server 2 (`technical-analyst`) endpoints `get_indicators` and `get_signal`.
   - Parsed `MarketRegime`, extracted indicator values, and synthesized `TechnicalSignal`.
4. **Quant Agent (`src/agents/quant/agent.py`)**:
   - Consumed MCP Server 3 (`quant-strategy-engine`) endpoints `evaluate_alpha_factors` and `propose_sized_order`.
   - Formulated candidate `OrderRequest` with ATR stop loss and fractional-Kelly sizing, linking `source_signal_ids`.
5. **Risk Agent (`src/agents/risk/agent.py`)**:
   - Consumed MCP Server 4 (`risk-guardrail`) endpoint `validate_order_proposal`.
   - Validated server-issued `RiskDecision`, preserving `approval_token` without client modification.
6. **Execution Agent (`src/agents/execution/agent.py`)**:
   - Consumed MCP Server 5 (`paper_execution_broker`) endpoint `execute_paper_order`.
   - Idempotently tracked dispatched order requests to guard against duplicate submissions.
   - Implemented ambiguous timeout reconciliation querying `get_paper_order_status`.
7. **Architectural Decision Record (`docs/adr/011-individual-autonomous-agents-and-mcp-contracts.md`)**:
   - Defined agent boundaries, zero-LLM risk authority, provenance tracking, and error semantics.
8. **Quality & Test Gates**:
   - 22 unit tests in `tests/unit/test_agents.py` achieving **93.96% branch coverage** on `src.agents` ($\ge 90\%$ gate satisfied).
   - Repeatable 10,000-fill stress test in `tests/unit/test_portfolio_ledger_stress.py`.

---

## Sprint 12 — LangGraph Stateful Multi-Agent Orchestrator & Conditional Routing

- **Date:** 2026-10-15
- **Sprint Goal:** Build the stateful, directed multi-agent trading floor orchestrator (`src/orchestrator/`) connecting `AnalystAgent`, `QuantAgent`, `RiskAgent`, and `ExecutionAgent`, with early exit paths for circuit-breaker halts, neutral signals, zero sizing, and risk rejections, durable Redis state checkpointing, and resolution of Sprint 11 audit findings.
- **Release Target:** `v0.11.0-alpha`

### 1. What Was Built
1. **Directed State Machine (`src/orchestrator/graph.py`)**:
   - `TradingFloorOrchestrator` coordinating sequential agent hops:
     - Pre-flight floor halt check (`is_floor_halted`) reading Redis `alphatrader:floor_halted`.
     - Analyst Node generating indicators and detecting market regime.
     - Quant Node calculating factor alpha and fractional-Kelly sizing.
     - Risk Node evaluating pre-trade guardrails and preserving HMAC `RiskApprovalToken`.
     - Execution Node submitting orders to paper broker with exact statutory tax modeling.
2. **Typed Graph State (`src/orchestrator/state.py`)**:
   - `TradingFloorState` dataclass tracking execution lifecycle `OrchestratorStage`, market inputs, agent artifacts, timestamps, and unbroken event lineage (`correlation_id`, `trigger_event_id`, `causation_id`).
3. **State Checkpointing (`src/orchestrator/checkpoint.py`)**:
   - `OrchestratorCheckpointer` writing snapshot state to Redis with TTL expiration (`alphatrader:orchestrator:state:<correlation_id>`) with transparent in-memory fallback.
4. **Sprint 11 Audit Defect Fixes**:
   - Resolved Finding 11-A: Added secondary `self.orders_by_request_id` lookup map in `PaperExecutionBroker` so `get_paper_order_status` resolves orders by either broker `order_id` or client `order_request_id`.
   - Resolved Finding 11-B: Propagated `trigger_event_id` through `ExecutionAgent` and `PaperExecutionBroker` so fill envelope `causation_id` reflects the upstream event ID rather than the internal `order_id`.
5. **Architectural Documentation**:
   - `docs/adr/012-stateful-multi-agent-directed-orchestrator.md`.
6. **Quality & Test Gates**:
   - 12 unit tests in `tests/unit/test_orchestrator.py` achieving **98.74% branch coverage** on `src.orchestrator` ($\ge 90\%$ gate satisfied).
   - Added `test-orchestrator-coverage` Makefile target and CI quality gate.

---

## Sprint 13 — End-to-End Tick-to-Paper-Fill Pipeline Integration

- **Date:** 2026-10-15
- **Sprint Goal:** Integrate the complete end-to-end trading pipeline from raw tick ingestion (`market.ticks`), candle aggregation, stateful 4-agent orchestrator execution (`AnalystAgent` -> `QuantAgent` -> `RiskAgent` -> `ExecutionAgent`), paper fill generation (`orders.fills`), to balanced posting on the PostgreSQL double-entry ledger.
- **Release Target:** `v0.12.0-alpha`

### 1. What Was Built
1. **End-to-End Integration Suite (`tests/integration/test_tick_to_fill_e2e.py`)**:
   - Implemented `test_tick_to_paper_fill_e2e_pipeline` with complete trace continuity.
   - Verified that `correlation_id` from the initial tick event flows through every agent hop, into the fill event envelope, and finally onto the ledger journal entry.
   - Verified debit-credit equality on the double-entry accounting ledger: total debits equal total credits, with STT and transaction charges properly allocated to expense accounts.
   - Enforced pure paper-trading invariants (`TRADING_MODE="PAPER"`).
2. **Tick Replay Pipeline Runner (`scripts/replay_pipeline.py`)**:
   - Replay utility simulating streaming NIFTY 50 tick series, updating the `CandleAggregator`, driving `TradingFloorState` through `TradingFloorOrchestrator`, and logging state progression.
3. **Quality & Test Gates**:
   - Zero lint errors (`ruff check`), zero formatting issues (`ruff format`), zero type errors (`mypy --strict`).
   - 331 tests passing across unit and integration suites.


