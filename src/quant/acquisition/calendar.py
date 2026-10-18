"""Injectable cash session calendar; holidays must come from a versioned source."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class Session:
    day: date
    opens: time
    closes: time
    kind: str = "REGULAR"
    verified: bool = True

    def __post_init__(self) -> None:
        if self.closes <= self.opens:
            raise ValueError("session close must follow open")

    def start_ns(self) -> int:
        return (
            int(datetime.combine(self.day, self.opens, IST).timestamp()) * 1_000_000_000
        )

    def end_ns(self) -> int:
        return (
            int(datetime.combine(self.day, self.closes, IST).timestamp())
            * 1_000_000_000
        )

    def expected_starts(self, interval_minutes: int) -> tuple[int, ...]:
        if interval_minutes <= 0:
            raise ValueError("interval must be positive")
        step = interval_minutes * 60 * 1_000_000_000
        return tuple(range(self.start_ns(), self.end_ns(), step))


@dataclass(frozen=True, slots=True)
class NseCalendar:
    version: str
    holidays: frozenset[date] = frozenset()
    special: dict[date, Session] = field(default_factory=dict)
    # Weekday schedule is provisional until holidays/special sessions are supplied.
    normal_open: time = time(9, 15)
    normal_close: time = time(15, 30)
    scope_start: date | None = None
    scope_end: date | None = None

    def __post_init__(self) -> None:
        if (self.scope_start is None) != (self.scope_end is None) or (
            self.scope_start is not None
            and self.scope_end is not None
            and self.scope_start >= self.scope_end
        ):
            raise ValueError("calendar scope is invalid")
        if not self.version or any(
            day != session.day for day, session in self.special.items()
        ):
            raise ValueError("calendar version or special-session date invalid")

    def session(self, day: date) -> Session | None:
        if self.scope_start is not None and self.scope_end is not None:
            if not self.scope_start <= day < self.scope_end:
                raise ValueError("date is outside evidenced calendar scope")
        if day in self.special:
            return self.special[day]
        if day.weekday() >= 5 or day in self.holidays:
            return None
        return Session(day, self.normal_open, self.normal_close)

    def sessions(self, start: date, end: date) -> tuple[Session, ...]:
        result = []
        cursor = start
        while cursor < end:
            session = self.session(cursor)
            if session is not None:
                result.append(session)
            cursor += timedelta(days=1)
        return tuple(result)


def utc_date_from_ns(timestamp_ns: int) -> date:
    return (
        datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).astimezone(IST).date()
    )


def load_calendar(path: Path) -> NseCalendar:
    data: dict[str, Any] = json.loads(path.read_text())
    scope = data.get("scope")
    special = {
        date.fromisoformat(item["day"]): Session(
            date.fromisoformat(item["day"]),
            time.fromisoformat(item["opens"]),
            time.fromisoformat(item["closes"]),
            item.get("kind", "SPECIAL"),
            item.get("verified", True),
        )
        for item in data.get("special", [])
    }
    return NseCalendar(
        data["version"],
        frozenset(date.fromisoformat(value) for value in data.get("holidays", [])),
        special,
        time.fromisoformat(data.get("normal_open", "09:15:00")),
        time.fromisoformat(data.get("normal_close", "15:30:00")),
        date.fromisoformat(scope["start"]) if scope is not None else None,
        date.fromisoformat(scope["end"]) if scope is not None else None,
    )
