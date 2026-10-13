"""Factory function to instantiate configured MarketDataProvider (ADR-005)."""

from __future__ import annotations

from typing import Any

from src.common.config import get_settings
from src.common.schemas.market import FeedMode
from src.mcp_servers.market_data_streamer.providers.base import MarketDataProvider
from src.mcp_servers.market_data_streamer.providers.live import LiveProvider
from src.mcp_servers.market_data_streamer.providers.replay import ReplayProvider
from src.mcp_servers.market_data_streamer.providers.simulated import SimulatedProvider


def create_market_data_provider(
    mode: FeedMode | str | None = None,
    **kwargs: Any,
) -> MarketDataProvider:
    """Create a MarketDataProvider instance based on configured feed mode."""
    if mode is None:
        settings = get_settings()
        selected_mode = settings.nse_market_feed_mode
    elif isinstance(mode, str):
        selected_mode = FeedMode(mode.upper())
    else:
        selected_mode = mode

    if selected_mode == FeedMode.SIMULATED:
        seed = kwargs.get("seed", 42)
        interval = kwargs.get("tick_interval_seconds", 0.1)
        symbols = kwargs.get("symbols")
        return SimulatedProvider(seed=seed, tick_interval_seconds=interval, symbols=symbols)
    if selected_mode == FeedMode.REPLAY:
        filepath = kwargs.get("filepath")
        speed = kwargs.get("speed_multiplier", 1.0)
        loop = kwargs.get("loop", False)
        return ReplayProvider(filepath=filepath, speed_multiplier=speed, loop=loop)
    if selected_mode == FeedMode.LIVE:
        symbols = kwargs.get("symbols")
        poll_interval = kwargs.get("poll_interval_seconds", 10.0)
        api_key = kwargs.get("api_key")
        return LiveProvider(
            symbols=symbols,
            poll_interval_seconds=poll_interval,
            api_key=api_key,
        )

    msg = f"Unsupported market feed mode: {selected_mode}"
    raise ValueError(msg)
