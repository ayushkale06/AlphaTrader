# ADR-003: Typed Event Contracts with Pydantic v2, EventEnvelope, Decimal Math, and Topic Registry

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Ayush Kale (Repository Owner & Lead Engineer)
- **Sprint:** Sprint 2 (Schemas & Contracts)

---

## 1. Context

AlphaTrader consists of four autonomous LangGraph agents and six isolated Model Context Protocol (MCP) microservices communicating over nine Redpanda (Kafka API) topics (`market.ticks`, `market.candles`, `signals.technical`, `signals.quant`, `risk.decisions`, `orders.requests`, `orders.fills`, `portfolio.updates`, `system.events`).

In distributed trading architectures, untyped Python `dict` payloads, implicit timezone assumptions, and IEEE-754 binary floating-point numbers (`float`) are leading causes of silent financial calculation drift, reconciliation failures, and runtime crashes:

1. **Untyped Message Drift**: Passing raw `dict[str, Any]` across Kafka topics or MCP boundaries allows fields to be misspelled, omitted, or mutated mid-flight without static or runtime detection.
2. **Binary Floating-Point Inaccuracy**: In IEEE-754 binary floating-point arithmetic, `0.1 + 0.2 == 0.30000000000000004`, and `float(2954.80) * 100` can accumulate sub-paisa rounding errors that break double-entry ledger balance assertions ($\sum \text{Debits} = \sum \text{Credits}$) and statutory NSE tax calculations.
3. **Naive Datetime Ambiguity**: Mixing naive timestamps (`datetime(2026, 10, 13, 9, 15)`) with timezone-aware timestamps across `Asia/Kolkata` (IST, UTC+05:30) and container `UTC` clocks causes subtle 5.5-hour bugs in market-session gating and candle window aggregation.
4. **Uncoordinated Topic Governance**: Without a single programmatic registry mapping Kafka topics to their allowed producers, consumers, partition keys, and payload schemas, microservices can publish unauthorized event types or partition by inconsistent keys (breaking per-symbol ordering guarantees).

---

## 2. Decision

We establish a **strict, zero-dict typed contract layer** across the entire platform consisting of four components:

### 1. Immutable Pydantic v2 Domain Schemas (`src/common/schemas/`)
Every domain object inherits from `AlphaTraderBaseModel`, which enforces:
- `frozen=True`: Models are immutable value objects once constructed.
- `extra="forbid"`: Unknown fields are immediately rejected with a `ValidationError`.
- `populate_by_name=True` and `validate_default=True`.

### 2. Why `Decimal` (Rejecting `float`) and `UTC` (Rejecting Naive Datetimes)
- **Strict `Decimal` Financial Types (`validate_decimal_no_float`)**: All prices (`PositivePrice`), quantities (`PositiveQuantity`, `NonNegativeQuantity`, `SignedQuantity`), monetary amounts (`NonNegativeMoney`, `SignedMoney`), and bounded ratios (`UnitIntervalDecimal`) are backed by `decimal.Decimal` and serialized deterministically as plain decimal strings (`format(value, "f")`) in JSON. Passing a Python `float` (e.g., `2954.50`) raises an immediate `ValueError` to prevent IEEE-754 precision loss at the ingestion boundary.
- **Strict UTC Timestamps (`validate_utc_datetime`)**: Every timestamp field uses `UtcDatetime`. Naive `datetime` objects or timezone-less ISO-8601 strings are rejected with a `ValueError`. Aware timestamps in any timezone (such as `Asia/Kolkata` `+05:30`) are normalized to `datetime.UTC` on construction, while exchange-session logic in `src/common/schemas/market.py` explicitly projects `UTC` into `ZoneInfo("Asia/Kolkata")` to evaluate NSE hours (`09:15–15:30 IST`).

### 3. Generic `EventEnvelope[PayloadT]` (`src/common/schemas/envelope.py`)
Every Kafka message is wrapped in `EventEnvelope[PayloadT]`, carrying:
- `event_id` (`UUID`): Unique message identifier for idempotent consumer deduplication.
- `correlation_id` (`NonEmptyStr`): Propagated end-to-end trace ID from the originating tick.
- `causation_id` (`UUID | None`): Parent `event_id` that directly triggered this event.
- `schema_version` (`str`, currently `"1.0.0"`): Enforced by `SchemaVersionMismatchError` on deserialization.
- `produced_at` (`UtcDatetime`) and `source` (`ComponentSource`).
- Deterministic UTF-8 byte serialization helpers (`to_json_bytes()` with `sort_keys=True` and `from_json_bytes()`).

### 4. Typed Topic Contract Registry & Committed JSON Schemas (`src/common/contracts.py`, `scripts/export_schemas.py`)
- `TOPIC_REGISTRY` maps all 9 canonical `TopicName` values to their `TopicContract` (payload model, parameterized `EventEnvelope` model, `MessageKeyRule`, partition count, retention hours, DLQ policy, `allowed_producers`, and `allowed_consumers`).
- `validate_topic_message()` validates payload schema, producer authorization, and partition key extraction in a single call.
- `make schemas` (`python -m scripts.export_schemas`) exports 23 canonical JSON Schema files to `docs/contracts/*.schema.json` and generates `docs/contracts.md` from real validated model instances. `make check` runs `python -m scripts.export_schemas --check` in CI to fail builds if committed schemas drift from code.

---

## 3. Consequences

### Positive
- **Zero Untyped Dicts**: Every boundary between agents, MCP servers, and Redpanda topics is statically checked by `mypy --strict` and validated at runtime by Pydantic v2 (`rust`/`pydantic-core`).
- **Exact Accounting**: `Decimal` string serialization guarantees zero floating-point drift across JSON, Redpanda, Redis, and PostgreSQL `NUMERIC` columns.
- **Self-Verifying Documentation**: `docs/contracts/*.schema.json` and `docs/contracts.md` are generated directly from the Python models and verified on every CI run.

### Negative / Trade-offs
- **Slightly Larger Wire Payloads**: JSON with string-encoded `Decimal` fields and UUID strings is larger on the wire (~300–600 bytes per message) than binary Protobuf or Avro. At NIFTY 50 paper-trading throughput ($100\text{–}2,000\text{ msgs/sec}$), this represents $< 1.2\text{ MB/s}$, well within Redpanda's local and cloud bandwidth limits.
- **Explicit `Decimal` Construction Required**: Callers must pass `Decimal("2954.50")`, `"2954.50"`, or `int` rather than `float` literals.

---

## 4. Alternatives Rejected & When We Would Revisit

| Alternative | Reason for Rejection in Sprint 2 | When We Would Revisit |
|---|---|---|
| **Apache Avro + External Schema Registry** | Requires running and managing a separate Schema Registry service before local infra exists, lacks native Python `Decimal` ergonomics without custom logical-type boilerplate, and obscures human-readable JSON debugging during agent/MCP audits. | If message volume exceeds $50,000\text{ msgs/sec}$ or multi-language (C++/Rust/Go) consumers require binary schema evolution via Redpanda Schema Registry. |
| **Protocol Buffers (Protobuf v3)** | Protobuf has no native `Decimal` or timezone-aware `datetime` primitive (requiring custom string/int64 mantissa-exponent wrappers) and adds a `protoc` code-generation build step that duplicates Pydantic validation logic. | If we introduce low-latency gRPC streaming between co-located C++/Rust execution microservices. |
| **Untyped `dict` + `dataclasses` without Pydantic** | Standard `dataclasses` do not perform runtime validation, JSON Schema generation, or automatic string-to-`Decimal`/`UTC` coercion on deserialized network payloads. | Never for external/network boundaries; frozen `dataclass` is used only for internal static registries like `TopicContract`. |
