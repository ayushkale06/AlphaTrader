"""Unit tests for QuantSignalPublisher and cryptographic out-of-sample edge verification gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.common.schemas.market import CandleInterval, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, EdgeBasis, QuantSignal
from src.mcp_servers.quant_strategy_engine.publisher import (
    EdgeVerificationError,
    QuantSignalPublisher,
    verify_backtest_report_credential,
)

FIXED_UTC = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
VALID_HASH = "013fb35eb63db8923dd8b9fb6ad67dbcb95af32af980433b3466105fb9fd2f55"
SAMPLE_PROV = DataProvenance(provider="simulated_feed", feed_mode=FeedMode.SIMULATED)


def test_publisher_accepts_and_publishes_signal_without_edge_claim() -> None:
    """A QuantSignal without expected edge claims passes verification and publishes normally."""
    sig = QuantSignal(
        signal_id=uuid4(),
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.ONE_DAY,
        strategy_name="EMA_Crossover",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    with patch("confluent_kafka.Producer") as mock_prod_cls:
        mock_prod = MagicMock()
        mock_prod_cls.return_value = mock_prod

        pub = QuantSignalPublisher(bootstrap_servers="localhost:9092")
        env = pub.publish_signal(sig, correlation_id="cid-001")

        assert env.payload == sig
        assert mock_prod.produce.called
        call_kwargs = mock_prod.produce.call_args[1]
        assert call_kwargs["topic"] == "signals.quant"
        assert call_kwargs["key"] == b"TCS.NS"


def test_publisher_accepts_signal_with_verified_out_of_sample_report() -> None:
    """A QuantSignal claiming an edge is accepted when referencing a valid out-of-sample report."""
    sig = QuantSignal(
        signal_id=uuid4(),
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.ONE_DAY,
        strategy_name="EMA_Crossover",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        expected_net_edge_bps_per_trade=Decimal("15.5"),
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        backtest_report_id=VALID_HASH,
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    with patch("confluent_kafka.Producer") as mock_prod_cls:
        mock_prod = MagicMock()
        mock_prod_cls.return_value = mock_prod

        pub = QuantSignalPublisher(bootstrap_servers="localhost:9092")
        env = pub.publish_signal(sig, correlation_id="cid-002")

        assert env.payload == sig
        assert mock_prod.produce.called


def test_publisher_refuses_signal_when_report_id_not_in_index() -> None:
    """Publisher strictly refuses to publish if backtest_report_id is not in index.json."""
    sig = QuantSignal(
        signal_id=uuid4(),
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.ONE_DAY,
        strategy_name="EMA_Crossover",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        expected_net_edge_bps_per_trade=Decimal("15.5"),
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        backtest_report_id="b" * 64,  # Not registered
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    with patch("confluent_kafka.Producer") as mock_prod_cls:
        mock_prod = MagicMock()
        mock_prod_cls.return_value = mock_prod

        pub = QuantSignalPublisher(bootstrap_servers="localhost:9092")
        with pytest.raises(EdgeVerificationError, match="not registered in backtest index"):
            pub.publish_signal(sig, correlation_id="cid-003")

        assert not mock_prod.produce.called


def test_publisher_refuses_signal_when_report_is_not_out_of_sample(tmp_path: Path) -> None:
    """Publisher strictly refuses to publish if the registered report has edge_basis != OUT_OF_SAMPLE."""
    custom_index = tmp_path / "index.json"
    fake_hash = "c" * 64
    custom_index.write_text(
        json.dumps({
            "reports": {
                fake_hash: {
                    "strategy_name": "EMA_Crossover",
                    "strategy_version": "1.0.0",
                    "symbol": "TCS.NS",
                    "edge_basis": "IN_SAMPLE",
                }
            }
        }),
        encoding="utf-8",
    )

    sig = QuantSignal(
        signal_id=uuid4(),
        symbol="TCS.NS",
        evaluated_at=FIXED_UTC,
        interval=CandleInterval.ONE_DAY,
        strategy_name="EMA_Crossover",
        strategy_version="1.0.0",
        direction=Direction.LONG,
        target_weight=Decimal("0.10"),
        expected_net_edge_bps_per_trade=Decimal("15.5"),
        edge_basis=EdgeBasis.OUT_OF_SAMPLE,
        backtest_report_id=fake_hash,
        config_hash="a" * 64,
        provenance=SAMPLE_PROV,
    )

    with patch("confluent_kafka.Producer") as mock_prod_cls:
        mock_prod = MagicMock()
        mock_prod_cls.return_value = mock_prod

        pub = QuantSignalPublisher(bootstrap_servers="localhost:9092", index_path=custom_index)
        with pytest.raises(EdgeVerificationError, match="required OUT_OF_SAMPLE"):
            pub.publish_signal(sig, correlation_id="cid-004")

        assert not mock_prod.produce.called
