# AlphaTrader Engineering Development Log (`DEV_LOG.md`)

This log records real engineering progress, actual technical issues encountered during build and verification, root-cause analyses, and lessons learned across our 5–6 day audit sprints.

---

## Sprint 1 — Repository Foundation, Safety Locks, Structured Logging & Quality Gates

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
- **Root Cause:** `pyproject.toml` enforces `line-length = 100`, and several initial package summary docstrings were 101–112 characters long.
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

## Sprint 2 — Typed Schemas, `EventEnvelope[T]`, Kafka Topic Contract Registry & JSON Schema Artifacts

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

## Sprint 3 — Production-Grade Local Infrastructure (PostgreSQL 16, Redis 7, Redpanda 24.x)

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

---

### 3. What Was Learned
- **Database Triggers Provide Ironclad Immutability:** Enforcing append-only restrictions on `risk_audit_log` via a database trigger at the PostgreSQL engine level is vastly superior to application-level conventions; even if application code has a bug, the database strictly forbids tampering with audit records.
- **Contract Registry as Single Source of Truth Eliminates Drift:** Generating topic provisioning scripts directly from `src/common/contracts.py` prevents discrepancies between defined event schemas and actual Kafka cluster topic configurations.

