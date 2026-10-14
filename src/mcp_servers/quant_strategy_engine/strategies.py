"""Deterministic quantitative strategy protocol, parameters, and registry.

Defines the Strategy interface, StrategyDecision dataclass, and concrete deterministic
factor strategies:
1. EmaTrendFollowingStrategy: Momentum / trend following
2. RsiBollingerMeanReversionStrategy: Statistical mean reversion
3. VolatilityBreakoutStrategy: Volatility-adjusted breakout

All strategies adhere strictly to:
- Zero LLM imports or nondeterministic calls.
- Zero lookahead: evaluate only closed candle t and indicators up to t.
- Long-only cash delivery constraints: emit LONG or FLAT only (SHORT is prohibited).
- Bounded Decimal return representations.
- Explicit indicator warm-up gates: return FLAT with reasons when unready.
"""

from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.common.schemas.market import Candle, CandleInterval
from src.common.schemas.signals import Direction, TechnicalSignal


@dataclass(frozen=True)
class StrategyDecision:
    """Outcome of a deterministic strategy evaluation."""

    strategy_name: str
    strategy_version: str
    symbol: str
    interval: CandleInterval
    direction: Direction
    target_weight: Decimal
    reasons: list[str] = field(default_factory=list)
    factor_scores: dict[str, Decimal] = field(default_factory=dict)
    indicators_used: dict[str, Decimal] = field(default_factory=dict)
    is_warmup: bool = False


class BaseStrategy(abc.ABC):
    """Abstract base class for all deterministic quantitative strategies."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @property
    @abc.abstractmethod
    def strategy_name(self) -> str:
        """Unique semantic identifier for the strategy."""
        ...

    @property
    @abc.abstractmethod
    def strategy_version(self) -> str:
        """Semantic version of the strategy logic."""
        ...

    @abc.abstractmethod
    def evaluate(
        self,
        candle: Candle,
        indicators: dict[str, Decimal],
        technical_signal: TechnicalSignal | None = None,
    ) -> StrategyDecision:
        """Deterministically evaluate market and indicator inputs to produce a decision."""
        ...


class EmaTrendFollowingStrategy(BaseStrategy):
    """Deterministic EMA Trend-Following Strategy.

    Rules:
    - Long Entry: EMA(9) > EMA(21) and Close > EMA(50).
    - Long Exit / Flat: EMA(9) < EMA(21) or Close < EMA(50).
    - Warmup Gate: Requires presence of ema_9, ema_21, and ema_50.
    """

    @property
    def strategy_name(self) -> str:
        return "ema_trend_following"

    @property
    def strategy_version(self) -> str:
        return str(self.config.get("strategy_version", "1.0.0"))

    def evaluate(
        self,
        candle: Candle,
        indicators: dict[str, Decimal],
        technical_signal: TechnicalSignal | None = None,
    ) -> StrategyDecision:
        ema_9 = indicators.get("ema_9")
        ema_21 = indicators.get("ema_21")
        ema_50 = indicators.get("ema_50")
        close = candle.close

        # Check warmup
        if ema_9 is None or ema_21 is None or ema_50 is None:
            return StrategyDecision(
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                reasons=["WARMING_UP: Missing required EMAs (ema_9, ema_21, or ema_50)"],
                is_warmup=True,
            )

        base_alloc = Decimal(str(self.config.get("base_allocation_pct", "0.10")))
        reasons: list[str] = []
        factor_scores: dict[str, Decimal] = {}

        # Trend momentum score: normalized distance between EMA 9 and 21
        spread = (ema_9 - ema_21) / ema_21
        factor_scores["ema_spread"] = spread

        if ema_9 > ema_21 and close > ema_50:
            reasons.append("BULLISH_TREND: EMA(9) > EMA(21) confirmed by Close > EMA(50)")
            direction = Direction.LONG
            weight = base_alloc
        else:
            if ema_9 <= ema_21:
                reasons.append("BEARISH_CROSS: EMA(9) <= EMA(21)")
            if close <= ema_50:
                reasons.append("BELOW_FILTER: Close <= EMA(50)")
            direction = Direction.FLAT
            weight = Decimal("0")

        return StrategyDecision(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            symbol=candle.symbol,
            interval=candle.interval,
            direction=direction,
            target_weight=weight,
            reasons=reasons,
            factor_scores=factor_scores,
            indicators_used={"ema_9": ema_9, "ema_21": ema_21, "ema_50": ema_50, "close": close},
            is_warmup=False,
        )


class RsiBollingerMeanReversionStrategy(BaseStrategy):
    """Deterministic RSI & Bollinger Bands Mean Reversion Strategy.

    Rules:
    - Long Entry: BB %B <= 0.15 (price near or below lower band) AND RSI(14) <= 35.
    - Long Exit / Flat: RSI(14) >= 50 or BB %B >= 0.50 (reverted to mean).
    - Warmup Gate: Requires bb_percent_b and rsi_14.
    """

    @property
    def strategy_name(self) -> str:
        return "rsi_bollinger_mean_reversion"

    @property
    def strategy_version(self) -> str:
        return str(self.config.get("strategy_version", "1.0.0"))

    def evaluate(
        self,
        candle: Candle,
        indicators: dict[str, Decimal],
        technical_signal: TechnicalSignal | None = None,
    ) -> StrategyDecision:
        pct_b = indicators.get("bb_percent_b")
        rsi_14 = indicators.get("rsi_14")

        if pct_b is None or rsi_14 is None:
            return StrategyDecision(
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                reasons=["WARMING_UP: Missing bb_percent_b or rsi_14"],
                is_warmup=True,
            )

        entry_pct_b = Decimal(str(self.config.get("bb_percent_b_entry", "0.15")))
        entry_rsi = Decimal(str(self.config.get("rsi_oversold_threshold", "35.0")))
        exit_rsi = Decimal(str(self.config.get("rsi_exit_threshold", "50.0")))
        base_alloc = Decimal(str(self.config.get("base_allocation_pct", "0.08")))

        reasons: list[str] = []
        factor_scores: dict[str, Decimal] = {
            "bb_percent_b": pct_b,
            "rsi_14": rsi_14,
        }

        if pct_b <= entry_pct_b and rsi_14 <= entry_rsi:
            reasons.append(
                f"OVERSOLD_REVERSION: BB %B ({pct_b:.2f}) <= {entry_pct_b} and "
                f"RSI ({rsi_14:.1f}) <= {entry_rsi}"
            )
            direction = Direction.LONG
            weight = base_alloc
        else:
            if rsi_14 >= exit_rsi:
                reasons.append(f"MEAN_REVERTED_RSI: RSI ({rsi_14:.1f}) >= {exit_rsi}")
            else:
                reasons.append("NEUTRAL_RSI_BB: Conditions not met for mean-reversion entry")
            direction = Direction.FLAT
            weight = Decimal("0")

        return StrategyDecision(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            symbol=candle.symbol,
            interval=candle.interval,
            direction=direction,
            target_weight=weight,
            reasons=reasons,
            factor_scores=factor_scores,
            indicators_used={"bb_percent_b": pct_b, "rsi_14": rsi_14},
            is_warmup=False,
        )


class VolatilityBreakoutStrategy(BaseStrategy):
    """Deterministic Volatility-Adjusted Breakout Strategy.

    Rules:
    - Long Entry: BB %B >= 0.90 (upper band breakout) AND ADX >= 25.0 AND Bandwidth >= 0.04.
    - Long Exit / Flat: BB %B < 0.70 or ADX < 20.0.
    - Warmup Gate: Requires bb_percent_b, bb_bandwidth, and adx.
    """

    @property
    def strategy_name(self) -> str:
        return "volatility_breakout"

    @property
    def strategy_version(self) -> str:
        return str(self.config.get("strategy_version", "1.0.0"))

    def evaluate(
        self,
        candle: Candle,
        indicators: dict[str, Decimal],
        technical_signal: TechnicalSignal | None = None,
    ) -> StrategyDecision:
        pct_b = indicators.get("bb_percent_b")
        bandwidth = indicators.get("bb_bandwidth")
        adx = indicators.get("adx")

        if pct_b is None or bandwidth is None or adx is None:
            return StrategyDecision(
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                symbol=candle.symbol,
                interval=candle.interval,
                direction=Direction.FLAT,
                target_weight=Decimal("0"),
                reasons=["WARMING_UP: Missing bb_percent_b, bb_bandwidth, or adx"],
                is_warmup=True,
            )

        trig_b = Decimal(str(self.config.get("bb_percent_b_trigger", "0.90")))
        min_bw = Decimal(str(self.config.get("bandwidth_min", "0.04")))
        min_adx = Decimal(str(self.config.get("adx_min", "25.0")))
        base_alloc = Decimal(str(self.config.get("base_allocation_pct", "0.06")))

        reasons: list[str] = []
        factor_scores: dict[str, Decimal] = {
            "bb_percent_b": pct_b,
            "bb_bandwidth": bandwidth,
            "adx": adx,
        }

        if pct_b >= trig_b and bandwidth >= min_bw and adx >= min_adx:
            reasons.append(
                f"EXPANSION_BREAKOUT: %B ({pct_b:.2f}) >= {trig_b}, "
                f"Bandwidth ({bandwidth:.4f}) >= {min_bw}, ADX ({adx:.1f}) >= {min_adx}"
            )
            direction = Direction.LONG
            weight = base_alloc
        else:
            if pct_b < trig_b:
                reasons.append(f"SUB_TRIGGER_B: %B ({pct_b:.2f}) < {trig_b}")
            if bandwidth < min_bw:
                reasons.append(f"LOW_BANDWIDTH: Bandwidth ({bandwidth:.4f}) < {min_bw}")
            if adx < min_adx:
                reasons.append(f"WEAK_TREND: ADX ({adx:.1f}) < {min_adx}")
            direction = Direction.FLAT
            weight = Decimal("0")

        return StrategyDecision(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            symbol=candle.symbol,
            interval=candle.interval,
            direction=direction,
            target_weight=weight,
            reasons=reasons,
            factor_scores=factor_scores,
            indicators_used={"bb_percent_b": pct_b, "bb_bandwidth": bandwidth, "adx": adx},
            is_warmup=False,
        )


class StrategyRegistry:
    """Registry managing available quantitative strategies."""

    def __init__(self, config_path: str | None = None) -> None:
        self._strategies: dict[str, BaseStrategy] = {}
        if config_path:
            self._load_from_path(config_path)

    def _load_from_path(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            strats = data.get("strategies", {})
            if "ema_trend_following" in strats:
                self.register(EmaTrendFollowingStrategy(strats["ema_trend_following"]))
            if "rsi_bollinger_mean_reversion" in strats:
                self.register(
                    RsiBollingerMeanReversionStrategy(strats["rsi_bollinger_mean_reversion"])
                )
            if "volatility_breakout" in strats:
                self.register(VolatilityBreakoutStrategy(strats["volatility_breakout"]))
        except Exception:
            pass

    def register(self, strategy: BaseStrategy) -> None:
        self._strategies[strategy.strategy_name] = strategy

    def get(self, strategy_name: str) -> BaseStrategy | None:
        return self._strategies.get(strategy_name)

    def list_strategies(self) -> list[dict[str, Any]]:
        return [
            {
                "strategy_name": s.strategy_name,
                "strategy_version": s.strategy_version,
                "config_hash": s.config_hash,
                "config": s.config,
            }
            for s in self._strategies.values()
        ]
