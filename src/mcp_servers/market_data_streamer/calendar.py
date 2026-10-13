"""NSE exchange calendar and market session evaluation engine."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from src.common.schemas.market import (
    NSE_MARKET_CLOSE,
    NSE_MARKET_OPEN,
    NSE_POST_CLOSE_END,
    NSE_PRE_OPEN_START,
    NSE_TIMEZONE,
    MarketSessionPhase,
    MarketStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_CALENDAR_PATH = REPO_ROOT / "config" / "nse_holidays_2026.json"


class MarketCalendar:
    """Evaluates NSE market status using configurable holiday calendar and IST operating hours."""

    def __init__(self, holidays_file: Path | str | None = None) -> None:
        self._path = Path(holidays_file) if holidays_file else DEFAULT_CALENDAR_PATH
        self._holidays: dict[date, str] = {}
        self._load_holidays()

    def _load_holidays(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("trading_holidays", []):
                self._holidays[date.fromisoformat(item["date"])] = item.get(
                    "description", "NSE Trading Holiday"
                )

    def is_holiday(self, check_date: date) -> bool:
        """Return True if the date is a weekend or listed in the NSE holiday calendar."""
        if check_date.weekday() >= 5:
            return True
        return check_date in self._holidays

    def evaluate_status(self, dt: datetime | None = None) -> MarketStatus:
        """Compute the current MarketStatus given a UTC or IST datetime."""
        now_utc = dt.astimezone(UTC) if dt else datetime.now(UTC)
        ist_dt = now_utc.astimezone(NSE_TIMEZONE)
        ist_date = ist_dt.date()
        ist_time = ist_dt.timetz().replace(tzinfo=None)

        if self.is_holiday(ist_date):
            phase = MarketSessionPhase.CLOSED
            if ist_date.weekday() >= 5:
                reason = "Weekend"
            else:
                reason = self._holidays.get(ist_date, "NSE Trading Holiday")
        elif NSE_PRE_OPEN_START <= ist_time < NSE_MARKET_OPEN:
            phase = MarketSessionPhase.PRE_OPEN
            reason = "Pre-open order collection and discovery session (09:00 - 09:15 IST)"
        elif NSE_MARKET_OPEN <= ist_time < NSE_MARKET_CLOSE:
            phase = MarketSessionPhase.OPEN
            reason = "Continuous regular trading session (09:15 - 15:30 IST)"
        elif NSE_MARKET_CLOSE <= ist_time < NSE_POST_CLOSE_END:
            phase = MarketSessionPhase.POST_CLOSE
            reason = "Post-market closing price calculation session (15:30 - 16:00 IST)"
        else:
            phase = MarketSessionPhase.CLOSED
            reason = "Outside regular trading hours (closed until next session)"

        return MarketStatus(
            as_of=now_utc,
            is_open=(phase == MarketSessionPhase.OPEN),
            session=phase,
            reason=reason,
        )
