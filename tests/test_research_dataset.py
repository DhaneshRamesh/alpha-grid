from __future__ import annotations

import gzip
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from quant.research.dataset import build_dataset
from quant.research.io import read_paper
from quant.research.labels import PriceObservation, label_path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs/benchmarks/phase3a-isolated-2026-10-15/run1/risk_filtered.qp.gz"


def _paper_frame(value: dict[str, Any]) -> bytes:
    body = json.dumps(value, separators=(",", ":")).encode()
    return b"QP01" + struct.pack("<II", len(body), zlib.crc32(body)) + body


def _sample_decision() -> dict[str, Any]:
    return next(read_paper(FIXTURE))


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    paper = _sample_decision()
    context = paper["event"]["Decision"]["context"]
    index = paper["source_record_index"]
    instrument = context["instrument"]
    stamp = context["source_receive_timestamp_ns"]
    market = tmp_path / "market.jsonl"
    rows = []
    for n in range(index):
        rows.append(
            {
                "source_record_index": n + 1,
                "record": {
                    "session_id": context["session_id"],
                    "event": {
                        "Depth": {
                            "instrument": instrument,
                            "receive_timestamp_ns": stamp,
                        }
                    },
                },
            }
        )
    rows.append(
        {
            "source_record_index": index + 1,
            "record": {
                "session_id": context["session_id"],
                "event": {
                    "Price": {
                        "instrument": instrument,
                        "price_nanos": 100_050_000_000,
                        "metadata": {"receive_timestamp_ns": stamp + 1_000_000_000},
                    }
                },
            },
        }
    )
    market.write_text("".join(json.dumps(row) + "\n" for row in rows))
    ledger = tmp_path / "paper.qp"
    ledger.write_bytes(_paper_frame(paper))
    return market, ledger


def test_dataset_roundtrip_lineage_and_future_isolation(tmp_path: Path) -> None:
    market, ledger = _sources(tmp_path)
    first = build_dataset(
        market_jsonl=market,
        paper_qp=ledger,
        output_dir=tmp_path / "first",
        horizons_seconds=(5,),
        max_gap_seconds=5,
        source_kind="SYNTHETIC",
    )
    second = build_dataset(
        market_jsonl=market,
        paper_qp=ledger,
        output_dir=tmp_path / "second",
        horizons_seconds=(5,),
        max_gap_seconds=5,
        source_kind="SYNTHETIC",
    )
    assert first == second
    assert first.independent_sessions == 1
    assert first.status == "NOT_TRAINED"
    feature = pq.read_table(tmp_path / "first/features.parquet").to_pylist()[0]
    label = pq.read_table(tmp_path / "first/labels.parquet").to_pylist()[0]
    assert feature["price_nanos"] == 100_000_000_000
    assert feature["behavior_action_probability"] is None
    assert label["forward_return"] is None  # recording ends before horizon
    assert label["right_censored"]
    assert feature["source_record_index"] == label["source_record_index"]
    assert not any("forward" in column for column in feature)


def test_future_snapshot_rejected(tmp_path: Path) -> None:
    market, ledger = _sources(tmp_path)
    record = _sample_decision()
    context = record["event"]["Decision"]["context"]
    context["features"]["processing_timestamp_ns"] += 1
    ledger.write_bytes(_paper_frame(record))
    with pytest.raises(ValueError, match="future features"):
        build_dataset(
            market_jsonl=market,
            paper_qp=ledger,
            output_dir=tmp_path / "dataset",
        )


def test_qp01_integrity_and_historical_gzip() -> None:
    assert next(read_paper(FIXTURE))["schema_version"] == 1
    with gzip.open(FIXTURE, "rb") as stream:
        frame = stream.read(12)
        payload = stream.read(struct.unpack("<I", frame[4:8])[0])
    bad = frame + payload[:-1]
    path = Path("/tmp/phase3b-corrupt.qp")
    try:
        path.write_bytes(bad)
        with pytest.raises(ValueError, match="corrupt"):
            list(read_paper(path))
    finally:
        path.unlink(missing_ok=True)


def test_labels_first_hit_gap_and_no_cross_session() -> None:
    path = [
        PriceObservation("A", "I", 2, 1_000_000_000, 110, "Price"),
        PriceObservation("A", "I", 3, 2_000_000_000, 90, "Price"),
    ]
    result = label_path(
        asof_index=1,
        asof_ns=0,
        entry_nanos=100,
        observations=path,
        horizon_ns=3_000_000_000,
        profit_barrier_nanos=5,
        loss_barrier_nanos=5,
        max_gap_ns=2_000_000_000,
    )
    assert result.first_event == "FAVORABLE"
    assert result.mfe_nanos == 10 and result.mae_nanos == -10
    assert result.available
    missing = label_path(
        asof_index=1,
        asof_ns=0,
        entry_nanos=100,
        observations=[path[1]],
        horizon_ns=3_000_000_000,
        profit_barrier_nanos=5,
        loss_barrier_nanos=5,
        max_gap_ns=1_000_000_000,
    )
    assert missing.censored and missing.censor_reason == "observation_gap"


def test_same_receive_time_conflicting_barriers_are_ambiguous() -> None:
    path = [
        PriceObservation("A", "I", 2, 1, 110, "Price"),
        PriceObservation("A", "I", 3, 1, 90, "Price"),
    ]
    result = label_path(
        asof_index=1,
        asof_ns=0,
        entry_nanos=100,
        observations=path,
        horizon_ns=10,
        profit_barrier_nanos=5,
        loss_barrier_nanos=5,
        max_gap_ns=10,
    )
    assert result.ambiguous and not result.available
