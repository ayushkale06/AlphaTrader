"""Unit tests for typed configuration and ADR-002 paper-trading safety invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.config import AlphaTraderSettings, get_settings


def test_default_settings_are_valid_and_paper_locked() -> None:
    """Default configuration must be valid, immutable, and locked to PAPER mode."""
    settings = AlphaTraderSettings()
    assert settings.trading_mode == "PAPER"
    assert settings.live_trading_enabled is False
    assert settings.app_env == "development"
    assert settings.market_timezone == "Asia/Kolkata"
    assert settings.nse_market_feed_mode == "SIMULATED"
    assert settings.max_daily_drawdown_pct == 2.0
    assert settings.max_position_size_pct == 5.0
    assert settings.max_open_positions == 10


def test_trading_mode_normalizes_lowercase_paper() -> None:
    """Lowercase or whitespace-padded 'paper' normalizes to 'PAPER'."""
    settings = AlphaTraderSettings(trading_mode="  paper ")
    assert settings.trading_mode == "PAPER"


@pytest.mark.parametrize("forbidden_mode", ["LIVE", "REAL", "PRODUCTION", "BROKER", ""])
def test_non_paper_trading_mode_raises_safety_violation(forbidden_mode: str) -> None:
    """Any trading_mode other than PAPER must fail startup validation immediately."""
    with pytest.raises(ValidationError, match=r"SAFETY VIOLATION \(ADR-002\)"):
        AlphaTraderSettings(trading_mode=forbidden_mode)


def test_non_string_trading_mode_raises_safety_violation() -> None:
    """Non-string trading_mode values must be rejected by the before-validator."""
    with pytest.raises(ValidationError, match=r"SAFETY VIOLATION \(ADR-002\)"):
        AlphaTraderSettings(trading_mode=123)  # type: ignore[arg-type]


def test_live_trading_enabled_true_raises_safety_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling LIVE_TRADING_ENABLED via constructor or env var must raise a safety error."""
    with pytest.raises(ValidationError, match=r"SAFETY VIOLATION \(ADR-002\)"):
        AlphaTraderSettings(live_trading_enabled=True)

    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValidationError, match=r"SAFETY VIOLATION \(ADR-002\)"):
        AlphaTraderSettings()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_daily_drawdown_pct", 0.0),
        ("max_daily_drawdown_pct", 5.5),
        ("max_position_size_pct", -1.0),
        ("max_position_size_pct", 25.0),
        ("max_open_positions", 0),
        ("max_open_positions", 51),
        ("initial_paper_capital_inr", 0.0),
    ],
)
def test_risk_guardrail_numeric_bounds(field_name: str, invalid_value: float) -> None:
    """Risk guardrail invariants must reject out-of-range numeric configurations."""
    kwargs: dict[str, object] = {field_name: invalid_value}
    with pytest.raises(ValidationError):
        AlphaTraderSettings(**kwargs)  # type: ignore[arg-type]


def test_postgres_dsn_and_masked_dsn() -> None:
    """Postgres DSN properties construct valid URLs and redact passwords in masked mode."""
    settings = AlphaTraderSettings(
        postgres_user="trader",
        postgres_password="super_secret_password",
        postgres_host="db.internal",
        postgres_port=5432,
        postgres_db="ledger",
    )
    assert (
        settings.postgres_dsn
        == "postgresql+asyncpg://trader:super_secret_password@db.internal:5432/ledger"
    )
    assert settings.postgres_dsn_masked == "postgresql+asyncpg://trader:***@db.internal:5432/ledger"
    assert "super_secret_password" not in settings.postgres_dsn_masked


def test_get_settings_returns_cached_instance() -> None:
    """get_settings() must return a cached singleton across repeated invocations."""
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
