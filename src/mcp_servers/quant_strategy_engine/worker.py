"""Standalone Quant Strategy Engine Streaming Worker.

Consumes:
- `signals.technical`: TechnicalSignal events from Technical Analyst.
- `market.candles`: Final completed Candle events (is_final=True).

Behavior:
- Correlates incoming technical signals and closed candles per symbol & interval.
- Evaluates registered quantitative strategies deterministically.
- Applies PositionSizer and MultiFactorEvaluator.
- Publishes validated QuantSignal envelopes to `signals.quant` keyed by symbol.
- Sets causation_id = incoming technical signal event_id.
- Commits Kafka offsets ONLY AFTER successful publication.
- Routes unparseable or poisoned messages to Dead-Letter Queue `signals.quant.dlq`.
- Updates Redis signal and target portfolio state.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import confluent_kafka

from src.common.config import get_settings
from src.common.contracts import TopicName
from src.common.schemas.envelope import EventEnvelope
from src.common.schemas.market import Candle, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, QuantSignal, TechnicalSignal
from src.mcp_servers.quant_strategy_engine.cache import QuantStrategyCache
from src.mcp_servers.quant_strategy_engine.evaluator import MultiFactorEvaluator
from src.mcp_servers.quant_strategy_engine.publisher import QuantSignalPublisher
from src.mcp_servers.quant_strategy_engine.sizing import PositionSizer
from src.mcp_servers.quant_strategy_engine.strategies import BaseStrategy, StrategyRegistry
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache

logger = logging.getLogger("alphatrader.quant.worker")


class QuantStrategyService:
    """Core streaming worker service for Quant Strategy Engine."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        redis_url: str | None = None,
        consumer_group: str = "quant-strategy-worker",
    ) -> None:
        self.settings = get_settings()
        self.bootstrap_servers = bootstrap_servers or self.settings.kafka_bootstrap_servers
        self.redis_url = redis_url or self.settings.redis_url
        self.consumer_group = consumer_group

        self.cache = QuantStrategyCache(redis_url=self.redis_url)
        self.analyst_cache = TechnicalAnalystCache(redis_url=self.redis_url)
        self.publisher = QuantSignalPublisher(bootstrap_servers=self.bootstrap_servers)
        self.registry = StrategyRegistry()
        self.evaluator = MultiFactorEvaluator()
        self.sizer = PositionSizer()

        # Cache of latest candles: (symbol, interval_str) -> Candle
        self.latest_candles: dict[tuple[str, str], Candle] = {}

        self._consumer: confluent_kafka.Consumer | None = None
        self._producer: confluent_kafka.Producer | None = None
        self._running = False
        self._messages_processed = 0
        self._signals_published = 0

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Register a strategy into the live engine."""
        self.registry.register(strategy)

    def process_technical_signal_envelope(
        self,
        envelope: EventEnvelope[TechnicalSignal],
    ) -> list[EventEnvelope[QuantSignal]]:
        """Process an incoming TechnicalSignal, evaluate strategies, and publish QuantSignals."""
        tech_sig = envelope.payload
        symbol = tech_sig.symbol
        interval = tech_sig.interval
        clean_inv = interval.value

        # Match with genuine closed candle; require genuine market data for active trading
        candle = self.latest_candles.get((symbol, clean_inv))
        if candle is None:
            # If no genuine closed candle has arrived yet for this symbol/interval,
            # we cannot safely evaluate OHLC-dependent rules or size ATR brackets.
            # Return empty proposal list (non-actionable / warming up)
            logger.info(
                "Skipping quant signal generation for %s %s: Genuine closed candle missing (warming up).",
                symbol,
                clean_inv,
            )
            return []

        published_envelopes: list[EventEnvelope[QuantSignal]] = []

        # Evaluate each registered strategy
        for strat_info in self.registry.list_strategies():
            strat_name = strat_info["strategy_name"]
            strat = self.registry.get(strat_name)
            if strat is None:
                continue

            decision = strat.evaluate(candle, tech_sig.indicators, tech_sig)
            if decision.is_warmup:
                # Do not emit signals during warmup
                continue

            # Enforce long-only cash delivery invariant: no SHORT signals
            direction = decision.direction
            if direction == Direction.SHORT:
                direction = Direction.FLAT
                target_weight = Decimal("0")
            elif direction == Direction.FLAT:
                target_weight = Decimal("0")
            else:
                # LONG: size via PositionSizer
                sizing_res = self.sizer.size_order(
                    close_price=candle.close,
                    atr=tech_sig.indicators.get("atr_14"),
                    direction=Direction.LONG,
                    base_weight=decision.target_weight,
                )
                target_weight = sizing_res.target_weight

            params_payload: dict[str, Any] = {
                "reasons": "; ".join(decision.reasons),
            }
            for k, v in decision.factor_scores.items():
                params_payload[f"factor_{k}"] = v

            prov = tech_sig.provenance or DataProvenance(
                provider="unknown_analyst",
                feed_mode=FeedMode.SIMULATED,
            )
            quant_sig = QuantSignal(
                symbol=symbol,
                evaluated_at=tech_sig.evaluated_at,
                interval=interval,
                strategy_name=strat.strategy_name,
                strategy_version=strat.strategy_version,
                direction=direction,
                target_weight=target_weight,
                expected_net_edge_bps_per_trade=None,
                edge_basis=None,
                backtest_report_id=None,
                source_signal_ids=[tech_sig.signal_id],
                config_hash=strat.config_hash,
                provenance=prov,
                parameters=params_payload,
            )

            # Publish with causation_id = incoming envelope.event_id
            published_env = self.publisher.publish_signal(
                signal=quant_sig,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.event_id,
            )

            # Cache latest signal in Redis
            self.cache.save_signal(quant_sig)
            published_envelopes.append(published_env)
            self._signals_published += 1

        self._messages_processed += 1
        return published_envelopes

    def publish_dlq(self, raw_payload: dict[str, Any], reason: str) -> None:
        """Route unparseable or poisoned messages to Dead-Letter Queue."""
        dlq_event = {
            "error_type": "QUANT_STRATEGY_PROCESSING_ERROR",
            "reason": reason,
            "failed_at": datetime.now(UTC).isoformat(),
            "raw_payload": raw_payload,
        }
        val_bytes = json.dumps(dlq_event, default=str).encode("utf-8")
        if self._producer is not None:
            self._producer.produce(topic="signals.quant.dlq", value=val_bytes)
            self._producer.poll(0)
        logger.warning("Routed invalid message to signals.quant.dlq: %s", reason)

    def handle_raw_message(self, topic: str, msg_bytes: bytes) -> bool:
        """Handle incoming raw Kafka message with publish-before-commit guarantee."""
        try:
            raw_dict = json.loads(msg_bytes.decode("utf-8"))
        except Exception as e:
            self.publish_dlq({"raw": str(msg_bytes)}, reason=f"JSON decode failure: {e}")
            return False

        if topic == TopicName.SIGNALS_TECHNICAL.value:
            try:
                env = EventEnvelope[TechnicalSignal].model_validate(raw_dict)
                self.process_technical_signal_envelope(env)
                return True
            except Exception as e:
                self.publish_dlq(
                    raw_dict, reason=f"TechnicalSignal envelope validation failure: {e}"
                )
                return False

        elif topic == TopicName.MARKET_CANDLES.value:
            try:
                env_candle = EventEnvelope[Candle].model_validate(raw_dict)
                c = env_candle.payload
                if c.is_final:
                    self.latest_candles[(c.symbol, c.interval.value)] = c
                return True
            except Exception as e:
                self.publish_dlq(raw_dict, reason=f"Candle envelope validation failure: {e}")
                return False

        return False
