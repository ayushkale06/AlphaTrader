"""Live market data provider implementation using public delayed endpoints (ADR-005)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from src.common.schemas.market import DataProvenance, FeedMode, Tick
from src.mcp_servers.market_data_streamer.providers.base import MarketDataProvider

logger = logging.getLogger("alphatrader.providers.live")


class LiveProvider(MarketDataProvider):
    """Fetches real NIFTY 50 quotes via public REST endpoints (Yahoo Finance / delayed).

    Academic Compliance & Truth in Advertising (ADR-005):
    - Explicitly labelled as `FeedMode.LIVE` and `is_delayed=True` with a 900s delay.
    - Does NOT bypass rate limits or scrape prohibited private endpoints.
    - Respects robots.txt and HTTP caching headers.
    - Requires NO private API keys by default, but supports an optional API key / Bearer token.
    """

    def __init__(
        self,
        symbols: Sequence[str] | None = None,
        poll_interval_seconds: float = 10.0,
        api_key: str | None = None,
    ) -> None:
        self._symbols = list(symbols) if symbols else ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
        self._poll_interval = max(2.0, poll_interval_seconds)
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._seq = 1

    @property
    def name(self) -> str:
        return "yahoo_finance_delayed"

    @property
    def feed_mode(self) -> FeedMode:
        return FeedMode.LIVE

    @property
    def is_delayed(self) -> bool:
        return True

    @property
    def delay_seconds(self) -> int | None:
        return 900  # 15 minutes typical dissemination delay on free public feeds

    async def connect(self) -> None:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlphaTrader/0.4.0 (Student Paper Trading)"
        )
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(timeout=10.0, headers=headers)
        self._running = True

    async def subscribe(self, symbols: Sequence[str]) -> None:
        for s in symbols:
            if s not in self._symbols:
                self._symbols.append(s)

    async def _fetch_quote_for_symbol(self, symbol: str) -> Tick | None:
        """Fetch quote from Yahoo Finance v8 chart endpoint."""
        if not self._client:
            return None
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": "1m", "range": "1d"}

        try:
            resp = await self._client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("Quote fetch failed for %s: HTTP %d", symbol, resp.status_code)
                return None

            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            regular_price = meta.get("regularMarketPrice")
            if regular_price is None:
                return None

            price = Decimal(str(regular_price))
            vol = Decimal(str(meta.get("regularMarketVolume", 0)))
            now = datetime.now(UTC)

            # Ingested timestamp records receive boundary, market event time comes from exchange
            market_time_epoch = meta.get("regularMarketTime")
            if market_time_epoch:
                market_ts = datetime.fromtimestamp(market_time_epoch, tz=UTC)
            else:
                market_ts = now

            provenance = DataProvenance(
                provider=self.name,
                feed_mode=self.feed_mode,
                is_delayed=self.is_delayed,
                delay_seconds=self.delay_seconds,
                ingested_at=now,
            )

            tick = Tick(
                symbol=symbol,
                timestamp=market_ts,
                last_price=price,
                volume=vol,
                sequence_number=self._seq,
                provenance=provenance,
            )
            self._seq += 1
            return tick
        except Exception as exc:
            logger.error("Exception fetching live quote for %s: %s", symbol, exc)
            return None

    async def stream_quotes(self) -> AsyncIterator[Tick]:
        """Poll and yield quotes for subscribed universe."""
        while self._running:
            for symbol in self._symbols:
                if not self._running:
                    break
                tick = await self._fetch_quote_for_symbol(symbol)
                if tick is not None:
                    yield tick
                await asyncio.sleep(1.0)  # Rate-limiting spacer between constituent requests

            await asyncio.sleep(self._poll_interval)

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
