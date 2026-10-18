"""Operator-controlled Dhan historical workflow; only pilot may use the network."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quant.acquisition.calendar import load_calendar
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.dhan import (
    HistoricalRequest,
    SavedResponseAudit,
    dry_run,
    plan_requests,
    response_path,
    run_backfill,
    verify_saved_response,
)
from quant.acquisition.dhan_instruments import verify_master_identity
from quant.acquisition.lake import (
    RawProvenance,
    stage_raw_file,
    verify_dataset,
    write_dataset,
)
from quant.acquisition.quality import QualityReport, audit_bars
from quant.acquisition.registry import load_registry
from quant.acquisition.status import acquisition_status, write_json_atomic, write_status

_COMMANDS = ("dry-run", "verify-config", "pilot", "verify", "import", "report")
_IST = ZoneInfo("Asia/Kolkata")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DATA_ONLY Dhan historical charts")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in _COMMANDS:
        command = commands.add_parser(name)
        command.add_argument("--security-id", action="append", required=True)
        command.add_argument("--symbol", action="append", required=True)
        command.add_argument("--start", type=date.fromisoformat, required=True)
        command.add_argument("--end", type=date.fromisoformat, required=True)
        command.add_argument("--interval", type=int, default=1)
        command.add_argument("--daily", action="store_true")
        command.add_argument(
            "--output-dir", type=Path, default=Path("data/historical/raw")
        )
        command.add_argument("--master", type=Path)
        command.add_argument("--master-manifest", type=Path)
        command.add_argument("--calendar", type=Path)
        command.add_argument("--registry", type=Path)
        command.add_argument(
            "--raw-root", type=Path, default=Path("data/historical/source")
        )
        command.add_argument(
            "--output", type=Path, default=Path("data/historical/lake/pilot")
        )
        command.add_argument("--dataset-id", default="dhan-historical-pilot")
        command.add_argument("--code-commit")
        if name == "pilot":
            command.add_argument("--subscription-confirmed", action="store_true")
            command.add_argument("--pilot-authorized", action="store_true")
            command.add_argument("--execute-read-only", action="store_true")
    return parser


def _identity(args: argparse.Namespace) -> dict[str, object]:
    if len(args.security_id) != 1 or len(args.symbol) != 1:
        raise ValueError("verified pilot requires exactly one NSE cash equity")
    if args.master is None or args.master_manifest is None:
        raise ValueError("dated Dhan instrument master and manifest required")
    evidence = verify_master_identity(
        args.master,
        args.master_manifest,
        symbol=args.symbol[0],
        security_id=args.security_id[0],
    )
    return {
        "venue": "NSE",
        "segment": "NSE_EQ",
        "security_id": evidence.instrument.security_id,
        "symbol": evidence.instrument.symbol,
        "name": evidence.name,
        "master_sha256": evidence.master_sha256,
        "master_retrieved_at": evidence.retrieved_at.isoformat(),
        "historical_identity": "UNVERIFIED",
    }


def _requests(args: argparse.Namespace) -> tuple[HistoricalRequest, ...]:
    if len(args.security_id) != len(args.symbol):
        raise ValueError("one symbol required for each security ID")
    registry = load_registry(args.registry) if args.registry is not None else None
    instruments = []
    for sid, symbol in zip(args.security_id, args.symbol, strict=True):
        if registry is None:
            instruments.append(CashInstrument("NSE", "NSE_EQ", sid, symbol))
        else:
            instrument = registry.by_security_id(sid, args.start).instrument
            if instrument.symbol != symbol:
                raise ValueError("as-of registry symbol mismatch")
            instruments.append(instrument)
    return plan_requests(
        tuple(instruments), args.start, args.end, None if args.daily else args.interval
    )


def _saved(
    args: argparse.Namespace, requests: tuple[HistoricalRequest, ...]
) -> tuple[SavedResponseAudit, ...]:
    return tuple(
        verify_saved_response(request, response_path(request, args.output_dir))
        for request in requests
    )


def _quality(args: argparse.Namespace, bars: list[HistoricalBar]) -> QualityReport:
    if args.calendar is None or args.registry is None:
        raise ValueError("independent calendar and as-of registry files required")
    return audit_bars(
        bars,
        calendar=load_calendar(args.calendar),
        registry=load_registry(args.registry),
        universe=tuple(args.security_id),
        start=args.start,
        end=args.end,
        interval_minutes=None if args.daily else args.interval,
    )


def _validate_pilot_scope(
    args: argparse.Namespace, requests: tuple[HistoricalRequest, ...]
) -> None:
    if (
        len(requests) != 1
        or args.daily
        or args.interval != 1
        or (args.end - args.start) > timedelta(days=7)
        or args.end > datetime.now(_IST).date()
    ):
        raise ValueError("pilot must be one completed week of one-minute equity bars")


def run(argv: list[str]) -> dict[str, object]:
    """Expose a testable command path. Legacy options still mean dry-run."""
    if argv and argv[0].startswith("--"):
        argv = ["dry-run", *argv]
    args = _parser().parse_args(argv)
    requests = _requests(args)
    if args.command == "dry-run":
        result = dry_run(requests, args.output_dir)
        result.update(
            method="POST",
            data_api_policy="at most 1 request/second; Dhan documents 5/second",
            historical_identity="UNVERIFIED",
        )
        if args.master is not None or args.master_manifest is not None:
            result["instrument_evidence"] = _identity(args)
        return result
    if args.command == "verify-config":
        _validate_pilot_scope(args, requests)
        return {
            "mode": "VERIFY_CONFIG_NO_NETWORK",
            "instrument_evidence": _identity(args),
            "request_count": len(requests),
            "token_present": bool(os.environ.get("DHAN_ACCESS_TOKEN")),
            "subscription_status": "UNVERIFIED_OFFLINE",
            "authenticated_pilot": "NOT_AUTHORIZED",
        }
    if args.command == "pilot":
        identity = _identity(args)
        if not (
            args.subscription_confirmed
            and args.pilot_authorized
            and args.execute_read_only
        ):
            raise PermissionError(
                "pilot requires confirmed subscription, explicit authorization "
                "and read-only execution opt-in"
            )
        _validate_pilot_scope(args, requests)
        token = os.environ.get("DHAN_ACCESS_TOKEN")
        if not token:
            raise ValueError("DHAN_ACCESS_TOKEN must be set locally")
        paths = run_backfill(
            requests, output_dir=args.output_dir, enabled=True, token=token
        )
        audits = _saved(args, requests)
        return {
            "mode": "READ_ONLY_HISTORICAL_PILOT",
            "instrument_evidence": identity,
            "saved_response_count": len(paths),
            "saved_responses": [str(path) for path in paths],
            "response_sha256": [audit.sha256 for audit in audits],
            "rows": sum(len(audit.bars) for audit in audits),
            "retrieval_times_recorded": all(
                audit.retrieved_at is not None for audit in audits
            ),
            "orders": "DISABLED",
        }
    if args.command == "report":
        if not args.output.exists():
            return {
                "mode": "DATA_ONLY",
                "subscription_status": "UNVERIFIED_OFFLINE",
                "authenticated_pilot": "NOT_AUTHORIZED",
                "genuine_historical_records": 0,
                "independent_sessions": 0,
                "research_eligibility": "NOT_TRAINED",
            }
        manifest = verify_dataset(args.output)
        quality_path = args.output.parent / f"{args.dataset_id}-quality.json"
        quality = json.loads(quality_path.read_text()) if quality_path.exists() else {}
        sessions = (
            quality.get("independent_sessions", 0)
            if quality.get("dataset_sha256") == manifest.dataset_sha256
            else 0
        )
        return {
            "mode": "DATA_ONLY",
            "dataset_sha256": manifest.dataset_sha256,
            "historical_records_in_lake": manifest.row_count,
            "independent_sessions": sessions,
            "source_verification_status": "UNVERIFIED",
            "timestamp_semantics": "UNVERIFIED",
            "research_eligibility": "NOT_TRAINED",
            "status": acquisition_status(
                dataset_dir=args.output,
                raw_root=args.raw_root,
                configured_sources=("DHAN_HISTORICAL_DISABLED",),
            ),
        }
    identity = _identity(args)
    audits = _saved(args, requests)
    bars = [bar for audit in audits for bar in audit.bars]
    report = _quality(args, bars)
    summary: dict[str, object] = {
        "mode": "VERIFY_SAVED_NO_NETWORK",
        "instrument_evidence": identity,
        "response_sha256": [audit.sha256 for audit in audits],
        "retrieval_times_recorded": all(
            audit.retrieved_at is not None for audit in audits
        ),
        "rows": report.rows,
        "independent_sessions": report.independent_sessions,
        "hard_failures": report.hard_failures,
        "quality_issues": [asdict(issue) for issue in report.issues],
        "timestamp_semantics": "UNVERIFIED",
        "calendar_completeness": "UNVERIFIED",
        "adjustment_policy": "UNVERIFIED",
        "historical_identity": "UNVERIFIED",
        "identity_in_response": "NOT_PRESENT",
        "interval_in_response": "NOT_PRESENT",
    }
    if args.command == "verify":
        return summary
    if report.hard_failures:
        raise ValueError("quality hard failures block Dhan Parquet publication")
    if not all(audit.retrieved_at is not None for audit in audits):
        raise ValueError("original retrieval timestamps required for import")
    if not bars or args.code_commit is None:
        raise ValueError("nonempty saved bars and code commit required")
    provenance = tuple(
        RawProvenance(
            audit.sha256,
            requests[index].endpoint,
            audit.retrieved_at.isoformat() if audit.retrieved_at else "",
            "DHAN_CHARTS_V2",
        )
        for index, audit in enumerate(audits)
    )
    for audit in audits:
        stage_raw_file(audit.path, args.raw_root, "DHAN_HISTORICAL")
    if args.output.exists():
        raise ValueError("immutable output already exists; choose a new dataset ID")
    manifest = write_dataset(
        bars,
        args.output,
        dataset_id=args.dataset_id,
        acquisition_mode="DHAN_SAVED_RESPONSE",
        instrument_universe=tuple(args.security_id),
        calendar_version=load_calendar(args.calendar).version,
        adjustment_policy="UNVERIFIED",
        code_commit=args.code_commit,
        source_provenance=provenance,
    )
    verify_dataset(args.output)
    status = acquisition_status(
        dataset_dir=args.output,
        quality=report,
        raw_root=args.raw_root,
        configured_sources=("DHAN_HISTORICAL_DISABLED",),
    )
    write_status(args.output.parent / f"{args.dataset_id}-status.json", status)
    write_json_atomic(
        args.output.parent / f"{args.dataset_id}-quality.json",
        {
            "schema_version": "dhan-quality-v1",
            "dataset_sha256": manifest.dataset_sha256,
            "source_checksums": manifest.source_checksums,
            "rows": report.rows,
            "independent_sessions": report.independent_sessions,
            "hard_failures": report.hard_failures,
            "issues": [asdict(issue) for issue in report.issues],
        },
    )
    summary.update(
        mode="IMPORT_SAVED_NO_NETWORK", dataset_sha256=manifest.dataset_sha256
    )
    return summary


def main() -> None:
    print(json.dumps(run(sys.argv[1:]), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
