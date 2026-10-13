"""Deterministic simulated market data feed with realistic NSE market microstructure (ADR-005)."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.common.schemas.market import DataProvenance, FeedMode, Tick
from src.mcp_servers.market_data_streamer.providers.base import MarketDataProvider

# Realistic baseline seed prices for primary NIFTY 50 constituents in INR
_BASE_PRICES_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "config" / "simulated_base_prices.json"
)


def _load_base_prices() -> dict[str, Decimal]:
    if _BASE_PRICES_PATH.exists():
        try:
            data = json.loads(_BASE_PRICES_PATH.read_text(encoding="utf-8"))
            return {k: Decimal(str(v)) for k, v in data.get("prices", {}).items()}
        except Exception:
            pass
    return {
        "RELIANCE.NS": Decimal("2950.00"),
        "TCS.NS": Decimal("4100.00"),
        "HDFCBANK.NS": Decimal("1650.00"),
        "ICICIBANK.NS": Decimal("1240.00"),
        "INFY.NS": Decimal("1880.00"),
        "BHARTIARTL.NS": Decimal("1620.00"),
        "ITC.NS": Decimal("490.00"),
        "SBIN.NS": Decimal("810.00"),
        "LICI.NS": Decimal("980.00"),
        "HINDUNILVR.NS": Decimal("2680.00"),
        "LT.NS": Decimal("3550.00"),
        "BAJFINANCE.NS": Decimal("7200.00"),
        "HCLTECH.NS": Decimal("1750.00"),
        "MARUTI.NS": Decimal("12400.00"),
        "SUNPHARMA.NS": Decimal("1820.00"),
        "TATAMOTORS.NS": Decimal("920.00"),
        "ONGC.NS": Decimal("285.00"),
        "NTPC.NS": Decimal("410.00"),
        "KOTAKBANK.NS": Decimal("1810.00"),
        "TITAN.NS": Decimal("3420.00"),
    }


DEFAULT_BASE_PRICES: dict[str, Decimal] = _load_base_prices()


class SimulatedProvider(MarketDataProvider):
    """Generates realistic synthetic NSE quote ticks via a seeded geometric random walk.

    Features:
    - Enforces NSE minimum tick size (0.05 INR).
    - Enforces NSE 10% daily price bands relative to opening reference.
    - Simulates bid/ask spreads (1-3 ticks) and volume distributions.
    - Labelled explicitly as SIMULATED across all emitted provenance records.
    """

    def __init__(
        self,
        seed: int = 42,
        tick_interval_seconds: float = 0.1,
        symbols: Sequence[str] | None = None,
    ) -> None:
        self._seed = seed
        self._rng = random.Random(seed)
        self._tick_interval = tick_interval_seconds
        self._symbols = list(symbols) if symbols else list(DEFAULT_BASE_PRICES.keys())
        self._running = False
        self._seq = 1
        self._current_prices: dict[str, Decimal] = {}
        self._reference_prices: dict[str, Decimal] = {}
        self._initialize_prices()

    def _initialize_prices(self) -> None:
        """Seed initial reference and current prices."""
        for sym in self._symbols:
            base = DEFAULT_BASE_PRICES.get(sym, Decimal("1000.00"))
            self._reference_prices[sym] = base
            self._current_prices[sym] = base

    @property
    def name(self) -> str:
        return "simulated_feed"

    @property
    def feed_mode(self) -> FeedMode:
        return FeedMode.SIMULATED

    @property
    def is_delayed(self) -> bool:
        return False

    @property
    def delay_seconds(self) -> int | None:
        return 0

    async def connect(self) -> None:
        self._running = True

    async def subscribe(self, symbols: Sequence[str]) -> None:
        for s in symbols:
            if s not in self._symbols:
                self._symbols.append(s)
                base = DEFAULT_BASE_PRICES.get(s, Decimal("1000.00"))
                self._reference_prices[s] = base
                self._current_prices[s] = base

    async def stream_quotes(self) -> AsyncIterator[Tick]:
        """Stream continuous synthetic ticks until close() is called."""
        tick_size = Decimal("0.05")

        while self._running:
            # Pick a random symbol from subscribed universe
            symbol = self._rng.choice(self._symbols)
            current = self._current_prices[symbol]
            reference = self._reference_prices[symbol]

            # Price change: small random walk step (-3 to +3 ticks)
            step_ticks = self._rng.randint(-3, 3)
            new_price = current + (Decimal(str(step_ticks)) * tick_size)

            # Enforce 10% circuit band around reference
            lower_band = (reference * Decimal("0.90")).quantize(tick_size)
            upper_band = (reference * Decimal("1.10")).quantize(tick_size)
            new_price = max(lower_band, min(upper_band, new_price)).quantize(tick_size)
            self._current_prices[symbol] = new_price

            # Bid/ask spread: 1 to 3 ticks
            spread_ticks = self._rng.randint(1, 3)
            bid = (new_price - (Decimal(str(spread_ticks // 2)) * tick_size)).quantize(tick_size)
            ask = (bid + (Decimal(str(spread_ticks)) * tick_size)).quantize(tick_size)

            # Random trade volume (multiples of 5)
            volume = Decimal(str(self._rng.randint(5, 500) * 5))
            now = datetime.now(UTC)

            provenance = DataProvenance(
                provider=self.name,
                feed_mode=self.feed_mode,
                is_delayed=self.is_delayed,
                delay_seconds=self.delay_seconds,
                ingested_at=now,
            )

            tick = Tick(
                symbol=symbol,
                timestamp=now,
                last_price=new_price,
                volume=volume,
                bid_price=bid,
                ask_price=ask,
                sequence_number=self._seq,
                provenance=provenance,
            )
            self._seq += 1

            yield tick
            await asyncio.sleep(self._tick_interval)

    async def close(self) -> None:
        self._running = False
