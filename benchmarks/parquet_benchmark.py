"""Small local conversion benchmark; no broker account or network required."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quant.domain import DepthEvent, DepthLevel
from quant.storage.journal import EventJournal, FsyncPolicy, JournalSettings
from quant.storage.parquet import ParquetSettings, export_journal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=2_000)
    parser.add_argument("--instruments", type=int, default=20)
    args = parser.parse_args()
    if args.events < 1 or not 1 <= args.instruments <= 50:
        parser.error("events must be positive; instruments must be 1 to 50")
    levels = tuple(
        DepthLevel(Decimal("100.05") + Decimal(index) / 20, 100 + index, 5)
        for index in range(20)
    )
    started = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        journal_settings = JournalSettings(
            root / "journal", "benchmark", FsyncPolicy.NEVER
        )
        with EventJournal(journal_settings) as journal:
            for index in range(args.events):
                journal.append(
                    DepthEvent(
                        "NSE",
                        "NSE_EQ",
                        str(1000 + index % args.instruments),
                        None,
                        started + timedelta(milliseconds=index),
                        levels,
                        levels,
                        index,
                    )
                )
        result = export_journal(ParquetSettings(journal_settings, root / "research"))
        print(
            json.dumps(
                {
                    "events": result.depth_events,
                    "instruments": args.instruments,
                    "parquet_files": result.parquet_files,
                    "events_per_second": round(result.events_per_second, 1),
                    "compressed_bytes_per_event": round(
                        result.compressed_bytes_per_event, 1
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
