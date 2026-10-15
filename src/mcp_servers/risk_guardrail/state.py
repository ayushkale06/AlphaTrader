"""Atomic Redis-backed risk state manager with Lua transitions.

Invariants:
1. Monotonic High-Water Mark (HWM) advancement.
2. Stale and duplicate event rejection based on monotonic timestamp/sequence.
3. Drawdown breach (>= 2.0%) atomically latches alphatrader:floor_halted = "1".
4. Latch persistence: date rollover or restart NEVER clears alphatrader:floor_halted.
5. Fail-closed: missing/unreachable Redis state returns unavailable/halted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import redis

from src.mcp_servers.risk_guardrail.evaluator import PortfolioState

logger = logging.getLogger("alphatrader.risk_guardrail.state")

LUA_UPDATE_RISK_STATE = """
-- KEYS[1]: alphatrader:risk:equity
-- KEYS[2]: alphatrader:risk:hwm
-- KEYS[3]: alphatrader:risk:sod_equity
-- KEYS[4]: alphatrader:risk:session_date
-- KEYS[5]: alphatrader:risk:last_event_ts
-- KEYS[6]: alphatrader:floor_halted
-- ARGV[1]: new_equity (string)
-- ARGV[2]: event_ts (string / float)
-- ARGV[3]: session_date (string YYYY-MM-DD)
-- ARGV[4]: max_drawdown_pct (string / float, e.g. 2.0)

local new_equity = tonumber(ARGV[1])
local event_ts = tonumber(ARGV[2])
local session_date = ARGV[3]
local max_dd_pct = tonumber(ARGV[4])

-- 1. Check event freshness (stale/out-of-order rejection)
local last_ts_str = redis.call('GET', KEYS[5])
if last_ts_str then
    local last_ts = tonumber(last_ts_str)
    if event_ts <= last_ts then
        -- Stale or duplicate event: reject mutation without altering state
        return {0, "STALE_OR_DUPLICATE_EVENT"}
    end
end

-- 2. Check and latch floor halt if already halted
local current_date = redis.call('GET', KEYS[4])
if not current_date or current_date ~= session_date then
    redis.call('SET', KEYS[4], session_date)
end

local is_halted = redis.call('GET', KEYS[6])
if is_halted == "1" then
    -- Already halted; update equity and timestamp but never unlatch
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('SET', KEYS[5], ARGV[2])
    return {1, "ALREADY_HALTED"}
end

-- 3. Check Session Date Rollover
local current_date = redis.call('GET', KEYS[4])
local sod_equity = redis.call('GET', KEYS[3])

if not current_date or current_date ~= session_date then
    -- New trading day: set SOD baseline to new_equity and update date
    redis.call('SET', KEYS[4], session_date)
    redis.call('SET', KEYS[3], ARGV[1])
    sod_equity = ARGV[1]
end

-- 4. Monotonic High-Water Mark update
local current_hwm_str = redis.call('GET', KEYS[2])
local hwm = new_equity
if current_hwm_str then
    local old_hwm = tonumber(current_hwm_str)
    if old_hwm > hwm then
        hwm = old_hwm
    end
end
redis.call('SET', KEYS[2], tostring(hwm))

-- 5. Calculate Drawdown against HWM
local drawdown_pct = 0.0
if hwm > 0 then
    local dd_val = hwm - new_equity
    if dd_val > 0 then
        drawdown_pct = (dd_val / hwm) * 100.0
    end
end

-- 6. Check Drawdown Breach
if drawdown_pct >= max_dd_pct then
    -- Latch floor circuit breaker!
    redis.call('SET', KEYS[6], "1")
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('SET', KEYS[5], ARGV[2])
    return {2, "DRAWDOWN_BREACH_HALTED"}
end

-- 7. Persist updated equity and event timestamp
redis.call('SET', KEYS[1], ARGV[1])
redis.call('SET', KEYS[5], ARGV[2])

return {1, "SUCCESS"}
"""


@dataclass(frozen=True)
class RiskTelemetrySnapshot:
    """Coherent telemetry snapshot read from Redis."""

    equity: Decimal
    high_water_mark: Decimal
    start_of_day_equity: Decimal
    current_drawdown_pct: Decimal
    is_floor_halted: bool
    session_date: str
    open_positions_count: int


class RiskStateManager:
    """Manages real-time risk state in Redis with atomic Lua scripting."""

    KEY_EQUITY = "alphatrader:risk:equity"
    KEY_HWM = "alphatrader:risk:hwm"
    KEY_SOD_EQUITY = "alphatrader:risk:sod_equity"
    KEY_SESSION_DATE = "alphatrader:risk:session_date"
    KEY_LAST_EVENT_TS = "alphatrader:risk:last_event_ts"
    KEY_POSITIONS = "alphatrader:risk:positions"
    KEY_FLOOR_HALTED = "alphatrader:floor_halted"

    def __init__(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        market_timezone: str = "Asia/Kolkata",
        max_daily_drawdown_pct: Decimal = Decimal("2.0"),
    ) -> None:
        self.redis = redis_client
        self.tz = ZoneInfo(market_timezone)
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self._lua_update = self.redis.register_script(LUA_UPDATE_RISK_STATE)

    def get_current_session_date(self, dt: datetime | None = None) -> str:
        """Return YYYY-MM-DD in configured market timezone."""
        target_dt = dt or datetime.now(UTC)
        return target_dt.astimezone(self.tz).strftime("%Y-%m-%d")

    def initialize_state(
        self,
        initial_equity: Decimal,
        session_date: str | None = None,
        force_unhalt: bool = False,
    ) -> None:
        """Initialize baseline risk state if absent."""
        s_date = session_date or self.get_current_session_date()
        eq_str = str(initial_equity)

        pipe = self.redis.pipeline()
        pipe.setnx(self.KEY_EQUITY, eq_str)
        pipe.setnx(self.KEY_HWM, eq_str)
        pipe.setnx(self.KEY_SOD_EQUITY, eq_str)
        pipe.setnx(self.KEY_SESSION_DATE, s_date)
        if force_unhalt:
            pipe.set(self.KEY_FLOOR_HALTED, "0")
        else:
            pipe.setnx(self.KEY_FLOOR_HALTED, "0")
        pipe.execute()

    def update_portfolio_equity(
        self,
        new_equity: Decimal,
        event_time: datetime,
    ) -> tuple[bool, str]:
        """Atomically update portfolio equity, HWM, and evaluate drawdown breach."""
        if new_equity <= Decimal("0"):
            logger.error("Rejected invalid non-positive portfolio equity: %s", new_equity)
            return False, "INVALID_EQUITY"

        event_ts = event_time.timestamp()
        s_date = self.get_current_session_date(event_time)

        keys = [
            self.KEY_EQUITY,
            self.KEY_HWM,
            self.KEY_SOD_EQUITY,
            self.KEY_SESSION_DATE,
            self.KEY_LAST_EVENT_TS,
            self.KEY_FLOOR_HALTED,
        ]
        args = [
            str(new_equity),
            str(event_ts),
            s_date,
            str(self.max_daily_drawdown_pct),
        ]

        try:
            res = self._lua_update(keys=keys, args=args)
            code, msg = res[0], res[1].decode("utf-8") if isinstance(res[1], bytes) else str(res[1])
            return (code > 0), msg
        except Exception as e:
            logger.exception("Failed to execute atomic risk state update: %s", e)
            return False, f"REDIS_ERROR: {e}"

    def update_open_position(self, symbol: str, notional_inr: Decimal) -> None:
        """Update notional exposure for a constituent symbol."""
        if notional_inr <= Decimal("0"):
            self.redis.hdel(self.KEY_POSITIONS, symbol)
        else:
            self.redis.hset(self.KEY_POSITIONS, symbol, str(notional_inr))

    def get_portfolio_state_for_evaluation(self) -> PortfolioState:
        """Fetch coherent state required by DeterministicRiskEvaluator.

        Fails closed: if keys missing or Redis down, returns floor halted state.
        """
        try:
            pipe = self.redis.pipeline()
            pipe.get(self.KEY_EQUITY)
            pipe.get(self.KEY_HWM)
            pipe.get(self.KEY_FLOOR_HALTED)
            pipe.hgetall(self.KEY_POSITIONS)
            eq_val, hwm_val, halted_val, pos_dict = pipe.execute()

            if not eq_val or not hwm_val:
                logger.warning("Risk state missing in Redis. Failing closed to halted state.")
                return PortfolioState(
                    equity=Decimal("0"),
                    cash=Decimal("0"),
                    high_water_mark=Decimal("0"),
                    open_positions={},
                    is_floor_halted=True,
                )

            is_halted = halted_val == b"1" or halted_val == "1"
            positions = {
                (k.decode("utf-8") if isinstance(k, bytes) else k): Decimal(
                    v.decode("utf-8") if isinstance(v, bytes) else v
                )
                for k, v in pos_dict.items()
            }

            eq = Decimal(eq_val.decode("utf-8") if isinstance(eq_val, bytes) else eq_val)
            hwm = Decimal(hwm_val.decode("utf-8") if isinstance(hwm_val, bytes) else hwm_val)

            return PortfolioState(
                equity=eq,
                cash=eq,  # cash conservative assumption
                high_water_mark=hwm,
                open_positions=positions,
                is_floor_halted=is_halted,
            )
        except Exception as e:
            logger.error("Exception fetching risk state from Redis, failing closed: %s", e)
            return PortfolioState(
                equity=Decimal("0"),
                cash=Decimal("0"),
                high_water_mark=Decimal("0"),
                open_positions={},
                is_floor_halted=True,
            )

    def trigger_floor_halt(self) -> bool:
        """Atomically latch alphatrader:floor_halted = '1'."""
        try:
            self.redis.set(self.KEY_FLOOR_HALTED, "1")
            return True
        except Exception as e:
            logger.error("Failed to set floor halted latch in Redis: %s", e)
            return False

    def get_telemetry(self) -> RiskTelemetrySnapshot:
        """Fetch read-only operational telemetry."""
        pipe = self.redis.pipeline()
        pipe.get(self.KEY_EQUITY)
        pipe.get(self.KEY_HWM)
        pipe.get(self.KEY_SOD_EQUITY)
        pipe.get(self.KEY_FLOOR_HALTED)
        pipe.get(self.KEY_SESSION_DATE)
        pipe.hlen(self.KEY_POSITIONS)
        eq_val, hwm_val, sod_val, halted_val, date_val, pos_count = pipe.execute()

        def to_dec(val: bytes | str | None) -> Decimal:
            if not val:
                return Decimal("0")
            return Decimal(val.decode("utf-8") if isinstance(val, bytes) else val)

        eq = to_dec(eq_val)
        hwm = to_dec(hwm_val)
        sod = to_dec(sod_val)
        is_halted = halted_val == b"1" or halted_val == "1"
        s_date = (
            (date_val.decode("utf-8") if isinstance(date_val, bytes) else str(date_val))
            if date_val
            else "UNKNOWN"
        )

        dd_pct = Decimal("0")
        if hwm > Decimal("0") and eq < hwm:
            dd_pct = ((hwm - eq) / hwm * Decimal("100")).quantize(Decimal("0.01"))

        return RiskTelemetrySnapshot(
            equity=eq,
            high_water_mark=hwm,
            start_of_day_equity=sod,
            current_drawdown_pct=dd_pct,
            is_floor_halted=is_halted,
            session_date=s_date,
            open_positions_count=int(pos_count or 0),
        )
