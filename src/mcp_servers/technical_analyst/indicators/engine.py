"""Incremental streaming indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, VWAP, ADX, Stoch.

Numerical Invariants:
- Input/Output decimal safety: Candle OHLCV Decimals converted cleanly at boundaries.
- Internal calculation uses 64-bit IEEE 754 floats for streaming numerical efficiency
  and matches standard TA libraries (Wilder's smoothing, exponential smoothing, std dev).
- Explicit warmup: until window size is reached, is_ready is False and values are None.
- No lookahead: bar t only uses information from bars <= t.
"""

from __future__ import annotations

import collections
import math
from decimal import Decimal
from typing import Any

from src.common.schemas.market import Candle
from src.mcp_servers.technical_analyst.indicators.base import (
    ADXValue,
    BollingerBandsValue,
    Indicator,
    MACDValue,
    SingleValue,
    StochasticValue,
)


def _to_decimal(val: float | None, places: int = 4) -> Decimal | None:
    if val is None or math.isnan(val) or math.isinf(val):
        return None
    # Quantize to Decimal
    return Decimal(str(round(val, places)))


class SMA(Indicator):
    """Simple Moving Average over N periods.

    Maintains a rolling window buffer of size N, running sum O(1) update.
    """

    def __init__(self, period: int = 20, price_field: str = "close") -> None:
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self.period = period
        self.price_field = price_field
        self._window: collections.deque[float] = collections.deque(maxlen=period)
        self._running_sum: float = 0.0

    @property
    def is_ready(self) -> bool:
        return len(self._window) == self.period

    def update(self, candle: Candle) -> SingleValue:
        price = float(getattr(candle, self.price_field))
        if len(self._window) == self.period:
            self._running_sum -= self._window[0]
        self._window.append(price)
        self._running_sum += price

        if not self.is_ready:
            return SingleValue(is_ready=False, value=None)

        avg = self._running_sum / self.period
        return SingleValue(is_ready=True, value=_to_decimal(avg))

    def reset(self) -> None:
        self._window.clear()
        self._running_sum = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {"window": list(self._window), "running_sum": self._running_sum}

    def restore(self, state: dict[str, Any]) -> None:
        self._window = collections.deque(state.get("window", []), maxlen=self.period)
        self._running_sum = float(state.get("running_sum", 0.0))


class EMA(Indicator):
    """Exponential Moving Average over N periods.

    Uses standard multiplier alpha = 2 / (period + 1).
    Initializes at bar 0 with first price, recursive O(1) thereafter.
    Reports is_ready=True after observing at least `period` bars (standard warmup convention).
    """

    def __init__(self, period: int = 20, price_field: str = "close") -> None:
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self.period = period
        self.price_field = price_field
        self.alpha: float = 2.0 / (period + 1)
        self._count: int = 0
        self._ema: float | None = None

    @property
    def is_ready(self) -> bool:
        return self._count >= self.period

    def update(self, candle: Candle) -> SingleValue:
        price = float(getattr(candle, self.price_field))
        self._count += 1
        if self._ema is None:
            self._ema = price
        else:
            self._ema = (self.alpha * price) + ((1.0 - self.alpha) * self._ema)

        if not self.is_ready:
            return SingleValue(is_ready=False, value=None)

        return SingleValue(is_ready=True, value=_to_decimal(self._ema))

    def reset(self) -> None:
        self._count = 0
        self._ema = None

    def snapshot(self) -> dict[str, Any]:
        return {"count": self._count, "ema": self._ema}

    def restore(self, state: dict[str, Any]) -> None:
        self._count = int(state.get("count", 0))
        self._ema = state.get("ema")


class RSI(Indicator):
    """Wilder's Relative Strength Index over N periods (default 14).

    First N changes: simple average of gains and losses.
    Subsequent: Wilder smoothing (avg_gain * (N-1) + gain) / N.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self.period = period
        self._prev_close: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._rsi: float | None = None

    @property
    def is_ready(self) -> bool:
        return self._rsi is not None

    def update(self, candle: Candle) -> SingleValue:
        close = float(candle.close)
        if self._prev_close is None:
            self._prev_close = close
            return SingleValue(is_ready=False, value=None)

        change = close - self._prev_close
        self._prev_close = close
        gain = max(0.0, change)
        loss = max(0.0, -change)

        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) == self.period:
                self._avg_gain = sum(self._gains) / self.period
                self._avg_loss = sum(self._losses) / self.period
                rsi_val = self._compute_rsi(self._avg_gain, self._avg_loss)
                self._rsi = rsi_val
                return SingleValue(is_ready=True, value=_to_decimal(self._rsi))
            return SingleValue(is_ready=False, value=None)

        # Wilder's smoothing
        assert self._avg_gain is not None and self._avg_loss is not None
        self._avg_gain = ((self._avg_gain * (self.period - 1)) + gain) / self.period
        self._avg_loss = ((self._avg_loss * (self.period - 1)) + loss) / self.period
        self._rsi = self._compute_rsi(self._avg_gain, self._avg_loss)
        return SingleValue(is_ready=True, value=_to_decimal(self._rsi))

    def _compute_rsi(self, avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0.0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def reset(self) -> None:
        self._prev_close = None
        self._gains.clear()
        self._losses.clear()
        self._avg_gain = None
        self._avg_loss = None
        self._rsi = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "prev_close": self._prev_close,
            "gains": list(self._gains),
            "losses": list(self._losses),
            "avg_gain": self._avg_gain,
            "avg_loss": self._avg_loss,
            "rsi": self._rsi,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._prev_close = state.get("prev_close")
        self._gains = list(state.get("gains", []))
        self._losses = list(state.get("losses", []))
        self._avg_gain = state.get("avg_gain")
        self._avg_loss = state.get("avg_loss")
        self._rsi = state.get("rsi")


class MACD(Indicator):
    """Moving Average Convergence Divergence.

    Fast EMA (default 12), Slow EMA (default 26), Signal EMA (default 9).
    Histogram = MACD Line - Signal Line.
    """

    def __init__(
        self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(f"fast_period ({fast_period}) must be < slow_period ({slow_period})")
        self.fast_ema = EMA(period=fast_period)
        self.slow_ema = EMA(period=slow_period)
        self.signal_period = signal_period
        self._signal_alpha = 2.0 / (signal_period + 1)
        self._macd_history_for_signal: list[float] = []
        self._signal_ema: float | None = None
        self._last_val: MACDValue | None = None

    @property
    def is_ready(self) -> bool:
        return self._signal_ema is not None

    def update(self, candle: Candle) -> MACDValue:
        f_val = self.fast_ema.update(candle)
        s_val = self.slow_ema.update(candle)

        if not self.slow_ema.is_ready or f_val.value is None or s_val.value is None:
            self._last_val = MACDValue(
                is_ready=False, macd_line=None, signal_line=None, histogram=None
            )
            return self._last_val

        macd_line = float(f_val.value) - float(s_val.value)

        if self._signal_ema is None:
            self._macd_history_for_signal.append(macd_line)
            if len(self._macd_history_for_signal) == self.signal_period:
                self._signal_ema = sum(self._macd_history_for_signal) / self.signal_period
                hist = macd_line - self._signal_ema
                self._last_val = MACDValue(
                    is_ready=True,
                    macd_line=_to_decimal(macd_line),
                    signal_line=_to_decimal(self._signal_ema),
                    histogram=_to_decimal(hist),
                )
                return self._last_val
            self._last_val = MACDValue(
                is_ready=False,
                macd_line=_to_decimal(macd_line),
                signal_line=None,
                histogram=None,
            )
            return self._last_val

        self._signal_ema = (self._signal_alpha * macd_line) + (
            (1.0 - self._signal_alpha) * self._signal_ema
        )
        hist = macd_line - self._signal_ema
        self._last_val = MACDValue(
            is_ready=True,
            macd_line=_to_decimal(macd_line),
            signal_line=_to_decimal(self._signal_ema),
            histogram=_to_decimal(hist),
        )
        return self._last_val

    def reset(self) -> None:
        self.fast_ema.reset()
        self.slow_ema.reset()
        self._macd_history_for_signal.clear()
        self._signal_ema = None
        self._last_val = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "fast_ema": self.fast_ema.snapshot(),
            "slow_ema": self.slow_ema.snapshot(),
            "macd_history": list(self._macd_history_for_signal),
            "signal_ema": self._signal_ema,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self.fast_ema.restore(state.get("fast_ema", {}))
        self.slow_ema.restore(state.get("slow_ema", {}))
        self._macd_history_for_signal = list(state.get("macd_history", []))
        self._signal_ema = state.get("signal_ema")


class BollingerBands(Indicator):
    """Bollinger Bands over N periods (default 20, 2 standard deviations).

    Upper = Middle + (num_std * std)
    Middle = SMA(period)
    Lower = Middle - (num_std * std)
    Bandwidth = (Upper - Lower) / Middle
    %B = (Close - Lower) / (Upper - Lower)
    """

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        if period < 2:
            raise ValueError(f"Period must be >= 2, got {period}")
        self.period = period
        self.num_std = num_std
        self._window: collections.deque[float] = collections.deque(maxlen=period)
        self._running_sum: float = 0.0
        self._running_sq_sum: float = 0.0

    @property
    def is_ready(self) -> bool:
        return len(self._window) == self.period

    def update(self, candle: Candle) -> BollingerBandsValue:
        close = float(candle.close)
        if len(self._window) == self.period:
            old = self._window[0]
            self._running_sum -= old
            self._running_sq_sum -= old * old

        self._window.append(close)
        self._running_sum += close
        self._running_sq_sum += close * close

        if not self.is_ready:
            return BollingerBandsValue(
                is_ready=False, upper=None, middle=None, lower=None, bandwidth=None, percent_b=None
            )

        mean = self._running_sum / self.period
        # Population standard deviation matching pandas/ta rolling std (ddof=0)
        variance = max(0.0, (self._running_sq_sum / self.period) - (mean * mean))
        std = math.sqrt(variance)

        upper = mean + (self.num_std * std)
        lower = mean - (self.num_std * std)
        bandwidth = (upper - lower) / mean if mean != 0.0 else 0.0
        denom = upper - lower
        percent_b = (close - lower) / denom if denom != 0.0 else 0.5

        return BollingerBandsValue(
            is_ready=True,
            upper=_to_decimal(upper),
            middle=_to_decimal(mean),
            lower=_to_decimal(lower),
            bandwidth=_to_decimal(bandwidth),
            percent_b=_to_decimal(percent_b),
        )

    def reset(self) -> None:
        self._window.clear()
        self._running_sum = 0.0
        self._running_sq_sum = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "window": list(self._window),
            "running_sum": self._running_sum,
            "running_sq_sum": self._running_sq_sum,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._window = collections.deque(state.get("window", []), maxlen=self.period)
        self._running_sum = float(state.get("running_sum", 0.0))
        self._running_sq_sum = float(state.get("running_sq_sum", 0.0))


class ATR(Indicator):
    """Average True Range over N periods (default 14) with Wilder's RMA smoothing.

    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    First N: simple average of TR.
    Subsequent: Wilder's smoothing (prev_atr * (N-1) + TR) / N.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self.period = period
        self._prev_close: float | None = None
        self._tr_seed: list[float] = []
        self._atr: float | None = None

    @property
    def is_ready(self) -> bool:
        return self._atr is not None

    def update(self, candle: Candle) -> SingleValue:
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        if self._prev_close is None:
            # First bar has no prev_close, TR is high - low
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))

        self._prev_close = close

        if self._atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) == self.period:
                self._atr = sum(self._tr_seed) / self.period
                return SingleValue(is_ready=True, value=_to_decimal(self._atr))
            return SingleValue(is_ready=False, value=None)

        self._atr = ((self._atr * (self.period - 1)) + tr) / self.period
        return SingleValue(is_ready=True, value=_to_decimal(self._atr))

    def reset(self) -> None:
        self._prev_close = None
        self._tr_seed.clear()
        self._atr = None

    def snapshot(self) -> dict[str, Any]:
        return {"prev_close": self._prev_close, "tr_seed": list(self._tr_seed), "atr": self._atr}

    def restore(self, state: dict[str, Any]) -> None:
        self._prev_close = state.get("prev_close")
        self._tr_seed = list(state.get("tr_seed", []))
        self._atr = state.get("atr")


class RollingVWAP(Indicator):
    """Rolling Intraday Volume Weighted Average Price (VWAP).

    Cumulative sum of (Typical Price * Volume) / Cumulative Volume.
    Resets at session date boundary (or configurable window/explicit reset).
    """

    def __init__(self, reset_daily: bool = True) -> None:
        self.reset_daily = reset_daily
        self._cum_pv: float = 0.0
        self._cum_vol: float = 0.0
        self._last_date_str: str | None = None

    @property
    def is_ready(self) -> bool:
        return self._cum_vol > 0.0

    def update(self, candle: Candle) -> SingleValue:
        date_str = candle.open_time.strftime("%Y-%m-%d")
        if self.reset_daily and self._last_date_str is not None and date_str != self._last_date_str:
            self.reset()
        self._last_date_str = date_str

        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)
        volume = float(candle.volume)
        typical_price = (high + low + close) / 3.0

        self._cum_pv += typical_price * volume
        self._cum_vol += volume

        if self._cum_vol <= 0.0:
            return SingleValue(is_ready=False, value=None)

        vwap_val = self._cum_pv / self._cum_vol
        return SingleValue(is_ready=True, value=_to_decimal(vwap_val))

    def reset(self) -> None:
        self._cum_pv = 0.0
        self._cum_vol = 0.0
        self._last_date_str = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "cum_pv": self._cum_pv,
            "cum_vol": self._cum_vol,
            "last_date_str": self._last_date_str,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._cum_pv = float(state.get("cum_pv", 0.0))
        self._cum_vol = float(state.get("cum_vol", 0.0))
        self._last_date_str = state.get("last_date_str")


class ADX(Indicator):
    """Average Directional Index (ADX) over N periods (default 14) with +DI / -DI.

    Uses standard Wilder's smoothing technique for TR, +DM, -DM and DX.
    Warmup requires 2 * period - 1 bars (period for +DI/-DI, plus period for ADX smoothing).
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError(f"Period must be >= 1, got {period}")
        self.period = period
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None

        self._tr_buffer: list[float] = []
        self._plus_dm_buffer: list[float] = []
        self._minus_dm_buffer: list[float] = []

        self._smooth_tr: float | None = None
        self._smooth_plus_dm: float | None = None
        self._smooth_minus_dm: float | None = None

        self._dx_buffer: list[float] = []
        self._adx: float | None = None
        self._last_plus_di: float | None = None
        self._last_minus_di: float | None = None

    @property
    def is_ready(self) -> bool:
        return self._adx is not None

    def update(self, candle: Candle) -> ADXValue:
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        if self._prev_high is None or self._prev_low is None or self._prev_close is None:
            self._prev_high = high
            self._prev_low = low
            self._prev_close = close
            return ADXValue(is_ready=False, adx=None, plus_di=None, minus_di=None)

        up_move = high - self._prev_high
        down_move = self._prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))

        self._prev_high = high
        self._prev_low = low
        self._prev_close = close

        if self._smooth_tr is None:
            self._tr_buffer.append(tr)
            self._plus_dm_buffer.append(plus_dm)
            self._minus_dm_buffer.append(minus_dm)

            if len(self._tr_buffer) == self.period:
                self._smooth_tr = sum(self._tr_buffer)
                self._smooth_plus_dm = sum(self._plus_dm_buffer)
                self._smooth_minus_dm = sum(self._minus_dm_buffer)
                dx, p_di, m_di = self._calculate_dx()
                self._last_plus_di = p_di
                self._last_minus_di = m_di
                self._dx_buffer.append(dx)
            return ADXValue(is_ready=False, adx=None, plus_di=None, minus_di=None)

        # Wilder's smoothing: prev - (prev/period) + current
        assert (
            self._smooth_tr is not None
            and self._smooth_plus_dm is not None
            and self._smooth_minus_dm is not None
        )
        self._smooth_tr = self._smooth_tr - (self._smooth_tr / self.period) + tr
        self._smooth_plus_dm = self._smooth_plus_dm - (self._smooth_plus_dm / self.period) + plus_dm
        self._smooth_minus_dm = (
            self._smooth_minus_dm - (self._smooth_minus_dm / self.period) + minus_dm
        )

        dx, p_di, m_di = self._calculate_dx()
        self._last_plus_di = p_di
        self._last_minus_di = m_di

        if self._adx is None:
            self._dx_buffer.append(dx)
            if len(self._dx_buffer) == self.period:
                self._adx = sum(self._dx_buffer) / self.period
                return ADXValue(
                    is_ready=True,
                    adx=_to_decimal(self._adx),
                    plus_di=_to_decimal(self._last_plus_di),
                    minus_di=_to_decimal(self._last_minus_di),
                )
            return ADXValue(
                is_ready=False,
                adx=None,
                plus_di=_to_decimal(self._last_plus_di),
                minus_di=_to_decimal(self._last_minus_di),
            )

        self._adx = ((self._adx * (self.period - 1)) + dx) / self.period
        return ADXValue(
            is_ready=True,
            adx=_to_decimal(self._adx),
            plus_di=_to_decimal(self._last_plus_di),
            minus_di=_to_decimal(self._last_minus_di),
        )

    def _calculate_dx(self) -> tuple[float, float, float]:
        if self._smooth_tr is None or self._smooth_tr == 0.0:
            return 0.0, 0.0, 0.0
        plus_di = (self._smooth_plus_dm or 0.0) / self._smooth_tr * 100.0
        minus_di = (self._smooth_minus_dm or 0.0) / self._smooth_tr * 100.0
        di_sum = plus_di + minus_di
        if di_sum == 0.0:
            return 0.0, plus_di, minus_di
        dx = abs(plus_di - minus_di) / di_sum * 100.0
        return dx, plus_di, minus_di

    def reset(self) -> None:
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._tr_buffer.clear()
        self._plus_dm_buffer.clear()
        self._minus_dm_buffer.clear()
        self._smooth_tr = None
        self._smooth_plus_dm = None
        self._smooth_minus_dm = None
        self._dx_buffer.clear()
        self._adx = None
        self._last_plus_di = None
        self._last_minus_di = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "prev_high": self._prev_high,
            "prev_low": self._prev_low,
            "prev_close": self._prev_close,
            "tr_buffer": list(self._tr_buffer),
            "plus_dm_buffer": list(self._plus_dm_buffer),
            "minus_dm_buffer": list(self._minus_dm_buffer),
            "smooth_tr": self._smooth_tr,
            "smooth_plus_dm": self._smooth_plus_dm,
            "smooth_minus_dm": self._smooth_minus_dm,
            "dx_buffer": list(self._dx_buffer),
            "adx": self._adx,
            "last_plus_di": self._last_plus_di,
            "last_minus_di": self._last_minus_di,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._prev_high = state.get("prev_high")
        self._prev_low = state.get("prev_low")
        self._prev_close = state.get("prev_close")
        self._tr_buffer = list(state.get("tr_buffer", []))
        self._plus_dm_buffer = list(state.get("plus_dm_buffer", []))
        self._minus_dm_buffer = list(state.get("minus_dm_buffer", []))
        self._smooth_tr = state.get("smooth_tr")
        self._smooth_plus_dm = state.get("smooth_plus_dm")
        self._smooth_minus_dm = state.get("smooth_minus_dm")
        self._dx_buffer = list(state.get("dx_buffer", []))
        self._adx = state.get("adx")
        self._last_plus_di = state.get("last_plus_di")
        self._last_minus_di = state.get("last_minus_di")


class Stochastic(Indicator):
    """Fast / Slow Stochastic Oscillator (%K, %D).

    Fast %K = ((Close - Lowest Low_n) / (Highest High_n - Lowest Low_n)) * 100.
    Slow %K = SMA(Fast %K, smooth_k) (default 3).
    Slow %D = SMA(Slow %K, smooth_d) (default 3).
    """

    def __init__(self, k_period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> None:
        if k_period < 1 or smooth_k < 1 or smooth_d < 1:
            raise ValueError("All stochastic periods must be >= 1")
        self.k_period = k_period
        self.smooth_k = smooth_k
        self.smooth_d = smooth_d

        self._highs: collections.deque[float] = collections.deque(maxlen=k_period)
        self._lows: collections.deque[float] = collections.deque(maxlen=k_period)
        self._closes: collections.deque[float] = collections.deque(maxlen=k_period)

        self._fast_k_buffer: collections.deque[float] = collections.deque(maxlen=smooth_k)
        self._slow_k_buffer: collections.deque[float] = collections.deque(maxlen=smooth_d)

        self._last_k: float | None = None
        self._last_d: float | None = None

    @property
    def is_ready(self) -> bool:
        return self._last_d is not None

    def update(self, candle: Candle) -> StochasticValue:
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)

        if len(self._highs) < self.k_period:
            return StochasticValue(is_ready=False, k=None, d=None)

        highest_high = max(self._highs)
        lowest_low = min(self._lows)

        denom = highest_high - lowest_low
        fast_k = ((close - lowest_low) / denom * 100.0) if denom != 0.0 else 50.0

        self._fast_k_buffer.append(fast_k)
        if len(self._fast_k_buffer) < self.smooth_k:
            return StochasticValue(is_ready=False, k=None, d=None)

        slow_k = sum(self._fast_k_buffer) / self.smooth_k
        self._slow_k_buffer.append(slow_k)

        if len(self._slow_k_buffer) < self.smooth_d:
            self._last_k = slow_k
            return StochasticValue(is_ready=False, k=_to_decimal(slow_k), d=None)

        slow_d = sum(self._slow_k_buffer) / self.smooth_d
        self._last_k = slow_k
        self._last_d = slow_d

        return StochasticValue(
            is_ready=True,
            k=_to_decimal(slow_k),
            d=_to_decimal(slow_d),
        )

    def reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._fast_k_buffer.clear()
        self._slow_k_buffer.clear()
        self._last_k = None
        self._last_d = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "highs": list(self._highs),
            "lows": list(self._lows),
            "closes": list(self._closes),
            "fast_k_buffer": list(self._fast_k_buffer),
            "slow_k_buffer": list(self._slow_k_buffer),
            "last_k": self._last_k,
            "last_d": self._last_d,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._highs = collections.deque(state.get("highs", []), maxlen=self.k_period)
        self._lows = collections.deque(state.get("lows", []), maxlen=self.k_period)
        self._closes = collections.deque(state.get("closes", []), maxlen=self.k_period)
        self._fast_k_buffer = collections.deque(
            state.get("fast_k_buffer", []), maxlen=self.smooth_k
        )
        self._slow_k_buffer = collections.deque(
            state.get("slow_k_buffer", []), maxlen=self.smooth_d
        )
        self._last_k = state.get("last_k")
        self._last_d = state.get("last_d")
