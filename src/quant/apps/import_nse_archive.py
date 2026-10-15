"""Offline local NSE archive import. One daily file per invocation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from quant.acquisition.calendar import load_calendar
from quant.acquisition.lake import RawProvenance, stage_raw_file, write_dataset
from quant.acquisition.nse import file_sha256, import_archive, local_source
from quant.acquisition.quality import audit_bars
from quant.acquisition.registry import load_registry, load_universe
from quant.acquisition.status import acquisition_status, write_status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import one already-downloaded NSE CM archive"
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--format", choices=("UDIFF_CM", "LEGACY_CM"), required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--downloaded-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    source = local_source(
        args.archive,
        format_version=args.format,
        source_url=args.source_url,
        downloaded_at=args.downloaded_at,
    )
    registry = load_registry(args.registry)
    universe = load_universe(args.universe)
    calendar = load_calendar(args.calendar)
    selected_symbols = frozenset(
        record.instrument.symbol
        for record in registry.records
        if record.instrument.security_id in universe.security_ids
    )
    bars = list(
        import_archive(source, registry.resolve, selected_symbols=selected_symbols)
    )
    if not bars or len(bars) > 100_000:
        raise ValueError("one bounded daily archive with selected securities required")
    days = {bar.trading_date for bar in bars}
    if len(days) != 1:
        raise ValueError("archive must contain one trading date")
    day = next(iter(days))
    if universe.selection_asof > day:
        raise ValueError("universe selected after historical trading date")
    bars.sort(key=lambda bar: (bar.trading_date, bar.instrument.security_id))
    report = audit_bars(
        bars,
        calendar=calendar,
        registry=registry,
        universe=universe.security_ids,
        start=day,
        end=day + timedelta(days=1),
        interval_minutes=None,
    )
    if report.hard_failures:
        raise ValueError(f"quality audit rejected {report.hard_failures} hard failures")
    stage_raw_file(args.archive, args.raw_root, "NSE_" + args.format)
    manifest = write_dataset(
        bars,
        args.output,
        dataset_id=args.dataset_id,
        acquisition_mode="LOCAL_FILE",
        instrument_universe=universe.security_ids,
        calendar_version=calendar.version,
        adjustment_policy="UNADJUSTED",
        code_commit=args.code_commit,
        source_provenance=(
            RawProvenance(
                file_sha256(args.archive),
                source.source_url,
                source.downloaded_at.isoformat(),
                source.format_version,
            ),
        ),
    )
    status = acquisition_status(dataset_dir=args.output, quality=report)
    status_path = args.output.parent / f"{args.dataset_id}-status.json"
    write_status(status_path, status)
    print(
        json.dumps(
            {"dataset_sha256": manifest.dataset_sha256, "status": str(status_path)},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
