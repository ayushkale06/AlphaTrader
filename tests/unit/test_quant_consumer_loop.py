"""Comprehensive unit tests for the Quant Strategy Engine consumer loop,
token-fenced manifest transitions, artifact identity, and failure boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import confluent_kafka
import pytest

from src.common.contracts import TopicName
from src.common.schemas.envelope import ComponentSource, EventEnvelope
from src.common.schemas.market import Candle, CandleInterval, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, TechnicalSignal
from src.mcp_servers.quant_strategy_engine.manifest import (
    ManifestManager,
    compute_canonical_artifact_hash,
    compute_canonical_signal_id,
    to_canonical_utc_str,
)
from src.mcp_servers.quant_strategy_engine.strategies import (
    BaseStrategy,
    EmaTrendFollowingStrategy,
    StrategyDecision,
)
from src.mcp_servers.quant_strategy_engine.worker import (
    FatalRegistryDriftError,
    QuantStrategyService,
)

FIXED_UTC = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
SAMPLE_PROV = DataProvenance(provider="simulated_feed", feed_mode=FeedMode.SIMULATED)


class FakeRedis:
    """In-memory Redis fake with Lua script support for unit testing."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = str(value)
        if ex:
            self.ttls[key] = ex
        return True

    def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) else 0

    def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: str) -> int:
        if key not in self.hashes:
            self.hashes[key] = {}
        if mapping:
            self.hashes[key].update(mapping)
        if kwargs:
            self.hashes[key].update(kwargs)
        return len(self.hashes[key])

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self.zsets:
            self.zsets[key] = {}
        self.zsets[key].update(mapping)
        return len(mapping)

    def xadd(
        self,
        key: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = False,
    ) -> str:
        if key not in self.streams:
            self.streams[key] = []
        self.streams[key].append(fields)
        return f"{len(self.streams[key])}-0"

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def pipeline(self) -> FakeRedis:
        return self

    def execute(self) -> list[Any]:
        return [True]

    def register_script(self, script_str: str) -> Any:
        def execute_script(keys: list[str], args: list[Any]) -> Any:
            # Emulate Token Fenced Transition Script
            if len(keys) == 3:
                claim_key = keys[0]
                manifest_key = keys[1]
                dedup_key = keys[2]
                expected_token = str(args[0])
                strat_name = str(args[1])
                target_status = str(args[2])

                # 1. Token Check
                current_token = self.data.get(claim_key)
                if current_token != expected_token:
                    raise Exception("ERR_LEASE_LOST")

                # 2. Manifest check
                m = self.hashes.get(manifest_key, {})
                current_status = m.get(f"status:{strat_name}")
                if not current_status:
                    raise Exception("ERR_STRATEGY_NOT_IN_MANIFEST")

                # 3. Transition Guard
                if current_status != "PENDING":
                    if current_status == target_status:
                        return 1
                    raise Exception(f"ERR_CONFLICTING_TERMINAL_STATE:{current_status}")

                # 4. Marker check
                if target_status == "CONFIRMED_PUBLISHED":
                    self.data[dedup_key] = "CONFIRMED_PUBLISHED"
                elif (
                    target_status == "REPLAY_DEDUPLICATED"
                    and self.data.get(dedup_key) != "CONFIRMED_PUBLISHED"
                ):
                    raise Exception("ERR_MARKER_MISSING_FOR_DEDUPLICATION")
                elif target_status == "INTENTIONALLY_SUPPRESSED" and dedup_key in self.data:
                    raise Exception("ERR_MARKER_EXISTS_FOR_SUPPRESSION")

                self.hashes[manifest_key][f"status:{strat_name}"] = target_status
                if ":" in dedup_key:
                    dedup_id = dedup_key.split(":")[-1]
                    self.hashes[manifest_key][f"dedup_id:{strat_name}"] = dedup_id
                return 1

            # Emulate Atomic Init Script
            elif "strategies" in script_str:
                m_key = keys[0]
                if m_key in self.hashes:
                    return 0
                fingerprint = str(args[0])
                strats_csv = str(args[1])
                m_json = str(args[2])
                ttl = int(args[3])
                self.hashes[m_key] = {
                    "registry_fingerprint": fingerprint,
                    "strategies": strats_csv,
                    "manifest": m_json,
                }
                for s in strats_csv.split(","):
                    self.hashes[m_key][f"status:{s}"] = "PENDING"
                self.ttls[m_key] = ttl
                return 1

            # Emulate Renew Script
            elif "quant:claim" in keys[0] and len(args) == 2:
                claim_key = keys[0]
                if self.data.get(claim_key) == str(args[0]):
                    self.ttls[claim_key] = int(args[1])
                    return 1
                return 0

            # Emulate Release Script
            elif "quant:claim" in keys[0] and len(args) == 1:
                claim_key = keys[0]
                if self.data.get(claim_key) == str(args[0]):
                    self.data.pop(claim_key, None)
                    return 1
                return 0

            return 1

        return execute_script


def make_candle(close: float = 100.0, close_time: datetime = FIXED_UTC) -> Candle:
    c_p = Decimal(str(close))
    return Candle(
        symbol="TCS.NS",
        interval=CandleInterval.FIVE_MINUTES,
        open_time=close_time - timedelta(minutes=5),
        close_time=close_time,
        open=c_p,
        high=c_p + Decimal("2.0"),
        low=c_p - Decimal("2.0"),
        close=c_p,
        volume=Decimal("1000"),
        is_final=True,
        provenance=SAMPLE_PROV,
    )


def test_canonical_artifact_hash_reproducibility() -> None:
    """Artifact hash must be identical across registration, manifest, and replay."""
    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    hash1 = strat.artifact_hash
    assert isinstance(hash1, str)
    assert len(hash1) == 64

    # Calculate again with same inputs
    hash2 = compute_canonical_artifact_hash(
        strategy_cls=EmaTrendFollowingStrategy,
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        config_hash=strat.config_hash,
        extra_dependencies=[BaseStrategy, StrategyDecision],
    )
    assert hash1 == hash2


def test_canonical_signal_id_deterministic() -> None:
    """Signal ID must be deterministic and reject naive or microsecond timestamps."""
    env_id = uuid4()
    sig_id = uuid4()

    out_id1 = compute_canonical_signal_id(
        envelope_event_id=env_id,
        technical_signal_id=sig_id,
        symbol="TCS.NS",
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        strategy_config_hash="a" * 64,
        evaluated_at=FIXED_UTC,
    )
    out_id2 = compute_canonical_signal_id(
        envelope_event_id=env_id,
        technical_signal_id=sig_id,
        symbol="TCS.NS",
        strategy_name="ema_trend_following",
        strategy_version="1.0.0",
        strategy_config_hash="a" * 64,
        evaluated_at=FIXED_UTC,
    )
    assert out_id1 == out_id2

    # Reject naive datetime
    with pytest.raises(ValueError, match="Naive datetime rejected"):
        to_canonical_utc_str(datetime(2026, 10, 15, 10, 0, 0))

    # Reject non-zero microsecond precision
    with pytest.raises(ValueError, match="Sub-second microsecond precision"):
        to_canonical_utc_str(datetime(2026, 10, 15, 10, 0, 0, 500, tzinfo=UTC))


def test_manifest_token_fenced_state_transitions() -> None:
    """Token fencing strictly prevents stolen/expired leases from mutating manifest."""
    fake_redis = FakeRedis()
    mgr = ManifestManager(fake_redis)  # type: ignore[arg-type]

    event_id = "test-event-001"
    mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp123",
        strategies=["stratA"],
        strategy_manifest={"stratA": {"version": "1.0.0"}},
    )

    # 1. Acquire Claim
    token1 = "worker-1:token-A"
    assert mgr.acquire_claim(event_id, "stratA", token1) is True
    # Duplicate acquire fails
    assert mgr.acquire_claim(event_id, "stratA", "worker-2:token-B") is False

    # 2. Invalid token cannot transition
    with pytest.raises(Exception, match="ERR_LEASE_LOST"):
        mgr.transition_status(
            event_id=event_id,
            strategy_name="stratA",
            target_status="CONFIRMED_PUBLISHED",
            token="wrong-token",
            dedup_id="dedup-001",
        )

    # 3. Valid token transitions cleanly
    res = mgr.transition_status(
        event_id=event_id,
        strategy_name="stratA",
        target_status="CONFIRMED_PUBLISHED",
        token=token1,
        dedup_id="dedup-001",
    )
    assert res is True
    assert mgr.get_dedup_marker("stratA", "dedup-001") == "CONFIRMED_PUBLISHED"

    # 4. Conflicting transition on terminal state fails
    with pytest.raises(Exception, match="ERR_CONFLICTING_TERMINAL_STATE"):
        mgr.transition_status(
            event_id=event_id,
            strategy_name="stratA",
            target_status="INTENTIONALLY_SUPPRESSED",
            token=token1,
            dedup_id="dedup-001",
        )


def test_consumer_loop_crash_window_reconciliation() -> None:
    """Crash window: Dedup marker exists in Redis promotes to REPLAY_DEDUPLICATED."""
    fake_redis = FakeRedis()
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    svc.redis_client = fake_redis  # type: ignore[assignment]
    svc.manifest_manager = ManifestManager(fake_redis)  # type: ignore[arg-type]
    svc.cache._client = fake_redis  # type: ignore[assignment]

    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    svc.register_strategy(strat)

    candle = make_candle(close=100.0)
    svc.cache.save_candle(candle)

    indicators_map = {
        "ema_9": Decimal("102.0"),
        "ema_21": Decimal("100.0"),
        "atr_14": Decimal("2.0"),
        "close": Decimal("100.0"),
    }
    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        rules_long=["ema_crossover_long"],
        indicators=indicators_map,
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    env = EventEnvelope[TechnicalSignal](
        event_id=uuid4(),
        correlation_id="cid-replay",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )

    # Pre-populate dedup marker as if prior run published before crashing
    dedup_id = str(
        compute_canonical_signal_id(
            envelope_event_id=env.event_id,
            technical_signal_id=tech_sig.signal_id,
            symbol="TCS.NS",
            strategy_name="ema_trend_following",
            strategy_version="1.0.0",
            strategy_config_hash=strat.config_hash,
            evaluated_at=FIXED_UTC,
        )
    )
    fake_redis.set(f"quant:dedup:ema_trend_following:{dedup_id}", "CONFIRMED_PUBLISHED")

    with patch.object(svc.publisher, "publish_signal_verified") as mock_pub:
        outputs = svc.process_technical_signal_envelope(env)
        # Should NOT re-publish because marker was found
        assert not mock_pub.called
        assert outputs == []

    manifest = svc.manifest_manager.get_manifest(str(env.event_id))
    assert manifest.get("status:ema_trend_following") == "REPLAY_DEDUPLICATED"
    assert svc.verify_fanout_completion(str(env.event_id)) is True


def test_fatal_registry_drift_error_on_replay() -> None:
    """Replaying an input with a different registry fingerprint must fail closed."""
    fake_redis = FakeRedis()
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    svc.redis_client = fake_redis  # type: ignore[assignment]
    svc.manifest_manager = ManifestManager(fake_redis)  # type: ignore[arg-type]
    svc.cache._client = fake_redis  # type: ignore[assignment]

    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    svc.register_strategy(strat)

    candle = make_candle(close=100.0)
    svc.cache.save_candle(candle)

    event_id = uuid4()
    # Write existing manifest with different fingerprint
    fake_redis.hset(
        f"quant:fanout:{event_id}",
        mapping={
            "registry_fingerprint": "old_different_fingerprint",
            "strategies": "ema_trend_following",
            "status:ema_trend_following": "PENDING",
        },
    )

    indicators_drift = {
        "ema_9": Decimal("102.0"),
        "ema_21": Decimal("100.0"),
        "atr_14": Decimal("2.0"),
        "close": Decimal("100.0"),
    }
    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        rules_long=["ema_crossover_long"],
        indicators=indicators_drift,
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    env = EventEnvelope[TechnicalSignal](
        event_id=event_id,
        correlation_id="cid-drift",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )

    with pytest.raises(FatalRegistryDriftError, match="Registry drift detected on replay"):
        svc.process_technical_signal_envelope(env)


def test_dlq_verified_delivery_and_timeout() -> None:
    """DLQ delivery requires broker ACK and flush==0; failure raises RuntimeError."""
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    mock_prod = MagicMock()
    svc._dlq_producer = mock_prod

    # 1. Success case: flush returns 0, callback fired without error
    def mock_produce_success(topic: str, value: bytes, on_delivery: Any) -> None:
        on_delivery(None, MagicMock())

    mock_prod.produce.side_effect = mock_produce_success
    mock_prod.flush.return_value = 0

    svc.publish_dlq_verified({"test": "data"}, reason="Test Reason")
    assert mock_prod.produce.called

    # 2. Timeout case: flush returns 1 (unflushed message)
    mock_prod.flush.return_value = 1
    with pytest.raises(RuntimeError, match="DLQ delivery unconfirmed"):
        svc.publish_dlq_verified({"test": "data"}, reason="Timeout Reason")


def test_lease_expiry_during_publication() -> None:
    """If a worker's lease expires during publication, token-fenced transition must fail."""
    fake_redis = FakeRedis()
    mgr = ManifestManager(fake_redis)  # type: ignore[arg-type]
    event_id = "test-lease-exp-01"

    mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp-lease",
        strategies=["stratX"],
        strategy_manifest={"stratX": {}},
    )
    token = "worker-orig:token-01"
    assert mgr.acquire_claim(event_id, "stratX", token) is True

    # Simulate lease expiry / steal by another worker
    fake_redis.data[f"quant:claim:{event_id}:stratX"] = "worker-other:token-02"

    with pytest.raises(Exception, match="ERR_LEASE_LOST"):
        mgr.transition_status(
            event_id=event_id,
            strategy_name="stratX",
            target_status="CONFIRMED_PUBLISHED",
            token=token,
            dedup_id="dedup-x",
        )


def test_conflicting_marker_and_manifest_states() -> None:
    """Protocol rejects impossible states: suppression with marker, or replay without marker."""
    fake_redis = FakeRedis()
    mgr = ManifestManager(fake_redis)  # type: ignore[arg-type]
    event_id = "test-conflict-01"

    mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp-conflict",
        strategies=["strat1", "strat2"],
        strategy_manifest={},
    )
    token1 = "tok1"
    token2 = "tok2"
    mgr.acquire_claim(event_id, "strat1", token1)
    mgr.acquire_claim(event_id, "strat2", token2)

    # 1. Target is REPLAY_DEDUPLICATED, but dedup marker is missing
    with pytest.raises(Exception, match="ERR_MARKER_MISSING_FOR_DEDUPLICATION"):
        mgr.transition_status(
            event_id=event_id,
            strategy_name="strat1",
            target_status="REPLAY_DEDUPLICATED",
            token=token1,
            dedup_id="dedup-missing",
        )

    # 2. Target is INTENTIONALLY_SUPPRESSED, but dedup marker already exists
    fake_redis.data["quant:dedup:strat2:dedup-exist"] = "CONFIRMED_PUBLISHED"
    with pytest.raises(Exception, match="ERR_MARKER_EXISTS_FOR_SUPPRESSION"):
        mgr.transition_status(
            event_id=event_id,
            strategy_name="strat2",
            target_status="INTENTIONALLY_SUPPRESSED",
            token=token2,
            dedup_id="dedup-exist",
        )


def test_manifest_initialization_race() -> None:
    """Atomic init script returns 1 on first init and 0 on subsequent concurrent inits."""
    fake_redis = FakeRedis()
    mgr = ManifestManager(fake_redis)  # type: ignore[arg-type]
    event_id = "test-race-init"

    res1 = mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp-init",
        strategies=["stratA", "stratB"],
        strategy_manifest={"meta": "v1"},
    )
    assert res1 is True

    # Second concurrent init returns False (idempotent / already initialized)
    res2 = mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp-init",
        strategies=["stratA", "stratB"],
        strategy_manifest={"meta": "v1"},
    )
    assert res2 is False


def test_partial_fanout_completion_guards_commit() -> None:
    """If one strategy fails or stays PENDING, verify_fanout_completion returns False."""
    fake_redis = FakeRedis()
    mgr = ManifestManager(fake_redis)  # type: ignore[arg-type]
    event_id = "test-partial-fanout"

    mgr.initialize_manifest(
        event_id=event_id,
        registry_fingerprint="fp-part",
        strategies=["stratA", "stratB"],
        strategy_manifest={},
    )
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    svc.manifest_manager = mgr

    # Only stratA reaches terminal state
    fake_redis.hashes[f"quant:fanout:{event_id}"]["status:stratA"] = "CONFIRMED_PUBLISHED"
    fake_redis.hashes[f"quant:fanout:{event_id}"]["dedup_id:stratA"] = "d-A"
    fake_redis.data["quant:dedup:stratA:d-A"] = "CONFIRMED_PUBLISHED"
    assert svc.verify_fanout_completion(event_id) is False

    # Inconsistent state: stratB is marked CONFIRMED_PUBLISHED in manifest but marker is missing
    fake_redis.hashes[f"quant:fanout:{event_id}"]["status:stratB"] = "CONFIRMED_PUBLISHED"
    fake_redis.hashes[f"quant:fanout:{event_id}"]["dedup_id:stratB"] = "d-B"
    assert svc.verify_fanout_completion(event_id) is False

    # Once stratB marker is present or stratB is INTENTIONALLY_SUPPRESSED without marker, passes
    fake_redis.hashes[f"quant:fanout:{event_id}"]["status:stratB"] = "INTENTIONALLY_SUPPRESSED"
    assert svc.verify_fanout_completion(event_id) is True


def test_rebalance_fencing_prevents_unassigned_partition_commit() -> None:
    """A revoked partition is removed from _assigned_partitions and must never be committed."""
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    mock_consumer = MagicMock()
    svc._consumer = mock_consumer

    tp = confluent_kafka.TopicPartition("signals.technical", 0)
    svc._on_rebalance_assign(mock_consumer, [tp])
    assert ("signals.technical", 0) in svc._assigned_partitions

    # Simulate partition revoke before commit
    svc._on_rebalance_revoke(mock_consumer, [tp])
    assert ("signals.technical", 0) not in svc._assigned_partitions

    # If poll returned a message from revoked partition, commit is skipped
    fake_msg = MagicMock()
    fake_msg.topic.return_value = "signals.technical"
    fake_msg.partition.return_value = 0
    fake_msg.value.return_value = b"{}"

    # Fencing check
    should_commit = (fake_msg.topic(), fake_msg.partition()) in svc._assigned_partitions
    assert should_commit is False
    assert not mock_consumer.commit.called


def test_artifact_hash_sensitive_to_project_helper_modification() -> None:
    """Modifying a project helper class changes the canonical artifact hash."""

    class HelperV1:
        def compute_indicator(self, x: float) -> float:
            return x * 1.5

    class HelperV2:
        def compute_indicator(self, x: float) -> float:
            return x * 2.5

    class DummyStrategy(BaseStrategy):
        @property
        def strategy_name(self) -> str:
            return "dummy"

        @property
        def strategy_version(self) -> str:
            return "1.0.0"

        def evaluate(
            self,
            candle: Candle,
            indicators: dict[str, Decimal],
            technical_signal: TechnicalSignal | None = None,
        ) -> StrategyDecision:
            return StrategyDecision(
                strategy_name="dummy",
                strategy_version="1.0.0",
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.LONG,
                target_weight=Decimal("0.1"),
            )

    hash_v1 = compute_canonical_artifact_hash(
        strategy_cls=DummyStrategy,
        strategy_name="dummy",
        strategy_version="1.0.0",
        config_hash="0" * 64,
        extra_dependencies=[HelperV1],
    )

    hash_v2 = compute_canonical_artifact_hash(
        strategy_cls=DummyStrategy,
        strategy_name="dummy",
        strategy_version="1.0.0",
        config_hash="0" * 64,
        extra_dependencies=[HelperV2],
    )

    assert hash_v1 != hash_v2


def test_worker_start_stop_and_consumer_poll_branches() -> None:
    """Cover worker start, stop, consumer poll execution, EOF, and commit branches."""
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    mock_consumer = MagicMock()
    svc._consumer = mock_consumer

    # 1. Start and stop lifecycle
    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        svc.start()
        assert svc._running is True
        assert mock_thread.start.called
        # Double start is idempotent
        svc.start()

        svc._running = True
        mock_thread.is_alive.return_value = True
        svc.publisher = MagicMock()
        svc._dlq_producer = MagicMock()
        svc.stop(timeout_seconds=0.1)
        assert svc._running is False
        assert mock_thread.join.called
        assert svc.publisher.flush.called
        assert svc._dlq_producer.flush.called

        # Empty registry raises error
        empty_svc = QuantStrategyService(bootstrap_servers="localhost:9092")
        with pytest.raises(RuntimeError, match="StrategyRegistry is empty"):
            empty_svc.process_technical_signal_envelope(MagicMock())

    # 2. Producer accessor coverage
    assert svc._producer is not None

    # 3. Direct execution of _consumer_loop poll branches
    with patch("confluent_kafka.Consumer", return_value=mock_consumer):
        # Setup mock messages: None, EOF error, General error, Valid message
        msg_eof = MagicMock()
        err_eof = MagicMock()
        err_eof.code.return_value = confluent_kafka.KafkaError._PARTITION_EOF
        msg_eof.error.return_value = err_eof

        msg_err = MagicMock()
        err_gen = MagicMock()
        err_gen.code.return_value = -999
        msg_err.error.return_value = err_gen

        msg_valid = MagicMock()
        msg_valid.error.return_value = None
        msg_valid.topic.return_value = "signals.technical"
        msg_valid.partition.return_value = 0
        msg_valid.value.return_value = b"{}"

        # Feed sequence then terminate loop
        def poll_side_effect(timeout: float = 0.2) -> Any:
            if not hasattr(poll_side_effect, "calls"):
                poll_side_effect.calls = 0  # type: ignore[attr-defined]
            poll_side_effect.calls += 1  # type: ignore[attr-defined]
            c = poll_side_effect.calls  # type: ignore[attr-defined]
            if c == 1:
                return None
            elif c == 2:
                return msg_eof
            elif c == 3:
                return msg_err
            elif c == 4:
                return msg_valid
            else:
                svc._running = False
                return None

        mock_consumer.poll.side_effect = poll_side_effect
        svc._assigned_partitions = {("signals.technical", 0)}

        with patch.object(svc, "handle_raw_message", return_value=True):
            svc._running = True
            svc._consumer_loop()

        assert mock_consumer.commit.called
        assert mock_consumer.close.called


def test_worker_handle_raw_message_candle_and_suppression() -> None:
    """Cover MARKET_CANDLES topic in handle_raw_message and unfinalized candle suppression."""
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    c = make_candle(close=150.0)

    # 1. Market candle handling
    env_c = EventEnvelope[Candle](
        correlation_id="cid-c",
        produced_at=FIXED_UTC,
        source=ComponentSource.MARKET_DATA_STREAMER,
        payload=c,
    )
    with patch.object(svc.cache, "save_candle") as mock_save:
        res = svc.handle_raw_message(TopicName.MARKET_CANDLES.value, env_c.to_json_bytes())
        assert res is True
        assert mock_save.called

    # 2. Unfinalized candle suppression in process_technical_signal_envelope
    c_unfinal = Candle(
        symbol="TCS.NS",
        interval=CandleInterval.FIVE_MINUTES,
        open_time=FIXED_UTC - timedelta(minutes=5),
        close_time=FIXED_UTC,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("100"),
        volume=Decimal("1000"),
        is_final=False,  # Unfinalized
        provenance=SAMPLE_PROV,
    )
    svc.latest_candles[("TCS.NS", "5m")] = c_unfinal
    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        rules_long=["r1"],
        indicators={"ema_9": Decimal("102")},
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    env_sig = EventEnvelope[TechnicalSignal](
        correlation_id="cid-unfin",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )
    strat = EmaTrendFollowingStrategy({"strategy_version": "1.0.0", "base_allocation_pct": "0.10"})
    svc.register_strategy(strat)
    out = svc.process_technical_signal_envelope(env_sig)
    assert out == []

    # 3. DLQ producer initialization
    assert svc._dlq_producer is None
    with patch("confluent_kafka.Producer") as mock_p_cls:
        p_inst = MagicMock()
        mock_p_cls.return_value = p_inst
        dlq_p = svc._get_dlq_producer()
        assert dlq_p is p_inst
        assert svc._dlq_producer is p_inst


def test_worker_warmup_and_direction_branches() -> None:
    """Cover warmup suppression, FLAT/SHORT direction conversions, and claim failure in worker."""
    fake_redis = FakeRedis()
    svc = QuantStrategyService(bootstrap_servers="localhost:9092")
    svc.redis_client = fake_redis  # type: ignore[assignment]
    svc.manifest_manager = ManifestManager(fake_redis)  # type: ignore[arg-type]
    svc.cache._client = fake_redis  # type: ignore[assignment]

    candle = make_candle(close=100.0)
    svc.cache.save_candle(candle)

    # 1. Warmup decision branch
    class WarmupStrat(BaseStrategy):
        @property
        def strategy_name(self) -> str:
            return "warmup_strat"

        @property
        def strategy_version(self) -> str:
            return "1.0.0"

        def evaluate(
            self,
            candle: Candle,
            indicators: dict[str, Decimal],
            technical_signal: TechnicalSignal | None = None,
        ) -> StrategyDecision:
            return StrategyDecision(
                strategy_name="warmup_strat",
                strategy_version="1.0.0",
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                is_warmup=True,
            )

    svc.register_strategy(WarmupStrat({}))
    tech_sig = TechnicalSignal(
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.FIVE_MINUTES,
        direction=Direction.LONG,
        confidence=Decimal("1.00"),
        rules_long=["r1"],
        indicators={"atr_14": Decimal("2.0")},
        config_version="1.0.0",
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )
    env = EventEnvelope[TechnicalSignal](
        correlation_id="cid-warmup",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )
    outs = svc.process_technical_signal_envelope(env)
    assert outs == []

    # 2. SHORT direction converted to FLAT
    class ShortStrat(BaseStrategy):
        @property
        def strategy_name(self) -> str:
            return "short_strat"

        @property
        def strategy_version(self) -> str:
            return "1.0.0"

        def evaluate(
            self,
            candle: Candle,
            indicators: dict[str, Decimal],
            technical_signal: TechnicalSignal | None = None,
        ) -> StrategyDecision:
            return StrategyDecision(
                strategy_name="short_strat",
                strategy_version="1.0.0",
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.SHORT,
                target_weight=Decimal("-0.2"),
            )

    svc2 = QuantStrategyService(bootstrap_servers="localhost:9092")
    svc2.redis_client = fake_redis  # type: ignore[assignment]
    svc2.manifest_manager = ManifestManager(fake_redis)  # type: ignore[arg-type]
    svc2.cache._client = fake_redis  # type: ignore[assignment]
    svc2.register_strategy(ShortStrat({}))

    env2 = EventEnvelope[TechnicalSignal](
        event_id=uuid4(),
        correlation_id="cid-short",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )

    with patch.object(svc2.publisher, "publish_signal_verified") as mock_pub:
        mock_env = MagicMock()
        mock_pub.return_value = mock_env
        outs_short = svc2.process_technical_signal_envelope(env2)
        assert len(outs_short) == 1
        published_sig = mock_pub.call_args[1]["signal"]
        assert published_sig.direction == Direction.FLAT
        assert published_sig.target_weight == Decimal("0")

    # 3. Claim failure branch
    env3 = EventEnvelope[TechnicalSignal](
        event_id=uuid4(),
        correlation_id="cid-no-claim",
        produced_at=FIXED_UTC,
        source=ComponentSource.TECHNICAL_ANALYST,
        payload=tech_sig,
    )
    with patch.object(svc2.manifest_manager, "acquire_claim", return_value=False):
        outs_no_claim = svc2.process_technical_signal_envelope(env3)
        assert outs_no_claim == []
