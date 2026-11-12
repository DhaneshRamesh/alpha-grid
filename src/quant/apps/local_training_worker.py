"""Private, single-job exploratory training worker over pinned local Dhan bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import tempfile
import time
from datetime import UTC, datetime
from datetime import date
from pathlib import Path

from quant.apps.exploratory_dhan_timing import study

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalRequest, response_path, run_backfill, verify_saved_response
from quant.apps.live_feed_credentials import load_credentials
from quant.apps.live_feed_scope import valid_authorization
from quant.acquisition.calendar import IST
from quant.apps.historical_simulator import simulate

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_START = date(2026, 10, 26)
ARCHIVE_END = date(2026, 10, 31)


def _cross_stock_archive(private: Path, ticker: str, identifier: str) -> tuple[dict, dict]:
    """Acquire the fixed five-day 1-minute slice and replay the frozen rule."""
    from quant.apps.cockpit_cache import MAC_EXPECTED_SHA256, MAC_REQUEST, MAC_ROOT

    universe = json.loads((ROOT / "docs/cross-stock-universe-2026-11-02.json").read_text())
    rows = universe["instruments"]
    selected = rows if ticker == "ALL_25" else [row for row in rows if row["symbol"] == ticker]
    if not selected:
        raise ValueError("ticker outside frozen universe")
    archive = private / "historical-25-oct26-30"
    bars_by_symbol = {}
    sources = {}
    missing = [row for row in selected if row["symbol"] != "RELIANCE"]
    token = None
    if missing:
        valid_authorization(ROOT, private, datetime.now(IST))
        token, _client, _expiry = load_credentials(ROOT)
    for index, row in enumerate(selected, 1):
        symbol = row["symbol"]
        if symbol == "RELIANCE":
            audit = verify_saved_response(MAC_REQUEST, response_path(MAC_REQUEST, MAC_ROOT))
            if audit.sha256 != MAC_EXPECTED_SHA256:
                raise ValueError("pinned RELIANCE archive checksum mismatch")
        else:
            valid_authorization(ROOT, private, datetime.now(IST))
            token, _client, _expiry = load_credentials(ROOT)
            instrument = CashInstrument("NSE", "NSE_EQ", row["security_id"], symbol, row["isin"])
            request = HistoricalRequest(instrument, ARCHIVE_START, ARCHIVE_END, 1)
            run_backfill((request,), output_dir=archive, enabled=True, token=token,
                         minimum_interval_seconds=1, max_retries=0)
            audit = verify_saved_response(request, response_path(request, archive))
        if not audit.bars:
            raise ValueError(f"no genuine Dhan bars for {symbol}")
        bars_by_symbol[symbol] = audit.bars
        sources[symbol] = {"sha256": audit.sha256, "bars": len(audit.bars),
                           "sessions": len({bar.trading_date for bar in audit.bars}),
                           "retrieved_at": audit.retrieved_at.isoformat() if audit.retrieved_at else None}
        atomic_json(private / "training-status.json", {"id": identifier, "ticker": ticker,
                    "state": "RUNNING", "completed_instruments": index,
                    "total_instruments": len(selected), "planned_new_requests": len(missing),
                    "updated_at": datetime.now(UTC).isoformat()})
    report = simulate(bars_by_symbol, universe)
    return {"schema_version": "alpha-grid-cross-stock-exploratory-job-v1",
            "training_job_id": identifier, "ticker": ticker,
            "historical_identity": "UNVERIFIED_FOR_FULL_RANGE",
            "timing": "CANDLE_AVAILABILITY_UNVERIFIED",
            "source_integrity": "VERIFIED_ORIGINAL_DHAN_BYTES",
            "source": "EXPLORATORY_ARCHIVE", "source_responses": sources,
            "fitted_model": None, "frozen_rule_simulation": report,
            "promotion_eligibility": "BLOCKED"}, sources


def atomic_json(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run_job(private: Path, job: dict) -> dict:
    identifier = job.get("id")
    ticker = job.get("ticker")
    if (
        not isinstance(identifier, str)
        or not identifier.isascii()
        or not identifier.isalnum()
    ):
        raise ValueError("invalid job ID")
    if ticker != "RELIANCE":
        artifact = private / "training-artifacts" / f"{identifier}.json"
        artifact.parent.mkdir(mode=0o700, exist_ok=True)
        if artifact.exists():
            raw = artifact.read_bytes()
            result = json.loads(raw)
            if result.get("training_job_id") != identifier:
                raise ValueError("training artifact integrity invalid")
            sources = result["source_responses"]
        else:
            result, sources = _cross_stock_archive(private, ticker, identifier)
            raw = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
            temporary = artifact.with_name(f"{identifier}.{os.getpid()}.tmp")
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, artifact)
            finally:
                temporary.unlink()
        digest = hashlib.sha256(raw).hexdigest()
        if ticker == "ALL_25":
            atomic_json(private / "cross-stock-simulation.latest.json",
                        {"schema_version": "alpha-grid-cross-stock-simulation-pointer-v1",
                         "artifact_name": artifact.name, "sha256": digest})
        return {"id": identifier, "ticker": ticker, "state": "COMPLETE",
                "artifact": str(artifact), "sha256": digest,
                "source_responses": sources, "research_eligibility": "EXPLORATORY_ONLY",
                "job_kind": "DHAN_ACQUISITION_AND_FROZEN_RULE_REPLAY",
                "fitted_model": False, "promotion_eligibility": "BLOCKED"}
    artifact = private / "training-artifacts" / f"{identifier}.json"
    artifact.parent.mkdir(mode=0o700, exist_ok=True)
    if artifact.exists():
        raw = artifact.read_bytes()
        saved = json.loads(raw)
        if (
            saved.get("training_job_id") != identifier
            or saved.get("promotion_eligibility") != "BLOCKED"
        ):
            raise ValueError("training artifact integrity invalid")
    else:

        def timed_out(*_: object) -> None:
            raise TimeoutError("training time limit")

        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, 120)
        try:
            result = study()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        result["training_job_id"] = identifier
        result["promotion_eligibility"] = "BLOCKED"
        raw = (
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        temporary = artifact.with_name(f"{identifier}.{os.getpid()}.tmp")
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, artifact)
        finally:
            temporary.unlink()
    return {
        "id": identifier,
        "ticker": ticker,
        "state": "COMPLETE",
        "artifact": str(artifact),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "research_eligibility": "EXPLORATORY_ONLY",
        "promotion_eligibility": "BLOCKED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_dir
    if private.is_symlink() or private.stat().st_mode & 0o077:
        raise PermissionError("private training directory required")
    request = private / "training-request.json"
    active = private / "training-active.json"
    status = private / "training-status.json"
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not status.exists():
        atomic_json(
            status, {"state": "IDLE", "updated_at": datetime.now(UTC).isoformat()}
        )
    while not stopping and not (private / "stop-requested").exists():
        if not active.exists() and request.exists():
            os.replace(request, active)
        if active.exists():
            job = json.loads(active.read_text())
            atomic_json(
                status,
                {
                    "id": job.get("id"),
                    "ticker": job.get("ticker"),
                    "state": "RUNNING",
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            try:
                result = run_job(private, job)
            except Exception as exc:
                result = {
                    "id": job.get("id"),
                    "ticker": job.get("ticker"),
                    "state": "FAILED",
                    "reason": type(exc).__name__,
                }
            result["updated_at"] = datetime.now(UTC).isoformat()
            atomic_json(status, result)
            active.unlink()
        else:
            current = json.loads(status.read_text())
            current["updated_at"] = datetime.now(UTC).isoformat()
            atomic_json(status, current)
        time.sleep(1)


if __name__ == "__main__":
    main()
