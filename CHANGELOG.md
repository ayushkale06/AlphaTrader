# AlphaTrader Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0-alpha] - 2026-10-03
### Added
- Created `src/common/schemas.py` containing Pydantic v2 domain schemas (`MarketTick`, `TechnicalIndicators`, `OrderProposal`, `RiskEvaluation`, `ExecutedOrder`, `PortfolioState`).
- Added custom field validators on `MarketTick` (ask >= bid) and `OrderProposal` (stop loss direction).
- Created `tests/unit/test_schemas.py` with 8 unit tests using Python's `unittest` framework.
- Verified 100% test pass rate (`Ran 8 tests in 0.002s - OK`).
- Updated `logs/DEV_LOG.md` with Sprint 2 technical metrics.

## [0.1.0-alpha] - 2026-10-03
### Added
- Initialized official repository scaffolding for AlphaTrader.
- Added comprehensive `README.md` with system architecture, data flow diagram, and technology stack.
- Added `ROADMAP.md` establishing 6-month phased milestones across 24 sprints.
- Added `docs/architecture.md` detailing 6 Model Context Protocol (MCP) microservices.
- Added `docs/adr/001-mcp-microservices-architecture.md` recording architectural decision rationale.
- Added `logs/DEV_LOG.md` for bi-weekly professor evaluation audits.
- Added `.env.example` and `.gitignore` configuration files.
