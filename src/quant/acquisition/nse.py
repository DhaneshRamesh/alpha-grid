"""Local-only NSE CM archive importer. No downloader or scraping path."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from quant.acquisition.contracts import PRICE_SCALE, CashInstrument, HistoricalBar

IdentityResolver = Callable[[str, str | None, date], CashInstrument]


@dataclass(frozen=True, slots=True)
class ArchiveSource:
    path: Path
    format_version: str
    source_url: str
    downloaded_at: datetime

    def __post_init__(self) -> None:
        if self.format_version not in {"UDIFF_CM", "LEGACY_CM"}:
            raise ValueError("unsupported NSE CM archive format")
        if not self.source_url.startswith("https://"):
            raise ValueError("original HTTPS source URL is required")
        if self.downloaded_at.tzinfo is None:
            raise ValueError("download time must be timezone-aware")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


_FIELDS = {
    "UDIFF_CM": {
        "symbol": "TckrSymb",
        "isin": "ISIN",
        "series": "SctySrs",
        "date": "TradDt",
        "open": "OpnPric",
        "high": "HghPric",
        "low": "LwPric",
        "close": "ClsPric",
        "volume": "TtlTradgVol",
        "turnover": "TtlTrfVal",
        "delivery": "DlvryQty",
    },
    "LEGACY_CM": {
        "symbol": "SYMBOL",
        "isin": "ISIN",
        "series": "SERIES",
        "date": "TIMESTAMP",
        "open": "OPEN",
        "high": "HIGH",
        "low": "LOW",
        "close": "CLOSE",
        "volume": "TOTTRDQTY",
        "turnover": "TOTTRDVAL",
        "delivery": "DELIV_QTY",
    },
}


def _date(text: str) -> date:
    for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError("invalid NSE archive trading date")


def _nanos(text: str) -> int:
    value = Decimal(text.strip()) * PRICE_SCALE
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError("NSE archive price exceeds nanounit precision")
    return int(value)


def _rows(
    stream: TextIO,
    source: ArchiveSource,
    checksum: str,
    resolve: IdentityResolver,
    selected_symbols: frozenset[str] | None,
) -> Iterator[HistoricalBar]:
    fields = _FIELDS[source.format_version]
    reader = csv.DictReader(stream)
    required = {
        fields[key]
        for key in (
            "symbol",
            "series",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("NSE archive schema does not match declared format")
    for row in reader:
        if row[fields["series"]].strip() != "EQ":
            continue
        if (
            selected_symbols is not None
            and row[fields["symbol"]].strip() not in selected_symbols
        ):
            continue
        trading_date = _date(row[fields["date"]])
        symbol = row[fields["symbol"]].strip()
        isin = row.get(fields["isin"]) or None
        instrument = resolve(symbol, isin, trading_date)
        if instrument.symbol != symbol or (isin and instrument.isin != isin):
            raise ValueError("point-in-time instrument mapping mismatch")
        turnover_text = row.get(fields["turnover"])
        delivery_text = row.get(fields["delivery"])
        yield HistoricalBar(
            instrument=instrument,
            interval_minutes=None,
            trading_date=trading_date,
            source_timestamp_ns=None,
            open_nanos=_nanos(row[fields["open"]]),
            high_nanos=_nanos(row[fields["high"]]),
            low_nanos=_nanos(row[fields["low"]]),
            close_nanos=_nanos(row[fields["close"]]),
            volume=int(row[fields["volume"]]),
            source="NSE_" + source.format_version,
            source_sha256=checksum,
            timestamp_semantics="DAILY",
            turnover_nanos=_nanos(turnover_text) if turnover_text else None,
            delivery_quantity=int(delivery_text) if delivery_text else None,
        )


def import_archive(
    source: ArchiveSource,
    resolve: IdentityResolver,
    *,
    selected_symbols: frozenset[str] | None = None,
) -> Iterator[HistoricalBar]:
    """Stream one local CSV or one-member ZIP; reject ambiguous ZIP contents."""
    checksum = file_sha256(source.path)
    if source.path.suffix.lower() == ".zip":
        with zipfile.ZipFile(source.path) as archive:
            members = [
                item
                for item in archive.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".csv")
            ]
            if len(members) != 1 or members[0].file_size > 256 * 1024 * 1024:
                raise ValueError("NSE archive requires one bounded CSV member")
            with (
                archive.open(members[0]) as raw,
                io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream,
            ):
                yield from _rows(stream, source, checksum, resolve, selected_symbols)
    elif source.path.suffix.lower() == ".csv":
        with source.path.open(encoding="utf-8-sig", newline="") as stream:
            yield from _rows(stream, source, checksum, resolve, selected_symbols)
    else:
        raise ValueError("NSE archive must be local CSV or ZIP")


def local_source(
    path: Path,
    *,
    format_version: str,
    source_url: str,
    downloaded_at: datetime,
) -> ArchiveSource:
    """The caller supplies the actual download time; file mtime is not evidence."""
    return ArchiveSource(path, format_version, source_url, downloaded_at)
