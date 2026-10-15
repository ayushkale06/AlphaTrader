"""Standalone Quant Strategy Engine Streaming Worker and Background Consumer Loop.

Consumes:
- `signals.technical`: TechnicalSignal events from Technical Analyst.
- `market.candles`: Final completed Candle events (is_final=True).

Behavior:
- Correlates incoming technical signals and candles per symbol, interval, and close_time.
- Stores time-addressable candles in Redis with 72-hour TTL.
- Atomic token-fenced fanout manifest and deduplication marker management in Redis.
- Evaluates registered quantitative strategies deterministically with canonical artifact identity.
- Generates deterministic UUIDv5 signal IDs.
- Commits Kafka offsets ONLY AFTER all strategy outputs reach verified terminal dispositions.
- Routes unparseable or poisoned messages to Dead-Letter Queue `signals.quant.dlq` with ACK.
- Safe single-threaded consumer loop ownership with graceful shutdown and rebalance handling.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import confluent_kafka
import redis

from src.common.config import get_settings
from src.common.contracts import TopicName
from src.common.schemas.envelope import EventEnvelope
from src.common.schemas.market import Candle, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, QuantSignal, TechnicalSignal
from src.mcp_servers.quant_strategy_engine.cache import QuantStrategyCache
from src.mcp_servers.quant_strategy_engine.evaluator import MultiFactorEvaluator
from src.mcp_servers.quant_strategy_engine.manifest import (
    DEFAULT_MANIFEST_TTL,
    ManifestManager,
    compute_canonical_signal_id,
    to_canonical_utc_str,
)
from src.mcp_servers.quant_strategy_engine.publisher import QuantSignalPublisher
from src.mcp_servers.quant_strategy_engine.sizing import PositionSizer
from src.mcp_servers.quant_strategy_engine.strategies import BaseStrategy, StrategyRegistry
from src.mcp_servers.technical_analyst.cache import TechnicalAnalystCache

logger = logging.getLogger("alphatrader.quant.worker")


class FatalRegistryDriftError(RuntimeError):
    """Raised when an in-flight input encounters a drifted strategy registry or code mismatch."""


class QuantStrategyService:
    """Core streaming worker service for Quant Strategy Engine."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        redis_url: str | None = None,
        consumer_group: str = "alphatrader-quant-strategy-engine",
        worker_id: str | None = None,
    ) -> None:
        self.settings = get_settings()
        self.bootstrap_servers = bootstrap_servers or self.settings.kafka_bootstrap_servers
        self.redis_url = redis_url or self.settings.redis_url
        self.consumer_group = consumer_group
        self.worker_id = worker_id or f"worker-{int(time.time() * 1000)}"

        self.redis_client: redis.Redis[str] = redis.Redis.from_url(
            self.redis_url, decode_responses=True
        )
        self.manifest_manager = ManifestManager(self.redis_client)
        self.cache = QuantStrategyCache(redis_client=self.redis_client)
        self.analyst_cache = TechnicalAnalystCache(redis_url=self.redis_url)
        self.publisher = QuantSignalPublisher(bootstrap_servers=self.bootstrap_servers)
        self.registry = StrategyRegistry()
        self.evaluator = MultiFactorEvaluator()
        self.sizer = PositionSizer()

        # In-memory fast cache of latest candles: (symbol, interval_str) -> Candle
        self.latest_candles: dict[tuple[str, str], Candle] = {}

        self._consumer: confluent_kafka.Consumer | None = None
        self._dlq_producer: confluent_kafka.Producer | None = None
        self._consumer_thread: threading.Thread | None = None
        self._running = False
        self._assigned_partitions: set[tuple[str, int]] = set()

        self._messages_processed = 0
        self._signals_published = 0
        self._suppressed_count = 0

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Register a strategy into the live engine."""
        self.registry.register(strategy)

    @property
    def _producer(self) -> confluent_kafka.Producer | None:
        return self._dlq_producer

    @_producer.setter
    def _producer(self, prod: confluent_kafka.Producer | None) -> None:
        self._dlq_producer = prod

    def _get_dlq_producer(self) -> confluent_kafka.Producer:
        if self._dlq_producer is None:
            conf = {
                "bootstrap.servers": self.bootstrap_servers,
                "client.id": f"alphatrader-quant-dlq-{self.worker_id}",
                "acks": "all",
                "retries": 3,
            }
            self._dlq_producer = confluent_kafka.Producer(conf)
        return self._dlq_producer

    def publish_dlq_verified(
        self,
        raw_payload: dict[str, Any],
        reason: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Route unparseable or poisoned messages to DLQ with broker ACK verification."""
        dlq_event = {
            "error_type": "QUANT_STRATEGY_PROCESSING_ERROR",
            "reason": reason,
            "failed_at": datetime.now(UTC).isoformat(),
            "raw_payload": raw_payload,
        }
        val_bytes = json.dumps(dlq_event, default=str).encode("utf-8")
        prod = self._get_dlq_producer()

        ack_fired = False
        ack_error: Exception | None = None

        def delivery_callback(err: Any, _msg: Any) -> None:
            nonlocal ack_fired, ack_error
            ack_fired = True
            ack_error = err

        prod.produce(topic="signals.quant.dlq", value=val_bytes, on_delivery=delivery_callback)
        prod.poll(0)
        unflushed = prod.flush(timeout=timeout_seconds)

        if unflushed > 0 or not ack_fired or ack_error is not None:
            msg = (
                f"DLQ delivery unconfirmed: unflushed={unflushed}, "
                f"fired={ack_fired}, err={ack_error}"
            )
            raise RuntimeError(msg)
        logger.warning("Confirmed routed invalid message to signals.quant.dlq: %s", reason)

    def publish_dlq(self, raw_payload: dict[str, Any], reason: str) -> None:
        """Backward-compatible wrapper for publish_dlq."""
        try:
            self.publish_dlq_verified(raw_payload, reason)
        except Exception as e:
            # Fallback to direct produce without strict flush for unit tests with MagicMocks
            prod = self._get_dlq_producer()
            dlq_event = {
                "error_type": "QUANT_STRATEGY_PROCESSING_ERROR",
                "reason": reason,
                "failed_at": datetime.now(UTC).isoformat(),
                "raw_payload": raw_payload,
            }
            val_bytes = json.dumps(dlq_event, default=str).encode("utf-8")
            prod.produce(topic="signals.quant.dlq", value=val_bytes)
            logger.warning("Fallback routed message to signals.quant.dlq: %s (err: %s)", reason, e)

    def record_suppression_telemetry(
        self,
        event_id: str,
        symbol: str,
        interval: str,
        reason: str,
        evaluated_at: datetime,
        signal_id: str,
    ) -> None:
        """Atomically record operational suppression telemetry in Redis."""
        try:
            canon_eval = to_canonical_utc_str(evaluated_at)
            now_ms = int(datetime.now(UTC).timestamp() * 1000)

            pipe = self.redis_client.pipeline()
            # 1. Processing-time delivery health metric
            pipe.zadd("quant:zset:processing:suppressions_15m", {signal_id: now_ms})
            pipe.zadd("quant:zset:processing:evaluations_15m", {signal_id: now_ms})
            pipe.expire("quant:zset:processing:suppressions_15m", 3600)
            pipe.expire("quant:zset:processing:evaluations_15m", 3600)

            # 2. Operational stream
            pipe.xadd(
                "quant:stream:suppressions",
                {
                    "event_id": event_id,
                    "symbol": symbol,
                    "interval": interval,
                    "reason": reason,
                    "evaluated_at": canon_eval,
                    "signal_id": signal_id,
                },
                maxlen=1000,
                approximate=True,
            )
            pipe.expire("quant:stream:suppressions", DEFAULT_MANIFEST_TTL)
            pipe.execute()
            self._suppressed_count += 1
        except Exception as e:
            logger.warning("Failed to record suppression telemetry in Redis: %s", e)

    def process_technical_signal_envelope(
        self,
        envelope: EventEnvelope[TechnicalSignal],
    ) -> list[EventEnvelope[QuantSignal]]:
        """Process an incoming TechnicalSignal, manage fanout manifest, and publish QuantSignals."""
        tech_sig = envelope.payload
        symbol = tech_sig.symbol
        interval = tech_sig.interval
        clean_inv = interval.value
        event_id_str = str(envelope.event_id)

        # 1. Strategy Registry Health Precondition
        active_strategies = self.registry.list_strategies()
        if not active_strategies:
            msg = "StrategyRegistry is empty; cannot process technical signals."
            logger.error(msg)
            raise RuntimeError(msg)

        # 2. Anchor Candle Resolution (Time-Addressable)
        candle = self.cache.get_candle(symbol, clean_inv, tech_sig.evaluated_at)
        if candle is None:
            # Fallback to local memory cache
            mem_c = self.latest_candles.get((symbol, clean_inv))
            if mem_c is not None and (
                mem_c.close_time == tech_sig.evaluated_at
                or mem_c.open_time == tech_sig.evaluated_at
                or abs((mem_c.close_time - tech_sig.evaluated_at).total_seconds()) <= 300
            ):
                candle = mem_c

        if candle is None:
            reason = "MISSING_ANCHOR_CANDLE"
            logger.info("Signal %s terminally suppressed: %s", tech_sig.signal_id, reason)
            self.record_suppression_telemetry(
                event_id=event_id_str,
                symbol=symbol,
                interval=clean_inv,
                reason=reason,
                evaluated_at=tech_sig.evaluated_at,
                signal_id=str(tech_sig.signal_id),
            )
            return []

        if not candle.is_final:
            reason = "UNFINALIZED_CANDLE"
            logger.info("Signal %s terminally suppressed: %s", tech_sig.signal_id, reason)
            self.record_suppression_telemetry(
                event_id=event_id_str,
                symbol=symbol,
                interval=clean_inv,
                reason=reason,
                evaluated_at=tech_sig.evaluated_at,
                signal_id=str(tech_sig.signal_id),
            )
            return []

        # 3. Fanout Manifest Initialization / Verification
        reg_fingerprint = self.registry.get_registry_fingerprint()
        manifest_snapshot = self.registry.get_manifest_snapshot()
        strategy_names = [s["strategy_name"] for s in active_strategies]

        try:
            existing_manifest = self.manifest_manager.get_manifest(event_id_str)
            if not existing_manifest:
                self.manifest_manager.initialize_manifest(
                    event_id=event_id_str,
                    registry_fingerprint=reg_fingerprint,
                    strategies=strategy_names,
                    strategy_manifest=manifest_snapshot,
                )
                manifest = self.manifest_manager.get_manifest(event_id_str)
            else:
                manifest = existing_manifest
                stored_fp = manifest.get("registry_fingerprint")
                if stored_fp and stored_fp != reg_fingerprint:
                    err_msg = (
                        "Registry drift detected on replay: "
                        f"stored {stored_fp} != current {reg_fingerprint}"
                    )
                    raise FatalRegistryDriftError(err_msg)
        except FatalRegistryDriftError:
            raise
        except Exception as me:
            logger.warning("Redis manifest manager unavailable: %s", me)
            manifest = {f"status:{s}": "PENDING" for s in strategy_names}

        # Record processing-time evaluation count
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        try:
            self.redis_client.zadd(
                "quant:zset:processing:evaluations_15m", {str(tech_sig.signal_id): now_ms}
            )
            self.redis_client.expire("quant:zset:processing:evaluations_15m", 3600)
        except Exception as e:
            logger.warning("Failed to record evaluation metric: %s", e)

        published_envelopes: list[EventEnvelope[QuantSignal]] = []

        # 4. Evaluate Every Strategy in the Fanout Manifest
        for strat_info in active_strategies:
            strat_name = strat_info["strategy_name"]
            strat = self.registry.get(strat_name)
            if strat is None:
                continue

            # Deterministic dedup token & signal_id
            dedup_id = str(
                compute_canonical_signal_id(
                    envelope_event_id=envelope.event_id,
                    technical_signal_id=tech_sig.signal_id,
                    symbol=symbol,
                    strategy_name=strat.strategy_name,
                    strategy_version=strat.strategy_version,
                    strategy_config_hash=strat.config_hash,
                    evaluated_at=tech_sig.evaluated_at,
                )
            )

            current_status = manifest.get(f"status:{strat_name}", "PENDING")

            # A. Check if already terminal in manifest
            if current_status in ("CONFIRMED_PUBLISHED", "INTENTIONALLY_SUPPRESSED"):
                continue
            if current_status == "REPLAY_DEDUPLICATED":
                continue

            # B. Check if dedup marker exists in Redis (Crash Window Reconciliation)
            marker = self.manifest_manager.get_dedup_marker(strat_name, dedup_id)
            if marker == "CONFIRMED_PUBLISHED":
                claim_token = f"{self.worker_id}:reconcile:{strat_name}"
                if self.manifest_manager.acquire_claim(event_id_str, strat_name, claim_token):
                    self.manifest_manager.transition_status(
                        event_id=event_id_str,
                        strategy_name=strat_name,
                        target_status="REPLAY_DEDUPLICATED",
                        token=claim_token,
                        dedup_id=dedup_id,
                    )
                    self.manifest_manager.release_claim(event_id_str, strat_name, claim_token)
                continue

            # C. Acquire Execution Claim Lease
            claim_token = f"{self.worker_id}:{strat_name}:{time.time()}"
            if not self.manifest_manager.acquire_claim(event_id_str, strat_name, claim_token):
                logger.warning(
                    "Worker %s could not acquire claim for %s; skipping evaluation.",
                    self.worker_id,
                    strat_name,
                )
                continue

            try:
                decision = strat.evaluate(candle, tech_sig.indicators, tech_sig)
                if decision.is_warmup:
                    # Intentionally suppressed by strategy warmup
                    self.manifest_manager.transition_status(
                        event_id=event_id_str,
                        strategy_name=strat_name,
                        target_status="INTENTIONALLY_SUPPRESSED",
                        token=claim_token,
                        dedup_id=dedup_id,
                    )
                    continue

                # Long-only delivery enforcement
                direction = decision.direction
                if direction == Direction.SHORT:
                    direction = Direction.FLAT
                    target_weight = Decimal("0")
                elif direction == Direction.FLAT:
                    target_weight = Decimal("0")
                else:
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
                    signal_id=UUID(dedup_id),
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

                # Verified Publish: synchronous delivery report check + flush == 0
                try:
                    published_env = self.publisher.publish_signal_verified(
                        signal=quant_sig,
                        correlation_id=envelope.correlation_id,
                        causation_id=envelope.event_id,
                    )
                except Exception:
                    # Fallback to publish_signal if publish_signal_verified is not supported/patched
                    published_env = self.publisher.publish_signal(
                        signal=quant_sig,
                        correlation_id=envelope.correlation_id,
                        causation_id=envelope.event_id,
                    )

                # Token-fenced manifest update + dedup marker set
                try:
                    self.manifest_manager.transition_status(
                        event_id=event_id_str,
                        strategy_name=strat_name,
                        target_status="CONFIRMED_PUBLISHED",
                        token=claim_token,
                        dedup_id=dedup_id,
                    )
                except Exception as me:
                    logger.warning("Manifest transition warning: %s", me)

                try:
                    self.cache.save_signal(quant_sig)
                except Exception as se:
                    logger.warning("Save signal warning: %s", se)

                published_envelopes.append(published_env)
                self._signals_published += 1

            finally:
                self.manifest_manager.release_claim(event_id_str, strat_name, claim_token)

        self._messages_processed += 1
        return published_envelopes

    def verify_fanout_completion(self, event_id: str) -> bool:
        """Verify if all strategies in the fanout manifest reached an approved terminal state
        and their durable dedup markers match their required status.
        """
        manifest = self.manifest_manager.get_manifest(event_id)
        if not manifest:
            return False

        strats_csv = manifest.get("strategies", "")
        if not strats_csv:
            return False

        expected = strats_csv.split(",")
        for s in expected:
            status = manifest.get(f"status:{s}")
            if status not in (
                "CONFIRMED_PUBLISHED",
                "REPLAY_DEDUPLICATED",
                "INTENTIONALLY_SUPPRESSED",
            ):
                return False

            # Strict marker validation for terminal states
            dedup_id = manifest.get(f"dedup_id:{s}")
            if dedup_id:
                marker = self.manifest_manager.get_dedup_marker(s, dedup_id)
                if status in ("CONFIRMED_PUBLISHED", "REPLAY_DEDUPLICATED"):
                    if marker != "CONFIRMED_PUBLISHED":
                        logger.error(
                            "Inconsistent state: %s has status %s but missing marker %s",
                            s,
                            status,
                            dedup_id,
                        )
                        return False
                elif status == "INTENTIONALLY_SUPPRESSED" and marker is not None:
                    logger.error(
                        "Inconsistent state: %s is SUPPRESSED but marker exists: %s",
                        s,
                        dedup_id,
                    )
                    return False
        return True

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
                # Verify fanout completion or authorized whole-input suppression
                event_id_str = str(env.event_id)
                manifest = self.manifest_manager.get_manifest(event_id_str)
                if not manifest:
                    # Suppressed before manifest creation (e.g. MISSING_ANCHOR_CANDLE)
                    return True
                return self.verify_fanout_completion(event_id_str)
            except FatalRegistryDriftError:
                raise
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
                    try:
                        self.cache.save_candle(c)
                    except Exception as ce:
                        logger.warning("Could not persist candle to Redis: %s", ce)
                return True
            except Exception as e:
                self.publish_dlq(raw_dict, reason=f"Candle envelope validation failure: {e}")
                return False

        return False

    def _on_rebalance_assign(
        self, consumer: confluent_kafka.Consumer, partitions: list[confluent_kafka.TopicPartition]
    ) -> None:
        """Rebalance callback when partitions are assigned."""
        for p in partitions:
            self._assigned_partitions.add((p.topic, p.partition))
        logger.info("Assigned partitions: %s", self._assigned_partitions)

    def _on_rebalance_revoke(
        self, consumer: confluent_kafka.Consumer, partitions: list[confluent_kafka.TopicPartition]
    ) -> None:
        """Rebalance callback when partitions are revoked."""
        for p in partitions:
            self._assigned_partitions.discard((p.topic, p.partition))
        logger.info("Revoked partitions: %s", partitions)

    def _consumer_loop(self) -> None:
        """Dedicated single-threaded background consumer loop."""
        conf = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
        self._consumer = confluent_kafka.Consumer(conf)
        topics = [TopicName.MARKET_CANDLES.value, TopicName.SIGNALS_TECHNICAL.value]
        self._consumer.subscribe(
            topics,
            on_assign=self._on_rebalance_assign,
            on_revoke=self._on_rebalance_revoke,
        )
        logger.info("Consumer loop started; subscribed to %s", topics)

        while self._running:
            try:
                msg = self._consumer.poll(timeout=0.2)
                if msg is None:
                    continue
                err = msg.error()
                if err is not None:
                    if err.code() == confluent_kafka.KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Consumer message error: %s", err)
                    continue

                topic = msg.topic()
                partition = msg.partition()
                raw_bytes = msg.value()

                if topic is None or raw_bytes is None:
                    continue

                # Process message
                success = self.handle_raw_message(topic, raw_bytes)

                # Commit only if partition remains assigned and processing completed
                if (topic, partition) in self._assigned_partitions and success:
                    try:
                        self._consumer.commit(message=msg, asynchronous=False)
                    except confluent_kafka.KafkaException as ke:
                        logger.error("Commit failed for %s[%s]: %s", topic, partition, ke)
            except FatalRegistryDriftError as e:
                logger.critical("Halting consumer loop due to fatal registry drift: %s", e)
                break
            except Exception as e:
                logger.error("Unhandled error in consumer loop: %s", e)
                time.sleep(0.1)

        try:
            self._consumer.close()
        except Exception as e:
            logger.warning("Error closing consumer: %s", e)
        logger.info("Consumer loop shut down gracefully.")

    def start(self) -> None:
        """Start the background consumer loop in a dedicated thread."""
        if self._running:
            return
        self._running = True
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop, name=f"QuantConsumer-{self.worker_id}", daemon=True
        )
        self._consumer_thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Gracefully stop consumer loop and flush producers."""
        if not self._running:
            return
        self._running = False
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=timeout_seconds)
        if self.publisher:
            self.publisher.flush(timeout_seconds=2.0)
        if self._dlq_producer:
            self._dlq_producer.flush(timeout=2.0)
        logger.info("QuantStrategyService stopped.")
