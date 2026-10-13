"""Unit tests for the CLI healthcheck entry point."""

from __future__ import annotations

import json

import pytest

from src import __version__
from src.common.healthcheck import main


def test_healthcheck_cli_succeeds_on_valid_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Healthcheck CLI must exit with 0 and output version and config_valid=True."""
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    report = json.loads(captured.out)
    assert report["status"] == "HEALTHY"
    assert report["version"] == __version__
    assert report["config_valid"] is True
    assert report["trading_mode"] == "PAPER"
    assert report["live_trading_enabled"] is False
    assert len(report["correlation_id"]) == 36


def test_healthcheck_cli_fails_when_safety_invariant_violated(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Healthcheck CLI must exit with 1 and print UNHEALTHY when TRADING_MODE is invalid."""
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 1
    error_report = json.loads(captured.err)
    assert error_report["status"] == "UNHEALTHY"
    assert error_report["version"] == __version__
    assert error_report["config_valid"] is False
    assert len(error_report["errors"]) >= 1
