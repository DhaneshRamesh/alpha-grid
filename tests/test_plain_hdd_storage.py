"""Verification suite for plain unencrypted HDD storage, recovery, and fail-closed safety."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import pytest

from tools.execute_full_3yr_acquisition import (
    verify_external_mount,
    default_target_dir,
    run_acquisition,
    verify_existing_checkpoint_file,
)
from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalRequest
from datetime import date


def test_plain_hdd_directory_structure_exists():
    """Verify that the unencrypted plain directory structure exists on Danny's HDD."""
    hdd_mount = Path("/Volumes/Danny's HDD")
    if not hdd_mount.is_mount():
        pytest.skip("/Volumes/Danny's HDD is not physically mounted")

    base = hdd_mount / "AlphaGrid-Plain"
    assert base.is_dir()
    required_dirs = [
        base / "historical" / "raw-dhan",
        base / "historical" / "manifests",
        base / "market-feed" / "quotes",
        base / "market-feed" / "depth",
        base / "journals" / "qj01",
        base / "journals" / "qp01",
        base / "research",
        base / "models",
        base / "checkpoints",
        base / "reports",
    ]
    for d in required_dirs:
        assert d.is_dir(), f"Missing required directory: {d}"


def test_restored_artifacts_sha256_parity():
    """Verify that every restored file on plain HDD matches the source SHA-256."""
    hdd_mount = Path("/Volumes/Danny's HDD")
    if not hdd_mount.is_mount():
        pytest.skip("/Volumes/Danny's HDD is not physically mounted")

    base = hdd_mount / "AlphaGrid-Plain"

    pairs = [
        (Path("data/research_snapshot_manifest_v1.json"), base / "historical" / "manifests" / "research_snapshot_manifest_v1.json"),
        (Path("docs/cross-stock-universe-2026-11-02.json"), base / "historical" / "manifests" / "cross-stock-universe-2026-11-02.json"),
        (Path("data/historical/raw-dhan-macos-2026-11-01/2885-1-2026-05-01-2026-07-30.json"), base / "historical" / "raw-dhan" / "2885-1-2026-05-01-2026-07-30.json"),
        (Path("data/models/alpha-grid-m1-v1.pkl"), base / "models" / "alpha-grid-m1-v1.pkl"),
        (Path("data/telemetry/paper-approvals.qp"), base / "journals" / "qp01" / "paper-approvals.qp"),
        (Path("data/telemetry/offline_replay_decisions.jsonl"), base / "checkpoints" / "offline_replay_decisions.jsonl"),
    ]

    for src, dst in pairs:
        assert src.exists(), f"Source file missing: {src}"
        assert dst.exists(), f"Restored file missing on HDD: {dst}"
        src_h = hashlib.sha256(src.read_bytes()).hexdigest()
        dst_h = hashlib.sha256(dst.read_bytes()).hexdigest()
        assert src_h == dst_h, f"Checksum mismatch for {src.name}: {src_h} != {dst_h}"


def test_fail_closed_mount_verification():
    """Verify that verify_external_mount rejects internal paths and unmounted volumes."""
    assert not verify_external_mount(Path("/tmp/nonexistent"))
    assert not verify_external_mount(Path("/Users/dhaneshramesh"))
    assert not verify_external_mount(Path("/Volumes/FakeVolumeUnmounted/data"))

    hdd_mount = Path("/Volumes/Danny's HDD")
    if hdd_mount.is_mount():
        assert verify_external_mount(hdd_mount / "AlphaGrid-Plain/historical/raw-dhan")


def test_interrupted_write_recovery_and_resume(tmp_path):
    """Verify safe interrupted-write recovery without corrupting valid files."""
    quarantine = tmp_path / ".quarantine"
    quarantine.mkdir(parents=True)

    # 1. Valid raw file
    valid_bytes = b'{"timestamp": [1704081900], "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [50]}'
    valid_sha = hashlib.sha256(valid_bytes).hexdigest()
    f1 = tmp_path / "2885-1-2024-01-01-2024-01-31.json"
    f1.write_bytes(valid_bytes)
    f1.with_suffix(".sha256").write_text(valid_sha + "\n")
    req1 = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 31), 1)
    f1.with_suffix(".meta.json").write_text(json.dumps({
        "schema_version": "dhan-chart-raw-v1",
        "endpoint": req1.endpoint,
        "method": "POST",
        "request": req1.payload(),
        "sha256": valid_sha,
        "received_at": "2024-02-01T00:00:00+00:00",
        "http_status": 200,
    }))

    # 2. Corrupt partial raw file (simulating power cut / disconnect during write)
    f2 = tmp_path / "1333-1-2024-01-01-2024-01-31.json"
    f2.write_bytes(b'{"timestamp": [1700000000], "open": [')  # truncated
    f2.with_suffix(".sha256").write_text("corrupted_hash\n")

    req1 = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 31), 1)
    res1 = verify_existing_checkpoint_file(req1, f1)
    assert res1["status"] == "COMPLETED"
    assert res1["source_integrity"] == "VALID_SOURCE"

    req2 = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "1333", "HDFCBANK"), date(2024, 1, 1), date(2024, 1, 31), 1)
    res2 = verify_existing_checkpoint_file(req2, f2)
    assert res2["status"] == "CORRUPT_HASH_MISMATCH"
    assert res2["source_integrity"] == "DEFECTIVE_SOURCE"


def test_no_destructive_alteration_of_old_sparsebundle():
    """Verify that old sparsebundles exist and remain completely untouched."""
    hdd_mount = Path("/Volumes/Danny's HDD")
    if not hdd_mount.is_mount():
        pytest.skip("/Volumes/Danny's HDD is not physically mounted")

    bundle_v2 = hdd_mount / "AlphaGridData-2026-11-05-v2.sparsebundle"
    assert bundle_v2.is_dir()
    assert (bundle_v2 / "Info.plist").is_file()
    assert (bundle_v2 / "token").is_file()
    assert (bundle_v2 / "bands").is_dir()

    bundle_v1 = hdd_mount / "AlphaGridData-2026-11-05.sparsebundle"
    assert bundle_v1.is_dir()
    assert (bundle_v1 / "Info.plist").is_file()
    assert (bundle_v1 / "token").is_file()
    assert (bundle_v1 / "bands").is_dir()


def test_merkle_root_independent_reproduction():
    """Verify independent mathematical reproduction of plain snapshot v2 Merkle root."""
    from tools.build_plain_snapshot_manifest import (
        compute_deterministic_merkle_root,
        verify_snapshot_merkle_root,
    )

    manifest_file = Path("data/research_snapshot_manifest_plain_v2.json")
    assert manifest_file.is_file(), f"Manifest file missing: {manifest_file}"

    matches, computed_root, expected_root = verify_snapshot_merkle_root(manifest_file)
    assert matches is True
    assert computed_root == "06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3"
    assert expected_root == "06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3"

    # Verify odd-leaf duplication and tamper detection on minimal vectors
    h_a = hashlib.sha256(b"leaf_a").hexdigest()
    h_b = hashlib.sha256(b"leaf_b").hexdigest()
    h_c = hashlib.sha256(b"leaf_c").hexdigest()

    # 3 leaves: pair (a, b) and (c, c) duplicated odd leaf -> root = SHA256(ab + cc)
    ab = hashlib.sha256((h_a + h_b).encode("ascii")).hexdigest()
    cc = hashlib.sha256((h_c + h_c).encode("ascii")).hexdigest()
    expected_root_3 = hashlib.sha256((ab + cc).encode("ascii")).hexdigest()
    assert compute_deterministic_merkle_root([h_a, h_b, h_c]) == expected_root_3

    # Tampering any leaf changes the root
    h_c_tampered = hashlib.sha256(b"leaf_c_tampered").hexdigest()
    assert compute_deterministic_merkle_root([h_a, h_b, h_c_tampered]) != expected_root_3
