from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quant.acquisition.lake import verify_dataset
from quant.acquisition.status import acquisition_status
from quant.apps.import_nse_archive import main


def test_local_archive_cli_is_offline_and_preserves_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "bhav.csv"
    archive.write_text(
        "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2025-01-02,RELIANCE,EQ,INE002A01018,100,101,99,100,500\n"
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": "test-master",
                "records": [
                    {
                        "instrument": {
                            "venue": "NSE",
                            "segment": "NSE_EQ",
                            "security_id": "1333",
                            "symbol": "RELIANCE",
                            "isin": "INE002A01018",
                        },
                        "name": "Example",
                        "effective_from": "2024-01-01",
                        "effective_to": None,
                        "listing_status": "LISTED",
                        "source_manifest_version": "master-2024",
                    }
                ],
            }
        )
    )
    universe = tmp_path / "universe.json"
    universe.write_text(
        json.dumps(
            {
                "version": "u1",
                "selection_asof": "2024-12-31",
                "security_ids": ["1333"],
                "selection_rule": "predeclared fixture",
            }
        )
    )
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps({"version": "test-calendar", "holidays": [], "special": []})
    )
    output = tmp_path / "dataset"
    args = [
        "quant-import-nse-archive",
        "--archive",
        str(archive),
        "--format",
        "UDIFF_CM",
        "--source-url",
        "https://www.nseindia.com/all-reports",
        "--downloaded-at",
        "2025-01-03T08:00:00+05:30",
        "--registry",
        str(registry),
        "--universe",
        str(universe),
        "--calendar",
        str(calendar),
        "--dataset-id",
        "fixture",
        "--code-commit",
        "fixture",
        "--output",
        str(output),
        "--raw-root",
        str(tmp_path / "raw"),
    ]
    monkeypatch.setattr(sys, "argv", args)
    main()
    manifest = verify_dataset(output)
    assert manifest.row_count == 1
    assert (
        manifest.source_provenance[0].source_url
        == "https://www.nseindia.com/all-reports"
    )
    status = json.loads((tmp_path / "fixture-status.json").read_text())
    assert status["access_status"] == "UNVERIFIED"
    assert status["checksum_verified"]
    assert status["source_files_available"] == 1
    assert status["genuine_source_files_available"] == 0
    assert status["training_blocked"] is True
    audit = json.loads((tmp_path / "fixture-quality.json").read_text())
    assert audit["source_sha256"] == manifest.source_checksums[0]
    assert audit["independent_sessions"] == 1
    staged = tmp_path / "raw" / "NSE_UDIFF_CM" / manifest.source_checksums[0]
    staged.unlink()
    missing_original = acquisition_status(dataset_dir=output, raw_root=tmp_path / "raw")
    assert missing_original["source_files_available"] == 0
    assert "original_source_file_missing" in missing_original["block_reasons"]
    main()  # interrupted sidecar publication can be resumed idempotently
    assert verify_dataset(output).dataset_sha256 == manifest.dataset_sha256
    archive.write_text(
        "TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2025-01-02,RELIANCE,EQ,INE002A01018,100,101,99,100,501\n"
    )
    with pytest.raises(ValueError, match="existing daily import differs"):
        main()
    assert len(list((tmp_path / "raw").rglob("*"))) >= 2
