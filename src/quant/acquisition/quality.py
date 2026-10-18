"""Conservative offline quality audit; missing data is never silently filled."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from quant.acquisition.calendar import NseCalendar
from quant.acquisition.contracts import HistoricalBar
from quant.acquisition.registry import InstrumentRegistry


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    unexplained_gap_fraction: float = 0.2
    tick_nanos: int = 10_000_000  # configurable; not presumed valid for all dates

    def __post_init__(self) -> None:
        if self.unexplained_gap_fraction <= 0 or self.tick_nanos <= 0:
            raise ValueError("quality thresholds must be positive")


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    security_id: str
    day: date
    detail: str
    severity: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    rows: int
    independent_sessions: int
    missing_bars: int
    issues: tuple[QualityIssue, ...]

    @property
    def hard_failures(self) -> int:
        return sum(issue.severity == "ERROR" for issue in self.issues)


DEFAULT_THRESHOLDS = QualityThresholds()


def audit_bars(
    bars: Iterable[HistoricalBar],
    *,
    calendar: NseCalendar,
    registry: InstrumentRegistry,
    universe: tuple[str, ...],
    start: date,
    end: date,
    interval_minutes: int | None,
    missing_evidence: Mapping[tuple[str, date], str] | None = None,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Audit a date-range partition; caller streams partitions for multi-year data."""
    issues: list[QualityIssue] = []
    observed: dict[tuple[str, date], set[int]] = {}
    previous: dict[str, HistoricalBar] = {}
    unknown_semantics: set[tuple[str, date]] = set()
    count = 0
    for bar in bars:
        count += 1
        sid, day = bar.instrument.security_id, bar.trading_date
        if sid not in universe or not start <= day < end:
            issues.append(
                QualityIssue(
                    "OUTSIDE_UNIVERSE_OR_RANGE", sid, day, "bar excluded", "ERROR"
                )
            )
            continue
        try:
            identity = registry.by_security_id(sid, day)
            if identity.instrument != bar.instrument:
                raise ValueError("identity mismatch")
        except ValueError:
            issues.append(
                QualityIssue("IDENTITY_UNVERIFIED", sid, day, "as-of mapping", "ERROR")
            )
        session = calendar.session(day)
        if session is None:
            issues.append(
                QualityIssue("NON_SESSION_BAR", sid, day, "no session", "ERROR")
            )
            continue
        stamp = (
            bar.source_timestamp_ns if interval_minutes is not None else day.toordinal()
        )
        if bar.interval_minutes != interval_minutes or stamp is None:
            issues.append(
                QualityIssue("INTERVAL_MISMATCH", sid, day, "wrong interval", "ERROR")
            )
            continue
        if interval_minutes is not None:
            if bar.timestamp_semantics != "BAR_START_VERIFIED":
                if (sid, day) not in unknown_semantics:
                    issues.append(
                        QualityIssue(
                            "TIMESTAMP_SEMANTICS_UNKNOWN",
                            sid,
                            day,
                            "cannot infer missing bars",
                            "WARNING",
                        )
                    )
                    unknown_semantics.add((sid, day))
            elif stamp not in session.expected_starts(interval_minutes):
                issues.append(
                    QualityIssue("OUTSIDE_SESSION", sid, day, "bar timestamp", "ERROR")
                )
        key = (sid, day)
        stamps = observed.setdefault(key, set())
        if stamp in stamps:
            issues.append(
                QualityIssue("DUPLICATE_TIMESTAMP", sid, day, str(stamp), "ERROR")
            )
        stamps.add(stamp)
        prior = previous.get(sid)
        if prior is not None:
            prior_stamp = (
                prior.source_timestamp_ns
                if interval_minutes is not None
                else prior.trading_date.toordinal()
            )
            if prior_stamp is not None and stamp <= prior_stamp:
                issues.append(
                    QualityIssue("TIMESTAMP_REGRESSION", sid, day, str(stamp), "ERROR")
                )
            if (
                prior.close_nanos
                and abs(bar.open_nanos / prior.close_nanos - 1)
                > thresholds.unexplained_gap_fraction
            ):
                code = (
                    "CORPORATE_ACTION_GAP"
                    if registry.corporate_action_on(sid, day)
                    else "UNEXPLAINED_PRICE_GAP"
                )
                issues.append(
                    QualityIssue(
                        code, sid, day, "open versus previous close", "WARNING"
                    )
                )
        previous[sid] = bar
        if any(
            price % thresholds.tick_nanos
            for price in (
                bar.open_nanos,
                bar.high_nanos,
                bar.low_nanos,
                bar.close_nanos,
            )
        ):
            issues.append(
                QualityIssue(
                    "PRICE_PRECISION", sid, day, "off configured tick grid", "WARNING"
                )
            )
    missing = 0
    for sid in universe:
        for session in calendar.sessions(start, end):
            day = session.day
            if not session.verified:
                issues.append(
                    QualityIssue(
                        "CALENDAR_APPLICABILITY_UNVERIFIED",
                        sid,
                        day,
                        "session schedule is provisional",
                        "ERROR",
                    )
                )
            try:
                registry.by_security_id(sid, day)
            except ValueError:
                issues.append(
                    QualityIssue(
                        "IDENTITY_UNVERIFIED", sid, day, "coverage unknown", "WARNING"
                    )
                )
            key = (sid, day)
            if key not in observed:
                classification = (missing_evidence or {}).get(key, "UNKNOWN")
                if classification not in {
                    "UNKNOWN",
                    "PROVIDER_OUTAGE",
                    "CONFIRMED_INACTIVITY",
                }:
                    raise ValueError("unsupported missing-data evidence")
                issues.append(
                    QualityIssue("MISSING_SESSION", sid, day, classification, "WARNING")
                )
            elif interval_minutes is not None:
                stamps = observed[key]
                expected_count = len(session.expected_starts(interval_minutes))
                # A count shortfall is observable even when exact bar-start
                # timestamps are not independently verified.
                if key in unknown_semantics and len(stamps) < expected_count:
                    issues.append(
                        QualityIssue(
                            "SESSION_COUNT_SHORTFALL",
                            sid,
                            day,
                            f"observed {len(stamps)} of {expected_count}; "
                            "exact missing timestamps unknown",
                            "ERROR",
                        )
                    )
                elif key not in unknown_semantics:
                    expected = set(session.expected_starts(interval_minutes))
                    gap_count = len(expected - stamps)
                    missing += gap_count
                    if gap_count:
                        issues.append(
                            QualityIssue(
                                "MISSING_BARS", sid, day, str(gap_count), "ERROR"
                            )
                        )
    # Instruments observed on one trading day are one independent session.
    return QualityReport(
        count, len({day for _, day in observed}), missing, tuple(issues)
    )


def reconcile_daily(left: HistoricalBar, right: HistoricalBar) -> tuple[str, ...]:
    if left.interval_minutes is not None or right.interval_minutes is not None:
        raise ValueError("daily overlap required")
    if (
        left.instrument.security_id != right.instrument.security_id
        or left.trading_date != right.trading_date
    ):
        raise ValueError("same instrument/date required")
    names = ("open_nanos", "high_nanos", "low_nanos", "close_nanos", "volume")
    return tuple(name for name in names if getattr(left, name) != getattr(right, name))
