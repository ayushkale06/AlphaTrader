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

---

### 3. What Was Learned
- **Test Negative Paths Early:** Writing an explicit unit test for the CLI failure path (`TRADING_MODE=LIVE` inside `test_healthcheck.py`) immediately exposed the Pydantic v2 `ctx["error"]` JSON serialization subtlety before it could reach production or an audit demo.
- **Defense-in-Depth Config Locks Work:** Enforcing `TRADING_MODE == "PAPER"` and `LIVE_TRADING_ENABLED is False` at the `BaseSettings` layer guarantees that every downstream component inherits paper-only execution invariants automatically.
