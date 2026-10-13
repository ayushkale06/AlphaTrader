"""Deterministic historical tick replay provider (ADR-005)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.common.schemas.market import DataProvenance, FeedMode, Tick
from src.mcp_servers.market_data_streamer.providers.base import MarketDataProvider

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DEFAULT_SAMPLE_PATH = REPO_ROOT / "data" / "historical" / "nifty50_sample_ticks.jsonl"


class ReplayProvider(MarketDataProvider):
    """Replays committed historical tick recording files with configurable speed.

    Ensures that demos, benchmark runs, and student/professor audits can reproduce
    exact execution sequences deterministically.
    """

    def __init__(
        self,
        filepath: str | Path | None = None,
        speed_multiplier: float = 1.0,
        loop: bool = False,
    ) -> None:
        self._filepath = Path(filepath) if filepath else DEFAULT_SAMPLE_PATH
        self._speed_multiplier = max(0.01, speed_multiplier)
        self._loop = loop
        self._running = False
        self._subscribed: set[str] = set()

    @property
    def name(self) -> str:
        return "replay_feed"

    @property
    def feed_mode(self) -> FeedMode:
        return FeedMode.REPLAY

    @property
    def is_delayed(self) -> bool:
        return False

    @property
    def delay_seconds(self) -> int | None:
        return 0

    async def connect(self) -> None:
        if not self._filepath.exists():
            msg = f"Replay data file not found: {self._filepath}"
            raise FileNotFoundError(msg)
        self._running = True

    async def subscribe(self, symbols: Sequence[str]) -> None:
        self._subscribed.update(symbols)

    async def stream_quotes(self) -> AsyncIterator[Tick]:
        """Stream ticks read from the historical JSONL file."""
        while self._running:
            lines = self._filepath.read_text(encoding="utf-8").splitlines()
            last_timestamp: datetime | None = None

            for line in lines:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue

                raw = json.loads(line)
                symbol = raw["symbol"]
                if self._subscribed and symbol not in self._subscribed:
                    continue

                event_ts = datetime.fromisoformat(raw["timestamp"]).astimezone(UTC)
                if last_timestamp is not None:
                    delta = (event_ts - last_timestamp).total_seconds()
                    if delta > 0:
                        sleep_time = delta / self._speed_multiplier
                        # Cap sleep at 0.5s for fast replay in unit/integration tests
                        await asyncio.sleep(min(0.5, sleep_time))
                last_timestamp = event_ts

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
                    timestamp=event_ts,  # Preserves historical event time per ADR-005
                    last_price=Decimal(str(raw["last_price"])),
                    volume=Decimal(str(raw["volume"])),
                    bid_price=Decimal(str(raw["bid_price"])) if raw.get("bid_price") else None,
                    ask_price=Decimal(str(raw["ask_price"])) if raw.get("ask_price") else None,
                    sequence_number=int(raw.get("sequence_number", 1)),
                    provenance=provenance,
                )
                yield tick

            if not self._loop:
                break

    async def close(self) -> None:
        self._running = False
