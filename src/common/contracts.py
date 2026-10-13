"""Kafka/Redpanda topic contract registry and message validation engine.

Defines the single source of truth mapping every AlphaTrader topic to its
Pydantic v2 payload model, partitioning key rule, retention policy, DLQ setting,
and authorized producer/consumer components.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from src.common.schemas.base import AlphaTraderBaseModel
from src.common.schemas.envelope import (
    ComponentSource,
    EventEnvelope,
    SchemaVersionMismatchError,
)
from src.common.schemas.market import (
    Candle,
    CandleInterval,
    DataProvenance,
    FeedMode,
    Tick,
)
from src.common.schemas.orders import Fill, OrderRequest, OrderType, Side
from src.common.schemas.portfolio import PortfolioSnapshot, Position
from src.common.schemas.risk import (
    RiskDecision,
    RiskInputSnapshot,
    RiskReasonCode,
    RiskVerdict,
)
from src.common.schemas.signals import (
    Direction,
    MarketRegime,
    QuantSignal,
    TechnicalSignal,
)
from src.common.schemas.system import EventSeverity, SystemEvent, SystemEventType


class TopicContractViolationError(ValueError):
    """Raised when a message violates its registered Kafka topic contract."""


class TopicName(StrEnum):
    """Canonical Redpanda/Kafka topic names across the AlphaTrader platform."""

    MARKET_TICKS = "market.ticks"
    MARKET_CANDLES = "market.candles"
    SIGNALS_TECHNICAL = "signals.technical"
    SIGNALS_QUANT = "signals.quant"
    RISK_DECISIONS = "risk.decisions"
    ORDERS_REQUESTS = "orders.requests"
    ORDERS_FILLS = "orders.fills"
    PORTFOLIO_UPDATES = "portfolio.updates"
    SYSTEM_EVENTS = "system.events"


class MessageKeyRule(StrEnum):
    """Deterministic partition key extraction rules for Kafka messages."""

    SYMBOL = "payload.symbol"
    ORDER_REQUEST_ID = "payload.order_request_id"
    SNAPSHOT_ID = "payload.snapshot_id"
    COMPONENT = "payload.component"


_KEY_RULE_TO_FIELD: dict[MessageKeyRule, str] = {
    MessageKeyRule.SYMBOL: "symbol",
    MessageKeyRule.ORDER_REQUEST_ID: "order_request_id",
    MessageKeyRule.SNAPSHOT_ID: "snapshot_id",
    MessageKeyRule.COMPONENT: "component",
}


@dataclass(frozen=True, slots=True)
class TopicContract:
    """Immutable specification of a single Redpanda/Kafka topic contract."""

    topic: TopicName
    payload_model: type[AlphaTraderBaseModel]
    envelope_model: type[EventEnvelope[Any]]
    key_rule: MessageKeyRule
    partitions: int
    retention_hours: int
    needs_dlq: bool
    dlq_topic: str | None
    allowed_producers: frozenset[ComponentSource]
    allowed_consumers: frozenset[ComponentSource]
    description: str

    def __post_init__(self) -> None:
        if not issubclass(self.payload_model, AlphaTraderBaseModel):
            msg = (
                "payload_model must be a subclass of AlphaTraderBaseModel, "
                f"got {self.payload_model!r}."
            )
            raise TypeError(msg)
        if not issubclass(self.envelope_model, EventEnvelope):
            msg = (
                f"envelope_model must be a subclass of EventEnvelope, got {self.envelope_model!r}."
            )
            raise TypeError(msg)
        if not (1 <= self.partitions <= 64):
            msg = f"partitions must be in [1, 64], got {self.partitions}."
            raise ValueError(msg)
        if self.retention_hours < 1:
            msg = f"retention_hours must be >= 1, got {self.retention_hours}."
            raise ValueError(msg)

    def extract_message_key(self, envelope: EventEnvelope[Any]) -> str:
        """Extract the deterministic Kafka partition key from a validated envelope."""
        attr_name = _KEY_RULE_TO_FIELD[self.key_rule]
        raw_val = getattr(envelope.payload, attr_name, None)
        if raw_val is None:
            msg = (
                f"Cannot extract key {self.key_rule.value} from payload of type "
                f"{type(envelope.payload).__name__} on topic {self.topic.value}."
            )
            raise TopicContractViolationError(msg)
        return str(raw_val)


TOPIC_REGISTRY: dict[TopicName, TopicContract] = {
    TopicName.MARKET_TICKS: TopicContract(
        topic=TopicName.MARKET_TICKS,
        payload_model=Tick,
        envelope_model=EventEnvelope[Tick],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=10,
        retention_hours=72,
        needs_dlq=True,
        dlq_topic="market.ticks.dlq",
        allowed_producers=frozenset({ComponentSource.MARKET_DATA_STREAMER}),
        allowed_consumers=frozenset(
            {
                ComponentSource.TECHNICAL_ANALYST,
                ComponentSource.QUANT_STRATEGY_ENGINE,
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.PAPER_EXECUTION_BROKER,
                ComponentSource.PORTFOLIO_LEDGER,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Normalized real-time or replayed NSE NIFTY 50 quote/trade ticks.",
    ),
    TopicName.MARKET_CANDLES: TopicContract(
        topic=TopicName.MARKET_CANDLES,
        payload_model=Candle,
        envelope_model=EventEnvelope[Candle],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=10,
        retention_hours=168,
        needs_dlq=True,
        dlq_topic="market.candles.dlq",
        allowed_producers=frozenset({ComponentSource.MARKET_DATA_STREAMER}),
        allowed_consumers=frozenset(
            {
                ComponentSource.TECHNICAL_ANALYST,
                ComponentSource.QUANT_STRATEGY_ENGINE,
                ComponentSource.ANALYST_AGENT,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Aggregated OHLCV candlestick bars for NIFTY 50 constituents.",
    ),
    TopicName.SIGNALS_TECHNICAL: TopicContract(
        topic=TopicName.SIGNALS_TECHNICAL,
        payload_model=TechnicalSignal,
        envelope_model=EventEnvelope[TechnicalSignal],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=6,
        retention_hours=168,
        needs_dlq=True,
        dlq_topic="signals.technical.dlq",
        allowed_producers=frozenset(
            {ComponentSource.TECHNICAL_ANALYST, ComponentSource.ANALYST_AGENT}
        ),
        allowed_consumers=frozenset(
            {
                ComponentSource.QUANT_STRATEGY_ENGINE,
                ComponentSource.QUANT_AGENT,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Multi-timeframe technical indicator snapshots and regime classifications.",
    ),
    TopicName.SIGNALS_QUANT: TopicContract(
        topic=TopicName.SIGNALS_QUANT,
        payload_model=QuantSignal,
        envelope_model=EventEnvelope[QuantSignal],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=6,
        retention_hours=168,
        needs_dlq=True,
        dlq_topic="signals.quant.dlq",
        allowed_producers=frozenset(
            {ComponentSource.QUANT_STRATEGY_ENGINE, ComponentSource.QUANT_AGENT}
        ),
        allowed_consumers=frozenset(
            {
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.RISK_AGENT,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Quantitative factor alpha signals and fractional-Kelly target weights.",
    ),
    TopicName.RISK_DECISIONS: TopicContract(
        topic=TopicName.RISK_DECISIONS,
        payload_model=RiskDecision,
        envelope_model=EventEnvelope[RiskDecision],
        key_rule=MessageKeyRule.ORDER_REQUEST_ID,
        partitions=6,
        retention_hours=720,
        needs_dlq=True,
        dlq_topic="risk.decisions.dlq",
        allowed_producers=frozenset({ComponentSource.RISK_GUARDRAIL}),
        allowed_consumers=frozenset(
            {
                ComponentSource.PAPER_EXECUTION_BROKER,
                ComponentSource.PORTFOLIO_LEDGER,
                ComponentSource.RISK_AGENT,
                ComponentSource.EXECUTION_AGENT,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Deterministic non-LLM risk guardrail verdicts (APPROVE / REJECT / HALT).",
    ),
    TopicName.ORDERS_REQUESTS: TopicContract(
        topic=TopicName.ORDERS_REQUESTS,
        payload_model=OrderRequest,
        envelope_model=EventEnvelope[OrderRequest],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=6,
        retention_hours=720,
        needs_dlq=True,
        dlq_topic="orders.requests.dlq",
        allowed_producers=frozenset(
            {
                ComponentSource.QUANT_AGENT,
                ComponentSource.RISK_AGENT,
                ComponentSource.EXECUTION_AGENT,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        allowed_consumers=frozenset(
            {
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.PAPER_EXECUTION_BROKER,
                ComponentSource.PORTFOLIO_LEDGER,
            }
        ),
        description="Idempotent paper order requests submitted for risk gatekeeping.",
    ),
    TopicName.ORDERS_FILLS: TopicContract(
        topic=TopicName.ORDERS_FILLS,
        payload_model=Fill,
        envelope_model=EventEnvelope[Fill],
        key_rule=MessageKeyRule.SYMBOL,
        partitions=6,
        retention_hours=2160,
        needs_dlq=True,
        dlq_topic="orders.fills.dlq",
        allowed_producers=frozenset({ComponentSource.PAPER_EXECUTION_BROKER}),
        allowed_consumers=frozenset(
            {
                ComponentSource.PORTFOLIO_LEDGER,
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Simulated NSE paper order fills with statutory tax and slippage breakdown.",
    ),
    TopicName.PORTFOLIO_UPDATES: TopicContract(
        topic=TopicName.PORTFOLIO_UPDATES,
        payload_model=PortfolioSnapshot,
        envelope_model=EventEnvelope[PortfolioSnapshot],
        key_rule=MessageKeyRule.SNAPSHOT_ID,
        partitions=3,
        retention_hours=720,
        needs_dlq=True,
        dlq_topic="portfolio.updates.dlq",
        allowed_producers=frozenset({ComponentSource.PORTFOLIO_LEDGER}),
        allowed_consumers=frozenset(
            {
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.QUANT_STRATEGY_ENGINE,
                ComponentSource.ORCHESTRATOR,
            }
        ),
        description="Real-time paper portfolio cash, equity, exposure, PnL, and drawdown state.",
    ),
    TopicName.SYSTEM_EVENTS: TopicContract(
        topic=TopicName.SYSTEM_EVENTS,
        payload_model=SystemEvent,
        envelope_model=EventEnvelope[SystemEvent],
        key_rule=MessageKeyRule.COMPONENT,
        partitions=3,
        retention_hours=720,
        needs_dlq=False,
        dlq_topic=None,
        allowed_producers=frozenset(ComponentSource),
        allowed_consumers=frozenset(
            {
                ComponentSource.ORCHESTRATOR,
                ComponentSource.RISK_GUARDRAIL,
                ComponentSource.SYSTEM,
            }
        ),
        description="Operational lifecycle events (startup, shutdown, halt, resume, heartbeat).",
    ),
}


def get_topic_contract(topic: TopicName | str) -> TopicContract:
    """Retrieve the registered TopicContract for a topic name or raise an error."""
    try:
        resolved_topic = topic if isinstance(topic, TopicName) else TopicName(topic)
    except ValueError as exc:
        msg = (
            f"Unregistered Kafka topic {topic!r}. Registered topics: {[t.value for t in TopicName]}"
        )
        raise TopicContractViolationError(msg) from exc

    contract = TOPIC_REGISTRY.get(resolved_topic)
    if contract is None:
        msg = f"Topic {resolved_topic.value!r} has no registered TopicContract."
        raise TopicContractViolationError(msg)
    return contract


def validate_topic_message(
    topic: TopicName | str,
    message: EventEnvelope[Any] | bytes | str | dict[str, Any],
    *,
    message_key: str | None = None,
) -> EventEnvelope[AlphaTraderBaseModel]:
    """Validate any envelope/JSON/dict message against its registered topic contract.

    Checks:
    1. Topic is registered in ``TOPIC_REGISTRY``.
    2. Envelope and ``schema_version`` are valid.
    3. ``payload`` conforms strictly to the topic's ``payload_model``.
    4. ``envelope.source`` is an authorized producer for the topic.
    5. ``message_key`` (if supplied) matches the topic's deterministic ``key_rule``.
    """
    contract = get_topic_contract(topic)

    try:
        if isinstance(message, (bytes, str)):
            validated: EventEnvelope[AlphaTraderBaseModel] = (
                contract.envelope_model.from_json_bytes(message)
            )
        elif isinstance(message, dict):
            validated = contract.envelope_model.model_validate(message)
        elif isinstance(message, EventEnvelope):
            validated = contract.envelope_model.model_validate(message.model_dump())
        else:
            msg = f"Unsupported message container type: {type(message).__name__}."
            raise TopicContractViolationError(msg)
    except (ValidationError, SchemaVersionMismatchError) as exc:
        msg = f"Payload validation failed for topic {contract.topic.value!r}: {exc}"
        raise TopicContractViolationError(msg) from exc

    if validated.source not in contract.allowed_producers:
        allowed_list = sorted(c.value for c in contract.allowed_producers)
        msg = (
            f"Producer {validated.source.value!r} is not authorized to publish to "
            f"topic {contract.topic.value!r}. Allowed producers: {allowed_list}."
        )
        raise TopicContractViolationError(msg)

    # Provenance is strictly required on market topics for schema_version >= 1.1.0 (ADR-005)
    if (
        contract.topic in (TopicName.MARKET_TICKS, TopicName.MARKET_CANDLES)
        and validated.schema_version >= "1.1.0"
    ):
        payload_provenance = getattr(validated.payload, "provenance", None)
        if payload_provenance is None:
            msg = (
                f"Topic {contract.topic.value!r} requires non-null 'provenance' "
                f"for schema_version {validated.schema_version!r} (Sprint 4 / ADR-005)."
            )
            raise TopicContractViolationError(msg)

    # Schema 1.2.0 Candle rules (ADR-006): is_final must be non-None,
    # and is_final=True implies close_time <= produced_at
    if contract.topic == TopicName.MARKET_CANDLES and validated.schema_version >= "1.2.0":
        candle_payload: Any = validated.payload
        is_final = getattr(candle_payload, "is_final", None)
        if is_final is None:
            msg = (
                f"Topic {contract.topic.value!r} requires non-null 'is_final' "
                f"for schema_version {validated.schema_version!r} (Sprint 5 / ADR-006)."
            )
            raise TopicContractViolationError(msg)
        if is_final is True and candle_payload.close_time > validated.produced_at:
            msg = (
                f"Topic {contract.topic.value!r} contract violation: closed final candle "
                f"(is_final=True) cannot have close_time ({candle_payload.close_time}) "
                f"> produced_at ({validated.produced_at})."
            )
            raise TopicContractViolationError(msg)

    # Schema 1.2.0 TechnicalSignal rules (ADR-006): provenance and config_version must be non-None
    if contract.topic == TopicName.SIGNALS_TECHNICAL and validated.schema_version >= "1.2.0":
        signal_payload: Any = validated.payload
        signal_provenance = getattr(signal_payload, "provenance", None)
        if signal_provenance is None:
            msg = (
                f"Topic {contract.topic.value!r} requires non-null 'provenance' "
                f"for schema_version {validated.schema_version!r} (Sprint 5 / ADR-006)."
            )
            raise TopicContractViolationError(msg)
        signal_config = getattr(signal_payload, "config_version", None)
        if not signal_config:
            msg = (
                f"Topic {contract.topic.value!r} requires non-empty 'config_version' "
                f"for schema_version {validated.schema_version!r} (Sprint 5 / ADR-006)."
            )
            raise TopicContractViolationError(msg)

    expected_key = contract.extract_message_key(validated)
    if message_key is not None and message_key != expected_key:
        msg = (
            f"Invalid message key {message_key!r} for topic {contract.topic.value!r} "
            f"(rule {contract.key_rule.value}): expected {expected_key!r}."
        )
        raise TopicContractViolationError(msg)

    return validated


def build_sample_topic_messages() -> dict[TopicName, EventEnvelope[AlphaTraderBaseModel]]:
    """Construct deterministic, validated sample EventEnvelope instances for all 9 topics."""
    fixed_ts = datetime(2026, 10, 13, 4, 30, 0, tzinfo=UTC)
    candle_open_ts = datetime(2026, 10, 13, 4, 25, 0, tzinfo=UTC)
    cid = "cid-nifty50-sample-0001"
    order_req_id = UUID("11111111-2222-4333-8444-555555555555")
    order_id = UUID("22222222-3333-4444-8555-666666666666")
    snapshot_id = UUID("33333333-4444-4555-8666-777777777777")

    sample_provenance = DataProvenance(
        provider="simulated_feed",
        feed_mode=FeedMode.SIMULATED,
        is_delayed=False,
        delay_seconds=0,
        ingested_at=fixed_ts,
    )

    return {
        TopicName.MARKET_TICKS: EventEnvelope[Tick](
            event_id=UUID("a0000001-0000-4000-8000-000000000001"),
            correlation_id=cid,
            produced_at=fixed_ts,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=Tick(
                symbol="RELIANCE.NS",
                timestamp=fixed_ts,
                last_price=Decimal("2954.50"),
                volume=Decimal("1250"),
                bid_price=Decimal("2954.25"),
                ask_price=Decimal("2954.75"),
                sequence_number=1042,
                provenance=sample_provenance,
            ),
        ),
        TopicName.MARKET_CANDLES: EventEnvelope[Candle](
            event_id=UUID("a0000002-0000-4000-8000-000000000002"),
            correlation_id=cid,
            produced_at=fixed_ts,
            source=ComponentSource.MARKET_DATA_STREAMER,
            payload=Candle(
                symbol="RELIANCE.NS",
                interval=CandleInterval.FIVE_MINUTES,
                open_time=candle_open_ts,
                close_time=fixed_ts,
                open=Decimal("2948.00"),
                high=Decimal("2958.00"),
                low=Decimal("2946.50"),
                close=Decimal("2954.50"),
                volume=Decimal("48500"),
                vwap=Decimal("2952.80"),
                trade_count=612,
                provenance=sample_provenance,
                is_final=True,
            ),
        ),
        TopicName.SIGNALS_TECHNICAL: EventEnvelope[TechnicalSignal](
            event_id=UUID("a0000003-0000-4000-8000-000000000003"),
            correlation_id=cid,
            causation_id=UUID("a0000002-0000-4000-8000-000000000002"),
            produced_at=fixed_ts,
            source=ComponentSource.TECHNICAL_ANALYST,
            payload=TechnicalSignal(
                signal_id=UUID("b0000003-0000-4000-8000-000000000003"),
                symbol="RELIANCE.NS",
                evaluated_at=fixed_ts,
                interval=CandleInterval.FIVE_MINUTES,
                direction=Direction.LONG,
                confidence=Decimal("0.78"),
                regime=MarketRegime.TRENDING_UP,
                indicators={
                    "ema_9": Decimal("2951.40"),
                    "ema_21": Decimal("2944.80"),
                    "rsi_14": Decimal("63.25"),
                    "atr_14": Decimal("14.60"),
                    "vwap": Decimal("2952.80"),
                },
                rules_fired=["ema_9_above_ema_21", "rsi_bullish_momentum"],
                config_version="1.0.0",
                provenance=sample_provenance,
            ),
        ),
        TopicName.SIGNALS_QUANT: EventEnvelope[QuantSignal](
            event_id=UUID("a0000004-0000-4000-8000-000000000004"),
            correlation_id=cid,
            causation_id=UUID("a0000003-0000-4000-8000-000000000003"),
            produced_at=fixed_ts,
            source=ComponentSource.QUANT_STRATEGY_ENGINE,
            payload=QuantSignal(
                signal_id=UUID("b0000004-0000-4000-8000-000000000004"),
                symbol="RELIANCE.NS",
                evaluated_at=fixed_ts,
                strategy_name="intraday_vwap_momentum_v1",
                direction=Direction.LONG,
                target_weight=Decimal("0.04"),
                expected_edge=Decimal("18.50"),
                confidence=Decimal("0.74"),
                parameters={
                    "kelly_fraction": Decimal("0.25"),
                    "lookback_bars": 20,
                    "atr_stop_multiplier": Decimal("2.0"),
                },
            ),
        ),
        TopicName.RISK_DECISIONS: EventEnvelope[RiskDecision](
            event_id=UUID("a0000005-0000-4000-8000-000000000005"),
            correlation_id=cid,
            causation_id=UUID("a0000006-0000-4000-8000-000000000006"),
            produced_at=fixed_ts,
            source=ComponentSource.RISK_GUARDRAIL,
            payload=RiskDecision(
                decision_id=UUID("b0000005-0000-4000-8000-000000000005"),
                order_request_id=order_req_id,
                evaluated_at=fixed_ts,
                decision=RiskVerdict.APPROVE,
                rule_fired="ALL_PRE_TRADE_INVARIANTS_PASSED",
                reason_code=RiskReasonCode.WITHIN_LIMITS,
                inputs_snapshot=RiskInputSnapshot(
                    symbol="RELIANCE.NS",
                    proposed_notional=Decimal("295450.00"),
                    portfolio_equity=Decimal("10000000.00"),
                    current_drawdown_pct=Decimal("0.15"),
                    max_daily_drawdown_pct=Decimal("2.00"),
                    post_trade_position_pct=Decimal("2.95"),
                    max_position_size_pct=Decimal("5.00"),
                    open_positions_count=3,
                    max_open_positions=10,
                    orders_last_minute=2,
                ),
                approval_token="rtok-hmac-sha256-9f86d081884c7d659a2feaa0c55ad015",
                message="Candidate paper order satisfies all deterministic pre-trade risk limits.",
            ),
        ),
        TopicName.ORDERS_REQUESTS: EventEnvelope[OrderRequest](
            event_id=UUID("a0000006-0000-4000-8000-000000000006"),
            correlation_id=cid,
            causation_id=UUID("a0000004-0000-4000-8000-000000000004"),
            produced_at=fixed_ts,
            source=ComponentSource.ORCHESTRATOR,
            payload=OrderRequest(
                order_request_id=order_req_id,
                idempotency_key="idem-reliance-20261013-043000-001",
                symbol="RELIANCE.NS",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("100"),
                limit_price=Decimal("2955.00"),
                stop_loss_price=Decimal("2925.00"),
                take_profit_price=Decimal("3015.00"),
                strategy_name="intraday_vwap_momentum_v1",
                requested_at=fixed_ts,
            ),
        ),
        TopicName.ORDERS_FILLS: EventEnvelope[Fill](
            event_id=UUID("a0000007-0000-4000-8000-000000000007"),
            correlation_id=cid,
            causation_id=UUID("a0000005-0000-4000-8000-000000000005"),
            produced_at=fixed_ts,
            source=ComponentSource.PAPER_EXECUTION_BROKER,
            payload=Fill(
                fill_id=UUID("b0000007-0000-4000-8000-000000000007"),
                order_id=order_id,
                order_request_id=order_req_id,
                symbol="RELIANCE.NS",
                side=Side.BUY,
                fill_price=Decimal("2954.80"),
                fill_quantity=Decimal("100"),
                slippage_bps=Decimal("1.02"),
                stt_tax=Decimal("73.87"),
                exchange_fees=Decimal("9.60"),
                gst_tax=Decimal("1.73"),
                stamp_duty=Decimal("8.86"),
                total_fees=Decimal("94.06"),
                filled_at=fixed_ts,
            ),
        ),
        TopicName.PORTFOLIO_UPDATES: EventEnvelope[PortfolioSnapshot](
            event_id=UUID("a0000008-0000-4000-8000-000000000008"),
            correlation_id=cid,
            causation_id=UUID("a0000007-0000-4000-8000-000000000007"),
            produced_at=fixed_ts,
            source=ComponentSource.PORTFOLIO_LEDGER,
            payload=PortfolioSnapshot(
                snapshot_id=snapshot_id,
                as_of=fixed_ts,
                cash=Decimal("9704425.94"),
                equity=Decimal("10000045.94"),
                exposure=Decimal("295620.00"),
                realized_pnl=Decimal("0.00"),
                unrealized_pnl=Decimal("140.00"),
                drawdown=Decimal("0.00"),
                high_water_mark=Decimal("10000045.94"),
                positions=[
                    Position(
                        symbol="RELIANCE.NS",
                        quantity=Decimal("100"),
                        average_entry_price=Decimal("2954.80"),
                        mark_price=Decimal("2956.20"),
                        market_value=Decimal("295620.00"),
                        unrealized_pnl=Decimal("140.00"),
                        realized_pnl=Decimal("0.00"),
                        updated_at=fixed_ts,
                    )
                ],
            ),
        ),
        TopicName.SYSTEM_EVENTS: EventEnvelope[SystemEvent](
            event_id=UUID("a0000009-0000-4000-8000-000000000009"),
            correlation_id=cid,
            produced_at=fixed_ts,
            source=ComponentSource.ORCHESTRATOR,
            payload=SystemEvent(
                system_event_id=UUID("b0000009-0000-4000-8000-000000000009"),
                event_type=SystemEventType.HEARTBEAT,
                severity=EventSeverity.INFO,
                component=ComponentSource.ORCHESTRATOR,
                occurred_at=fixed_ts,
                message="LangGraph orchestrator heartbeat healthy; floor active in PAPER mode.",
                metadata={
                    "active_agents": 4,
                    "floor_halted": False,
                    "uptime_seconds": 3600,
                },
            ),
        ),
    }
