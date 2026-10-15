"""Offline local NSE archive import. One daily file per invocation."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from quant.acquisition.calendar import load_calendar
from quant.acquisition.lake import (
    LakeManifest,
    RawProvenance,
    stage_raw_file,
    verify_dataset,
    write_dataset,
)
from quant.acquisition.nse import file_sha256, import_archive, local_source
from quant.acquisition.quality import audit_bars
from quant.acquisition.registry import load_registry, load_universe
from quant.acquisition.status import acquisition_status, write_json_atomic, write_status


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

    def publish(path: Path) -> LakeManifest:
        return write_dataset(
            bars,
            path,
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

    if args.output.exists():
        existing = verify_dataset(args.output)
        with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
            candidate = publish(Path(directory) / "candidate")
            if existing.dataset_sha256 != candidate.dataset_sha256:
                raise ValueError(
                    "existing daily import differs from current source or mapping"
                )
        manifest = existing
    else:
        manifest = publish(args.output)
    status = acquisition_status(
        dataset_dir=args.output, quality=report, raw_root=args.raw_root
    )
    status_path = args.output.parent / f"{args.dataset_id}-status.json"
    write_status(status_path, status)
    audit_path = args.output.parent / f"{args.dataset_id}-quality.json"
    write_json_atomic(
        audit_path,
        {
            "schema_version": "nse-quality-v1",
            "source_sha256": file_sha256(args.archive),
            "dataset_sha256": manifest.dataset_sha256,
            "rows": report.rows,
            "independent_sessions": report.independent_sessions,
            "missing_bars": report.missing_bars,
            "issues": [asdict(issue) for issue in report.issues],
        },
    )
    print(
        json.dumps(
            {"dataset_sha256": manifest.dataset_sha256, "status": str(status_path)},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
