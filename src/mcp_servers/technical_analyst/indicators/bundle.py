"""Symbol multi-indicator container and tracker for incremental updates."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.common.schemas.market import Candle
from src.mcp_servers.technical_analyst.indicators.engine import (
    ADX,
    ATR,
    EMA,
    MACD,
    RSI,
    SMA,
    BollingerBands,
    RollingVWAP,
    Stochastic,
)


class SymbolIndicatorSet:
    """Manages all streaming indicators for a single (symbol, interval) pair."""

    def __init__(self) -> None:
        self.ema_9 = EMA(period=9)
        self.ema_21 = EMA(period=21)
        self.ema_50 = EMA(period=50)
        self.sma_20 = SMA(period=20)
        self.rsi_14 = RSI(period=14)
        self.macd = MACD(fast_period=12, slow_period=26, signal_period=9)
        self.bb = BollingerBands(period=20, num_std=2.0)
        self.atr_14 = ATR(period=14)
        self.vwap = RollingVWAP(reset_daily=True)
        self.adx = ADX(period=14)
        self.stoch = Stochastic(k_period=14, smooth_k=3, smooth_d=3)

    def update(self, candle: Candle) -> dict[str, Decimal]:
        """Update all indicators with a new final bar and return dictionary of ready indicators."""
        e9_val = self.ema_9.update(candle)
        e21_val = self.ema_21.update(candle)
        e50_val = self.ema_50.update(candle)
        s20_val = self.sma_20.update(candle)
        rsi_val = self.rsi_14.update(candle)
        macd_val = self.macd.update(candle)
        bb_val = self.bb.update(candle)
        atr_val = self.atr_14.update(candle)
        vwap_val = self.vwap.update(candle)
        adx_val = self.adx.update(candle)
        stoch_val = self.stoch.update(candle)

        res: dict[str, Decimal] = {}
        if e9_val.value is not None:
            res["ema_9"] = e9_val.value
        if e21_val.value is not None:
            res["ema_21"] = e21_val.value
        if e50_val.value is not None:
            res["ema_50"] = e50_val.value
        if s20_val.value is not None:
            res["sma_20"] = s20_val.value
        if rsi_val.value is not None:
            res["rsi_14"] = rsi_val.value
        if macd_val.macd_line is not None:
            res["macd_line"] = macd_val.macd_line
        if macd_val.signal_line is not None:
            res["macd_signal"] = macd_val.signal_line
        if macd_val.histogram is not None:
            res["macd_hist"] = macd_val.histogram
        if bb_val.upper is not None:
            res["bb_upper"] = bb_val.upper
        if bb_val.middle is not None:
            res["bb_middle"] = bb_val.middle
        if bb_val.lower is not None:
            res["bb_lower"] = bb_val.lower
        if bb_val.bandwidth is not None:
            res["bb_bandwidth"] = bb_val.bandwidth
        if bb_val.percent_b is not None:
            res["bb_percent_b"] = bb_val.percent_b
        if atr_val.value is not None:
            res["atr_14"] = atr_val.value
        if vwap_val.value is not None:
            res["vwap"] = vwap_val.value
        if adx_val.adx is not None:
            res["adx"] = adx_val.adx
        if adx_val.plus_di is not None:
            res["plus_di"] = adx_val.plus_di
        if adx_val.minus_di is not None:
            res["minus_di"] = adx_val.minus_di
        if stoch_val.k is not None:
            res["stoch_k"] = stoch_val.k
        if stoch_val.d is not None:
            res["stoch_d"] = stoch_val.d

        return res

    def snapshot(self) -> dict[str, Any]:
        """Serialize indicator states."""
        return {
            "ema_9": self.ema_9.snapshot(),
            "ema_21": self.ema_21.snapshot(),
            "ema_50": self.ema_50.snapshot(),
            "sma_20": self.sma_20.snapshot(),
            "rsi_14": self.rsi_14.snapshot(),
            "macd": self.macd.snapshot(),
            "bb": self.bb.snapshot(),
            "atr_14": self.atr_14.snapshot(),
            "vwap": self.vwap.snapshot(),
            "adx": self.adx.snapshot(),
            "stoch": self.stoch.snapshot(),
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Restore indicator states."""
        if "ema_9" in state:
            self.ema_9.restore(state["ema_9"])
        if "ema_21" in state:
            self.ema_21.restore(state["ema_21"])
        if "ema_50" in state:
            self.ema_50.restore(state["ema_50"])
        if "sma_20" in state:
            self.sma_20.restore(state["sma_20"])
        if "rsi_14" in state:
            self.rsi_14.restore(state["rsi_14"])
        if "macd" in state:
            self.macd.restore(state["macd"])
        if "bb" in state:
            self.bb.restore(state["bb"])
        if "atr_14" in state:
            self.atr_14.restore(state["atr_14"])
        if "vwap" in state:
            self.vwap.restore(state["vwap"])
        if "adx" in state:
            self.adx.restore(state["adx"])
        if "stoch" in state:
            self.stoch.restore(state["stoch"])
