"""Typed runtime configuration and safety boundary enforcement for AlphaTrader.

Loads environment variables via ``pydantic-settings`` and enforces the hard
architectural invariant (ADR-002) that AlphaTrader executes strictly in
``PAPER`` trading mode with live order execution disabled at startup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.common.schemas.market import FeedMode

AppEnvironment = Literal["development", "staging", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
MarketFeedMode = FeedMode


class AlphaTraderSettings(BaseSettings):
    """Immutable, strictly-typed configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- Core Application Metadata ---
    app_env: AppEnvironment = Field(
        default="development",
        description="Runtime deployment environment.",
    )
    service_name: str = Field(
        default="alphatrader-core",
        min_length=1,
        description="Logical microservice or agent identifier.",
    )

    # --- Non-Negotiable Paper Trading Safety Boundary (ADR-002) ---
    trading_mode: str = Field(
        default="PAPER",
        description="Execution mode. Must strictly equal 'PAPER'.",
    )
    live_trading_enabled: bool = Field(
        default=False,
        description="Hard safety kill-switch. Must strictly equal False.",
    )

    # --- Market & Portfolio Configuration (NSE NIFTY 50) ---
    market_timezone: str = Field(
        default="Asia/Kolkata",
        min_length=1,
        description="Canonical exchange timezone for NIFTY 50 timestamps.",
    )
    nse_market_feed_mode: MarketFeedMode = Field(
        default=FeedMode.SIMULATED,
        description="Read-only market data ingestion mode.",
    )
    initial_paper_capital_inr: float = Field(
        default=10_000_000.0,
        gt=0.0,
        description="Starting virtual capital in INR for the paper ledger.",
    )

    # --- Deterministic Risk Guardrail Invariants (Non-LLM) ---
    max_daily_drawdown_pct: float = Field(
        default=2.0,
        gt=0.0,
        le=5.0,
        description="Maximum daily drawdown percentage before deterministic floor halt.",
    )
    max_position_size_pct: float = Field(
        default=5.0,
        gt=0.0,
        le=20.0,
        description="Maximum single-instrument notional as percentage of portfolio equity.",
    )
    max_open_positions: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum concurrent open positions across NIFTY 50 constituents.",
    )
    max_orders_per_minute: int = Field(
        default=30,
        ge=1,
        le=600,
        description="Maximum order submissions per minute before rate-limiter trips.",
    )

    # --- Data & Messaging Infrastructure ---
    postgres_host: str = Field(default="localhost", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = Field(default="alphatrader", min_length=1)
    postgres_password: str = Field(default="alphatrader_local_dev_only", min_length=1)
    postgres_db: str = Field(default="alphatrader_ledger", min_length=1)

    redis_host: str = Field(default="localhost", min_length=1)
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: str | None = Field(default=None)
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        min_length=1,
        description="Redis connection URL for low-latency state and circuit-breaker flags.",
    )

    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        min_length=1,
        description="Comma-separated Kafka/Redpanda bootstrap servers.",
    )
    redpanda_brokers: str = Field(
        default="localhost:9092",
        min_length=1,
        description="Alias for kafka_bootstrap_servers (Redpanda Kafka API).",
    )
    kafka_schema_registry_url: str = Field(
        default="http://localhost:8081",
        min_length=1,
        description="Redpanda/Kafka Schema Registry HTTP endpoint.",
    )

    # --- Observability & Structured Logging ---
    log_level: LogLevel = Field(
        default="INFO",
        description="Minimum severity level for structured logging.",
    )
    log_json: bool = Field(
        default=True,
        description="Emit structured JSON log lines when True.",
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317",
        min_length=1,
        description="OpenTelemetry OTLP collector endpoint.",
    )
    prometheus_metrics_port: int = Field(
        default=9090,
        ge=1024,
        le=65535,
        description="HTTP port for Prometheus metrics exposition.",
    )

    @field_validator("trading_mode", mode="before")
    @classmethod
    def enforce_paper_trading_mode(cls, value: object) -> str:
        """Reject any configuration attempting to run outside PAPER mode."""
        if not isinstance(value, str):
            msg = "SAFETY VIOLATION (ADR-002): TRADING_MODE must be the string 'PAPER'."
            raise ValueError(msg)
        normalized = value.strip().upper()
        if normalized != "PAPER":
            msg = (
                f"SAFETY VIOLATION (ADR-002): TRADING_MODE='{value}' is prohibited. "
                "AlphaTrader is strictly restricted to 'PAPER' trading mode."
            )
            raise ValueError(msg)
        return normalized

    @field_validator("live_trading_enabled", mode="after")
    @classmethod
    def enforce_live_trading_disabled(cls, value: bool) -> bool:
        """Reject any configuration attempting to enable live real-money trading."""
        if value is True:
            msg = (
                "SAFETY VIOLATION (ADR-002): LIVE_TRADING_ENABLED=True is prohibited. "
                "AlphaTrader never routes orders to real-money brokerage endpoints."
            )
            raise ValueError(msg)
        return False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn(self) -> str:
        """Construct the asyncpg PostgreSQL connection DSN (with password masked in logs)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn_masked(self) -> str:
        """Construct a safe asyncpg PostgreSQL DSN with password redacted."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_sync_dsn(self) -> str:
        """Construct the synchronous psycopg PostgreSQL connection DSN."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_sync_dsn_masked(self) -> str:
        """Construct a safe synchronous PostgreSQL DSN with password redacted."""
        return (
            f"postgresql://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url_masked(self) -> str:
        """Construct a safe Redis URL with password redacted if present."""
        if "@" in self.redis_url:
            prefix, rest = self.redis_url.split("://", 1)
            _, host_port = rest.split("@", 1)
            return f"{prefix}://:***@{host_port}"
        return self.redis_url


@lru_cache(maxsize=1)
def get_settings() -> AlphaTraderSettings:
    """Return a cached singleton of validated application settings."""
    return AlphaTraderSettings()
