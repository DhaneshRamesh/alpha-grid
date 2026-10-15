"""Point-in-time NSE identity. Never resolve a historical date from today's master."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from quant.acquisition.contracts import CashInstrument


@dataclass(frozen=True, slots=True)
class CorporateAction:
    effective_date: date
    kind: str
    source_reference: str


@dataclass(frozen=True, slots=True)
class MappingRecord:
    instrument: CashInstrument
    name: str
    effective_from: date
    effective_to: date | None  # exclusive
    listing_status: str
    source_manifest_version: str
    corporate_actions: tuple[CorporateAction, ...] = ()

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("invalid effective date range")
        if self.listing_status not in {"LISTED", "DELISTED", "UNKNOWN"}:
            raise ValueError("invalid listing status")
        if not self.source_manifest_version:
            raise ValueError("mapping source manifest required")

    def effective_on(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day < self.effective_to
        )


@dataclass(frozen=True, slots=True)
class UniverseVersion:
    version: str
    selection_asof: date
    security_ids: tuple[str, ...]
    selection_rule: str

    def __post_init__(self) -> None:
        if (
            not self.version
            or not self.selection_rule
            or len(set(self.security_ids)) != len(self.security_ids)
        ):
            raise ValueError("universe needs unique IDs, version and predeclared rule")


class InstrumentRegistry:
    def __init__(self, records: tuple[MappingRecord, ...], version: str) -> None:
        if not version:
            raise ValueError("registry version required")
        self.records = records
        self.version = version

    def resolve(self, symbol: str, isin: str | None, asof: date) -> CashInstrument:
        candidates = [
            record
            for record in self.records
            if record.effective_on(asof)
            and record.instrument.symbol == symbol
            and (isin is None or record.instrument.isin == isin)
        ]
        if len(candidates) != 1:
            raise ValueError("historical instrument identity missing or ambiguous")
        record = candidates[0]
        if record.listing_status != "LISTED":
            raise ValueError("instrument not proven listed on historical date")
        return record.instrument

    def by_security_id(self, security_id: str, asof: date) -> MappingRecord:
        candidates = [
            record
            for record in self.records
            if record.effective_on(asof)
            and record.instrument.security_id == security_id
        ]
        if len(candidates) != 1:
            raise ValueError("historical security ID missing or ambiguous")
        if candidates[0].listing_status != "LISTED":
            raise ValueError("security not proven listed on historical date")
        return candidates[0]

    def corporate_action_on(self, security_id: str, day: date) -> bool:
        return any(
            action.effective_date == day
            for record in self.records
            if record.instrument.security_id == security_id
            for action in record.corporate_actions
        )
