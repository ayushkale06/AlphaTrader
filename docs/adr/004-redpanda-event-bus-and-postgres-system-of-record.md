# ADR-004: Redpanda Event Bus, PostgreSQL System of Record, and Redis State Store

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Ayush Kale (Repository Owner & Lead Engineer)
- **Sprint:** Sprint 3 (Production-Grade Local Infrastructure)

---

## 1. Context

AlphaTrader requires a reproducible, production-grade local infrastructure stack to support an autonomous multi-agent NIFTY 50 paper trading architecture. The platform consists of four LangGraph agents, six Model Context Protocol (MCP) servers, and continuous event streams.

Key architectural requirements:
1. **Event Bus Throughput & Predictable Latency**: Ticks and market events arrive continuously during market hours. The messaging layer must support Kafka API semantics (partitioning by symbol for strict per-symbol ordering, consumer groups, retention policies, and Dead Letter Queues).
2. **Authoritative Financial Ledger & Audit Immutability**: Order submissions, fills, portfolio positions, and risk decisions must be durably stored in an ACID-compliant relational store with exact decimal arithmetic (`NUMERIC(20, 6)`), referential integrity, and mathematical invariants. Risk decisions must be provably immutable to satisfy rigorous academic audits.
3. **Sub-Millisecond Circuit-Breaker State**: Evaluating whether the entire trading floor or a specific symbol is halted must execute in sub-millisecond latency before every order submission.
4. **Developer & CI Reproducibility**: The entire stack must initialize with one command on developer workstations (Linux, macOS, and Windows WSL 2) and within standard GitHub Actions runners (`ubuntu-latest`) without heavy JVM memory footprints or external cloud dependencies.

---

## 2. Decision

We establish a local containerized infrastructure stack managed via Docker Compose (`deploy/docker/docker-compose.yml`), `.env` configuration, and the contract registry (`src/common/contracts.py`).

### 1. Redpanda 24.x (over Apache Kafka JVM)
- **Architecture**: Single-node Redpanda (`docker.redpanda.com/redpandadata/redpanda:v24.3.4`) operating in developer mode.
- **Why Redpanda over JVM Kafka**:
  - **Zero JVM Overhead**: Implemented in native C++ using the Seastar async framework. Requires no Java Runtime Environment, no garbage collection tuning, and no ZooKeeper/KRaft quorum for local single-node development.
  - **Memory & Startup Footprint**: Boots in under 1 second and operates comfortably within a 1.0 GB container limit (suitable for developer laptops and CI).
  - **100% Kafka API Compatibility**: Supports standard Kafka clients (`confluent-kafka`, `kafka-python-ng`) without protocol translation.
  - **Integrated Schema Registry & Admin API**: Redpanda includes an integrated Schema Registry (`:8081`) and Admin API (`:9644`), removing the need for auxiliary Confluent containers.

### 2. PostgreSQL 16 as Authoritative System of Record
- **Schema & Invariants (`deploy/docker/init.sql`)**:
  - `orders`: Tracks lifecycle states matching `OrderStatus` enum. Enforces a strict `UNIQUE` constraint on `idempotency_key` to guarantee duplicate order requests cannot execute.
  - `fills`: Stores execution details with foreign key to `orders`. Enforces check constraint ensuring `total_fees = stt_tax + exchange_fees + gst_tax + stamp_duty`.
  - `positions`: Holds current per-symbol quantities and average prices.
  - `portfolio_snapshots`: Records periodic ledger snapshots (cash, equity, exposure, realized/unrealized PnL, drawdown).
  - `schema_migrations`: Records applied database migrations with baseline records (`001_initial_schema`, `002_protect_risk_audit_log_truncate`).
- **Append-Only Immutability for `risk_audit_log`**:
  - Row-level trigger (`trg_prevent_risk_audit_log_modification`) strictly blocks `UPDATE` and `DELETE` operations, raising an explicit `RAISE EXCEPTION`.
  - Statement-level trigger (`trg_prevent_risk_audit_log_truncate`, Migration 002) blocks `TRUNCATE TABLE risk_audit_log`, preventing bulk table wipes.
  - **Role Privilege Hardening Note**: While database triggers prevent modification and truncation, defense-in-depth dictates that the runtime application database role should later receive strictly `INSERT` and `SELECT` permissions on `risk_audit_log` (omitting `UPDATE`, `DELETE`, and `TRUNCATE` grants). This least-privilege role separation is scheduled for Sprint 23 (Security Hardening).

### 3. Redis 7 for Low-Latency Intraday State
- **Role**: Key-value state store for high-frequency runtime checks:
  - Global circuit-breaker flag: `alphatrader:floor_halted` (`"0"` or `"1"`).
  - Intraday drawdown tracker and High-Water Mark (HWM) cache.
  - Token-bucket rate limiter counters for order submission throttling.
- **Persistence**: Append-Only File (`AOF`) enabled with `--save 60 1` to survive container restarts without losing critical state.

### 4. Single Source of Truth for Kafka Topics (`TOPIC_REGISTRY`)
- Topic creation is governed entirely by `src/common/contracts.py`.
- `scripts/create_topics.py` reads `TOPIC_REGISTRY`, automatically creating all 9 business topics and all 8 Dead Letter Queue (`.dlq`) topics with their registered partitions and retention periods.
- A one-shot compose container (`alphatrader-redpanda-init`) invokes `init_topics.sh` (generated directly by `create_topics.py`), ensuring that fresh boots have all topics provisioned before application workers start.

---

## 3. Consequences

### Positive
- **Instant Local Deployment**: `make up` brings up all three core services and provisions all 17 Kafka topics in seconds.
- **Audit-Grade Integrity**: The database schema strictly guarantees mathematical invariants and prevents tampering with risk decisions.
- **Clean Separation of Concerns**: High-throughput messaging (Redpanda), ultra-fast state gating (Redis), and durable financial accounting (Postgres) operate independently.
- **Cross-Platform Parity**: Fully tested and verified across Linux CI and Windows host environments (`make.ps1` and `Makefile`).

### Negative / Trade-offs
- **Local Port Management**: Native host database installations (e.g. Postgres on 5432) can conflict. Handled gracefully by exposing container ports via configurable environment variables (defaulting `POSTGRES_PORT=5433` in `.env`).
- **Single-Node Redpanda Limitations**: Running single-node dev mode disables multi-broker replication, appropriate for local development and CI, while production will target a clustered deployment.

---

## 4. Alternatives Considered & Rejected

1. **Apache Kafka (JVM) + KRaft**:
   - *Rejected*: Higher memory footprint (minimum 2–4 GB for Kafka + Schema Registry), longer startup delays (15–30 seconds), and JVM garbage collection spikes that interfere with local laptop performance.
2. **SQLite instead of PostgreSQL**:
   - *Rejected*: Inadequate concurrency support for multiple independent agent processes writing fills, snapshots, and audit records simultaneously. Lacks PostgreSQL trigger and JSONB querying capabilities.
3. **Database-Only Architecture (No Redis)**:
   - *Rejected*: Gating high-frequency order decisions against Postgres would create database connection saturation and higher latency variance compared to in-memory Redis reads.
