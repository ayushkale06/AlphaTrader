# ADR-006: Incremental Technical Indicators, Explainable Signals, and Provenance Merging

- **Status**: Accepted
- **Date**: 2026-10-13
- **Author**: Senior Lead Engineer
- **Deciders**: AlphaTrader Technical Committee
- **Sprint**: Sprint 5 (Days 21-25)

---

## 1. Context and Problem Statement

In AlphaTrader's multi-agent architecture, the Analyst Agent requires access to multi-timeframe technical indicator snapshots and rule-based regime classifications over NSE NIFTY 50 candlestick bars.
Previously:
1. Indicators were either recomputed over full histories in batch or not streaming-safe.
2. In-flight (partial) candlesticks could prematurely trigger false trading signals before bar close.
3. Signal confidence was ambiguous, carrying the risk of being mistaken for a machine learning model's calibrated profit probability.
4. Signals derived across multi-bar histories lacked a rigorous deterministic merge protocol for data provenance (feed mode, latency, and provider attribution).

---

## 2. Decision Drivers

- **Zero Lookahead**: The indicator value at bar $t$ must strictly depend on bars $\le t$.
- **Streaming Efficiency ($O(1)$ updates)**: Incremental evaluation per new completed candlestick bar, maintaining bounded memory buffers.
- **Explainability & Non-LLM Decisions**: All technical signals must be generated deterministically from versioned rule sets (`config/technical_rules.json`). No LLM may decide or mutate signals.
- **Data Honesty & Provenance Merge**: Signals merging multiple candles or sources must propagate worst-case trustworthiness and explicit latencies.
- **Separation of Concerns**: The Technical Analyst MCP server produces indicator features and directional opinions. It does **not** size positions, manage risk, or execute orders.

---

## 3. Considered Options

1. **Option A: Batch Recalculation on Demand**: Fetch past 200 bars from storage on every MCP tool call and compute indicators via pandas.
2. **Option B: Pure Incremental Streaming with Cold-Start Checkpointing (Selected)**: Streaming consumer ingests closed bars (`is_final=True`), updates $O(1)$ stateful indicators, caches state and snapshots in Redis, and exposes read-only FastMCP tools.

---

## 4. Decision Outcome

We choose **Option B**.

### 4.1 Incremental Indicator Engine

The indicator engine implements streaming, $O(1)$ per new candle algorithms:
- **SMA**: Rolling queue of length $N$, running sum update.
- **EMA**: Recursive $\alpha = \frac{2}{N+1}$ smoothing with initial bar recursion and explicit $N$-bar warmup gate.
- **RSI**: J. Welles Wilder's original smoothed relative strength index over 14 bars.
- **MACD**: Fast EMA (12), Slow EMA (26), Signal EMA (9), Histogram = MACD Line - Signal Line.
- **Bollinger Bands**: 20-period rolling sum and sum-of-squares for $O(1)$ sample variance with $\pm 2.0$ standard deviations.
- **ATR**: Wilder smoothed True Range over 14 bars.
- **Rolling VWAP**: Intraday typical price $\times$ volume running accumulator, resetting at session open boundary.
- **ADX**: Directional movement index (+DI, -DI) with Wilder smoothed DX over 14 periods.
- **Stochastic**: Rolling 14-period highest high and lowest low with 3-period smoothing for %K and %D.

**Boundary Invariant**: Inputs and outputs strictly enforce Decimal-safe contracts. 64-bit IEEE 754 floats are permitted internally for streaming efficiency and numerical parity with reference libraries.

### 4.2 Explicit Warmup and Zero Lookahead

- Indicators report `is_ready=False` and return `None` until their required warmup bar window is fulfilled.
- Indicators never emit NaN, sentinel zeroes, or premature estimates during warmup.
- Zero lookahead is verified by property-based and truncated-history regression tests.

### 4.3 Signal Confidence Definition

> [!IMPORTANT]
> **Confidence Score Definition**:
> In AlphaTrader, `TechnicalSignal.confidence` is defined strictly as a **rule-agreement score** bounded in $[0, 1]$:
> $$\text{confidence} = \frac{\text{count}(\text{rules agreeing with winning direction})}{\text{total triggered directional rules}}$$
> It is **NOT** a calibrated probability of profit or statistical win rate. It merely quantifies internal consensus among deterministic indicator rules.

### 4.4 Data Provenance Merge Rule

When a technical signal aggregates indicators derived across candlestick histories or multiple data feeds:
1. **Feed Mode**: The least trustworthy mode strictly wins:
   $$\text{SIMULATED} \succ \text{REPLAY} \succ \text{LIVE}$$
   If any input bar is SIMULATED, the resulting signal provenance is marked SIMULATED.
2. **Delayed Status (`is_delayed`)**: Logical OR. If any constituent candle was delayed, the signal is marked delayed.
3. **Delay Seconds (`delay_seconds`)**: Maximum observed delay across constituent inputs:
   $$\text{delay\_seconds} = \max_{i}(\text{delay\_seconds}_i)$$
4. **Provider Attribution**: If all constituent candles share the same provider, that provider string is preserved. If inputs derive from multiple providers, the provider is set to `"mixed"`.
5. **State Restore**: Indicator state restored from Redis on cold start retains its latest known provenance. Only final candles (`is_final=True`) are processed.

---

## 5. Architectural Invariants and Quality Gates

1. **Read-Only MCP Server**: Tools (`get_indicators`, `get_signal`, `explain_signal`, `list_supported_indicators`, `get_analyzer_health`) are pure and side-effect free.
2. **Schema 1.2.0 Compliance**: Every emitted `TechnicalSignal` carries required `interval`, `regime`, `config_version="1.0.0"`, `rules_fired`, and validated `provenance`.
3. **Kafka Causation**: Every `TechnicalSignal` envelope links `causation_id` to the trigger `Candle` envelope's `event_id`.
4. **Coverage Gate**: `src/mcp_servers/technical_analyst` enforces a $\ge 90\%$ test coverage gate in local make targets and GitHub Actions CI.
