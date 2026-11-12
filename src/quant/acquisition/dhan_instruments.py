"""Offline identity check against a dated, checksummed Dhan instrument master."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.nse import file_sha256

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


@dataclass(frozen=True, slots=True)
class MasterIdentity:
    instrument: CashInstrument
    name: str
    master_sha256: str
    retrieved_at: datetime


def verify_master_identity(
    master: Path,
    manifest_path: Path,
    *,
    symbol: str,
    security_id: str,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=7),
) -> MasterIdentity:
    """Verify current listing evidence; historical ID continuity remains unknown."""
    return verify_master_identities(
        master, manifest_path, [(symbol, security_id)], now=now, max_age=max_age
    )[0]


def verify_master_identities(
    master: Path,
    manifest_path: Path,
    symbols_and_ids: list[tuple[str, str]],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=7),
) -> list[MasterIdentity]:
    """Verify a fixed universe with one checksum and one original-master scan."""
    expected = dict(symbols_and_ids)
    if not expected or len(expected) != len(symbols_and_ids):
        raise ValueError("duplicate or empty Dhan instrument scope")
    metadata = json.loads(manifest_path.read_text())
    if (
        metadata.get("schema_version") != "dhan-instrument-master-v1"
        or metadata.get("source_url") != MASTER_URL
        or metadata.get("sha256") != file_sha256(master)
    ):
        raise ValueError("Dhan instrument master provenance mismatch")
    retrieved_at = datetime.fromisoformat(metadata["retrieved_at"])
    current = now or datetime.now(UTC)
    if (
        retrieved_at.tzinfo is None
        or current.tzinfo is None
        or max_age <= timedelta(0)
        or not timedelta(0) <= current - retrieved_at <= max_age
    ):
        raise ValueError("Dhan instrument master is stale or undated")
    with master.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "SEM_EXM_EXCH_ID",
            "SEM_SEGMENT",
            "SEM_SMST_SECURITY_ID",
            "SEM_INSTRUMENT_NAME",
            "SEM_TRADING_SYMBOL",
            "SEM_SERIES",
            "SM_SYMBOL_NAME",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("Dhan compact instrument master schema mismatch")
        matches: dict[str, list[dict[str, str]]] = {symbol: [] for symbol in expected}
        for row in reader:
            symbol = row["SEM_TRADING_SYMBOL"]
            if (symbol in expected and row["SEM_EXM_EXCH_ID"] == "NSE"
                    and row["SEM_SEGMENT"] == "E"
                    and row["SEM_INSTRUMENT_NAME"] == "EQUITY"
                    and row["SEM_SERIES"] == "EQ"):
                matches[symbol].append(row)
    result = []
    for symbol, security_id in symbols_and_ids:
        matching = matches[symbol]
        if len(matching) != 1 or matching[0]["SEM_SMST_SECURITY_ID"] != security_id:
            raise ValueError("NSE equity symbol/security ID not uniquely confirmed")
        result.append(MasterIdentity(
            CashInstrument("NSE", "NSE_EQ", security_id, symbol),
            matching[0]["SM_SYMBOL_NAME"], metadata["sha256"], retrieved_at,
        ))
    return result
