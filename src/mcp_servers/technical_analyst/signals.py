"""Deterministic, rule-based explainable technical signal generator.

Evaluates incremental indicator snapshots against deterministic rules defined in
config/technical_rules.json (versioned, default 1.0.0).

Non-LLM invariants:
- Confidence score is bounded in [0, 1] and represents the rule-agreement fraction:
  (agreed_rules / total_fired_rules). It is NOT a calibrated probability of profit.
- Each TechnicalSignal carries the full indicators dictionary and the exact list
  of rules_fired that justified the direction.
- MarketRegime is classified deterministically from ADX trend strength and Bollinger Bandwidth:
  - TRENDING_UP: ADX >= 25 and Close > EMA(50)
  - TRENDING_DOWN: ADX >= 25 and Close < EMA(50)
  - HIGH_VOL_CHOP: ADX < 25 and Bandwidth > 0.05
  - MEAN_REVERTING: ADX < 25 and Bandwidth <= 0.05
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.common.schemas.market import Candle, CandleInterval, DataProvenance, FeedMode
from src.common.schemas.signals import Direction, MarketRegime, TechnicalSignal

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_RULES_PATH = REPO_ROOT / "config" / "technical_rules.json"


def merge_provenance(provenances: list[DataProvenance]) -> DataProvenance:
    """Merge provenance across input bars according to ADR-006 rules:

    1. Feed mode: least trustworthy wins: SIMULATED > REPLAY > LIVE.
    2. is_delayed: logical OR (if any feed is delayed, signal is marked delayed).
    3. delay_seconds: maximum observed delay.
    4. provider: if multiple providers present, set to 'mixed'.
    5. ingested_at: latest ingestion timestamp.
    """
    if not provenances:
        raise ValueError("Cannot merge empty provenances list")

    modes = [p.feed_mode for p in provenances]
    if FeedMode.SIMULATED in modes:
        effective_mode = FeedMode.SIMULATED
    elif FeedMode.REPLAY in modes:
        effective_mode = FeedMode.REPLAY
    else:
        effective_mode = FeedMode.LIVE

    is_delayed = any(p.is_delayed for p in provenances)
    max_delay = max(p.delay_seconds or 0 for p in provenances)

    providers = {p.provider for p in provenances}
    effective_provider = providers.pop() if len(providers) == 1 else "mixed"
    valid_ingested = [p.ingested_at for p in provenances if p.ingested_at is not None]
    latest_ingested = max(valid_ingested) if valid_ingested else datetime.now(UTC)

    return DataProvenance(
        provider=effective_provider,
        feed_mode=effective_mode,
        is_delayed=is_delayed,
        delay_seconds=max_delay,
        ingested_at=latest_ingested,
    )


class SignalEngine:
    """Deterministic, explainable rule-based signal generator."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or DEFAULT_RULES_PATH
        self.config = self._load_config()
        self.config_version = str(self.config.get("version", "1.0.0"))
        import hashlib

        self.config_hash = hashlib.sha256(
            json.dumps(self.config, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"version": "1.0.0", "indicators": {}, "rules": []}
        with open(self.config_path, encoding="utf-8") as f:
            data: Any = json.load(f)
            return dict(data) if isinstance(data, dict) else {}

    def classify_regime(
        self,
        close: Decimal,
        ema_50: Decimal | None,
        adx: Decimal | None,
        bandwidth: Decimal | None,
    ) -> MarketRegime:
        """Deterministically classify volatility and trend regime."""
        adx_thresh = Decimal(
            str(self.config.get("indicators", {}).get("adx_trend_threshold", 25.0))
        )
        bw_chop_thresh = Decimal("0.05")

        if adx is not None and adx >= adx_thresh and ema_50 is not None:
            if close >= ema_50:
                return MarketRegime.TRENDING_UP
            return MarketRegime.TRENDING_DOWN

        if bandwidth is not None and bandwidth > bw_chop_thresh:
            return MarketRegime.HIGH_VOL_CHOP

        return MarketRegime.MEAN_REVERTING

    def evaluate(
        self,
        symbol: str,
        interval: CandleInterval,
        candle: Candle,
        indicators: dict[str, Decimal],
        provenance: DataProvenance,
    ) -> TechnicalSignal | None:
        """Evaluate deterministic rules against current indicator state.

        Returns None if key indicators are still warming up.
        """
        # Essential indicators needed for signal generation
        required_keys = {"ema_9", "ema_21", "rsi_14"}
        if not required_keys.issubset(indicators.keys()):
            return None

        close = candle.close
        ema_9 = indicators.get("ema_9")
        ema_21 = indicators.get("ema_21")
        ema_50 = indicators.get("ema_50")
        rsi_14 = indicators.get("rsi_14")
        macd_line = indicators.get("macd_line")
        macd_sig = indicators.get("macd_signal")
        vwap = indicators.get("vwap")
        bb_percent_b = indicators.get("bb_percent_b")
        bb_bandwidth = indicators.get("bb_bandwidth")
        stoch_k = indicators.get("stoch_k")
        adx = indicators.get("adx")

        long_rules: list[str] = []
        short_rules: list[str] = []

        # 1. EMA 9 vs 21
        if ema_9 is not None and ema_21 is not None:
            if ema_9 > ema_21:
                long_rules.append("ema_9_above_ema_21")
            elif ema_9 < ema_21:
                short_rules.append("ema_9_below_ema_21")

        # 2. Price vs EMA 50
        if ema_50 is not None:
            if close > ema_50:
                long_rules.append("price_above_ema_50")
            elif close < ema_50:
                short_rules.append("price_below_ema_50")

        # 3. RSI
        if rsi_14 is not None:
            if Decimal("50") < rsi_14 < Decimal("70"):
                long_rules.append("rsi_bullish_momentum")
            elif Decimal("30") < rsi_14 < Decimal("50"):
                short_rules.append("rsi_bearish_momentum")
            elif rsi_14 <= Decimal("30"):
                long_rules.append("rsi_oversold_reversal")
            elif rsi_14 >= Decimal("70"):
                short_rules.append("rsi_overbought_reversal")

        # 4. MACD Line vs Signal
        if macd_line is not None and macd_sig is not None:
            if macd_line > macd_sig:
                long_rules.append("macd_bullish_cross")
            elif macd_line < macd_sig:
                short_rules.append("macd_bearish_cross")

        # 5. Price vs VWAP
        if vwap is not None:
            if close > vwap:
                long_rules.append("price_above_vwap")
            elif close < vwap:
                short_rules.append("price_below_vwap")

        # 6. Bollinger Bands %B
        if bb_percent_b is not None:
            if bb_percent_b <= Decimal("0.10"):
                long_rules.append("bb_lower_bounce")
            elif bb_percent_b >= Decimal("0.90"):
                short_rules.append("bb_upper_rejection")

        # 7. Stochastic %K
        if stoch_k is not None:
            if stoch_k <= Decimal("20"):
                long_rules.append("stoch_oversold")
            elif stoch_k >= Decimal("80"):
                short_rules.append("stoch_overbought")

        total_rules = len(long_rules) + len(short_rules)
        if total_rules == 0:
            # During warm-up or indeterminate market with zero rule agreement, publish nothing
            return None
        elif len(long_rules) > len(short_rules):
            direction = Direction.LONG
            rules_fired = long_rules
            rules_against = short_rules
            # Rule agreement score
            confidence = (Decimal(str(len(long_rules))) / Decimal(str(total_rules))).quantize(
                Decimal("0.01")
            )
        elif len(short_rules) > len(long_rules):
            direction = Direction.SHORT
            rules_fired = short_rules
            rules_against = long_rules
            confidence = (Decimal(str(len(short_rules))) / Decimal(str(total_rules))).quantize(
                Decimal("0.01")
            )
        else:
            direction = Direction.FLAT
            # On tied neutral signals: rules_fired is empty because neither side won.
            # rules_tied records all tied rules (long and short) with confidence = 0.50
            rules_fired = []
            rules_against = short_rules
            rules_tied = long_rules + short_rules
            confidence = Decimal("0.50")

        regime = self.classify_regime(
            close=close,
            ema_50=ema_50,
            adx=adx,
            bandwidth=bb_bandwidth,
        )

        return TechnicalSignal(
            signal_id=uuid4(),
            symbol=symbol,
            evaluated_at=candle.close_time,
            interval=interval,
            direction=direction,
            confidence=confidence,
            regime=regime,
            indicators=indicators,
            rules_fired=rules_fired,
            rules_against=rules_against,
            config_version=self.config_version,
            provenance=provenance,
        )
