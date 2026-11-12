#!/usr/bin/env python3
"""Cross-platform Python implementation of tools/repo-context.ps1.

Generates or checks docs/context/merkle.json from Git-tracked files plus explicit
support files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MANIFEST_PATH = "docs/context/merkle.json"

SUPPORT_FILES = [
    "AGENTS.md",
    "docs/context/REPO_MAP.md",
    "docs/context/DECISIONS.md",
    "docs/exp-opening-sell-pressure-reversal-v1.md",
    "docs/exp-weekly-relative-strength-v1.md",
    "docs/forward-observations.md",
    "docs/trading-product-brief.md",
    "docs/dhan-api-key-callbacks.md",
    "docs/historical-simulator-2026-11-02.md",
    "docs/live-pilot-2026-11-02-preflight.md",
    "docs/live-simulation-macos.md",
    "docs/cross-equity-adaptive-v2-2026-11-03.md",
    "docs/cross-stock-opportunity-plan-2026-11-02.md",
    "docs/cross-stock-plan-review-2026-11-02.md",
    "docs/cross-stock-universe-2026-11-02.json",
    "docs/nov3-dhan-probe-preflight.md",
    "docs/real-microstructure-readiness-2026-11-02.md",
    "docs/reliance-30-60-remaining-readiness.md",
    "docs/reliance-60m-closure-audit.md",
    "docs/reliance-60m-first-signal-result.md",
    "docs/reliance-60m-frozen-protocol.md",
    "docs/reliance-assumed-timing-exploratory.md",
    "docs/reliance-candle-timing-review.md",
    "docs/reliance-six-month-mac-audit.md",
    "data/config/reliance-observation-2026-10-27.json",
    "data/config/reliance-observation-2026-10-28.json",
    "web/server/trading-service.mjs",
    "web/server/trading-service.test.mjs",
    "web/server/dhan-account.mjs",
    "web/server/dhan-account.test.mjs",
    "web/server/market-data-access.mjs",
    "web/server/market-data-access.test.mjs",
    "web/src/TradingWorkspace.tsx",
    "web/src/SimulationWorkspace.tsx",
    "web/src/SimulationWorkspace.test.tsx",
    "web/src/TradingDemo.tsx",
    "web/src/CommandCenter.tsx",
    "rust/crates/quant-telemetry/src/bin/telemetry_local.rs",
    "src/quant/acquisition/forward_observations.py",
    "src/quant/apps/forward_observations.py",
    "src/quant/apps/dhan_candle_availability.py",
    "src/quant/apps/dhan_quote_preflight.py",
    "src/quant/apps/cockpit_cache.py",
    "src/quant/apps/historical_simulator.py",
    "src/quant/apps/live_simulation.py",
    "src/quant/apps/live_feed_scope.py",
    "src/quant/apps/live_feed_credentials.py",
    "src/quant/apps/local_training_worker.py",
    "src/quant/apps/dhan_account_cache.py",
    "src/quant/apps/ingest_nse_evidence.py",
    "src/quant/apps/cross_equity_adaptive.py",
    "src/quant/apps/descriptive_dhan_opportunities.py",
    "src/quant/apps/exploratory_dhan_timing.py",
    "src/quant/apps/reliance_60m_falsification.py",
    "tests/test_forward_observations.py",
    "tests/test_dhan_candle_availability.py",
    "tests/test_dhan_quote_preflight.py",
    "tests/test_cockpit_cache.py",
    "tests/test_historical_simulator.py",
    "tests/test_live_simulation.py",
    "tests/test_live_feed_scope.py",
    "tests/test_live_feed_credentials.py",
    "tests/test_local_training_worker.py",
    "tests/test_dhan_account_cache.py",
    "tests/test_dhan_oauth_callback.py",
    "tests/test_ingest_nse_evidence.py",
    "tests/test_repo_context.py",
    "tests/test_opening_reversal_v1.py",
    "tests/test_weekly_relative_strength_v1.py",
    "tests/test_cross_equity_adaptive.py",
    "tests/test_descriptive_dhan_opportunities.py",
    "tests/test_exploratory_dhan_timing.py",
    "tests/test_reliance_60m_falsification.py",
    "tools/start-cockpit.ps1",
    "tools/start-cockpit-macos.sh",
    "tools/dhan_oauth_callback.py",
    "tools/repo-context.ps1",
    "tools/repo_context.py",
    "tools/evaluate_opening_reversal_v1.py",
    "tools/evaluate_weekly_relative_strength_v1.py",
]


def text_sha256(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest().lower()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def compute_merkle(repo_root: Path) -> dict[str, Any]:
    cmd = ["git", "-C", str(repo_root), "-c", "core.quotePath=false", "ls-files", "--cached"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    tracked = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    all_candidate_paths = sorted(set(tracked + SUPPORT_FILES))
    paths: list[str] = []
    for rel in all_candidate_paths:
        if rel.startswith('"'):
            raise ValueError(f"Git-escaped filename is unsupported: {rel}")
        if rel == MANIFEST_PATH:
            continue
        abs_p = repo_root / rel
        if abs_p.is_file():
            if abs_p.is_symlink():
                raise ValueError(f"Symlink/reparse point is unsupported: {rel}")
            paths.append(rel)
        elif abs_p.exists():
            raise ValueError(f"Expected a regular file, found another entry type: {rel}")

    files: dict[str, Any] = {}
    tree_entries: dict[str, dict[str, Any]] = {".": {}}

    for rel in sorted(paths):
        abs_p = repo_root / rel
        c_hash = file_sha256(abs_p)
        blob_hash = text_sha256(f"blob\0{c_hash}")
        files[rel] = {
            "content_sha256": c_hash,
            "sha256": blob_hash,
        }
        parts = rel.split("/")
        parent = "."
        for i in range(len(parts) - 1):
            child = parts[i] if parent == "." else f"{parent}/{parts[i]}"
            if child not in tree_entries:
                tree_entries[child] = {}
            tree_entries[parent][parts[i]] = {"kind": "tree", "path": child}
            parent = child
        tree_entries[parent][parts[-1]] = {"kind": "blob", "path": rel}

    computed_trees: dict[str, Any] = {}
    # Process from deepest paths to root
    for directory in sorted(tree_entries.keys(), key=len, reverse=True):
        children: list[dict[str, Any]] = []
        payload_lines: list[str] = ["tree\0"]
        for name in sorted(tree_entries[directory].keys()):
            entry = tree_entries[directory][name]
            if entry["kind"] == "blob":
                ehash = files[entry["path"]]["sha256"]
            else:
                ehash = computed_trees[entry["path"]]["sha256"]
            payload_lines.append(f"{entry['kind']}\0{name}\0{ehash}\n")
            children.append({"name": name, "kind": entry["kind"], "sha256": ehash})

        payload_str = "".join(payload_lines)
        computed_trees[directory] = {
            "sha256": text_sha256(payload_str),
            "children": children,
        }

    directories: dict[str, Any] = {d: computed_trees[d] for d in sorted(computed_trees.keys())}

    return {
        "schema_version": 1,
        "algorithm": "SHA-256; UTF-8; ordinal child-name order; lowercase hex",
        "blob_rule": 'SHA256(UTF8("blob\\0" + content_sha256))',
        "tree_rule": 'SHA256(UTF8("tree\\0" + concat(kind + "\\0" + name + "\\0" + sha256 + "\\n")))',
        "scope": "Existing Git-tracked working files plus explicit supportFiles; excludes this manifest",
        "root_sha256": computed_trees["."]["sha256"],
        "file_count": len(files),
        "files": files,
        "directories": directories,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-platform context index check & refresh")
    parser.add_argument("--check", action="store_true", help="Check against existing merkle.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    manifest_file = repo_root / MANIFEST_PATH

    computed = compute_merkle(repo_root)

    if args.check:
        if not manifest_file.exists():
            print("Context index missing. Run this script without --check to create it.")
            return 1
        with open(manifest_file, "r", encoding="utf-8") as f:
            existing = json.load(f)

        if existing.get("root_sha256") == computed["root_sha256"] and existing.get("file_count") == computed["file_count"]:
            print(f"Context index current: {computed['file_count']} files; root {computed['root_sha256']}")
            return 0

        print("Context index stale. Inspect changed files and update summaries before refreshing.")
        old_files = {k: v["sha256"] for k, v in existing.get("files", {}).items()}
        new_files = {k: v["sha256"] for k, v in computed["files"].items()}

        for p in sorted(new_files.keys()):
            if p not in old_files:
                print(f"ADDED {p}")
            elif old_files[p] != new_files[p]:
                print(f"CHANGED {p}")
        for p in sorted(old_files.keys()):
            if p not in new_files:
                print(f"REMOVED {p}")

        old_trees = {k: v["sha256"] for k, v in existing.get("directories", {}).items()}
        new_trees = {k: v["sha256"] for k, v in computed["directories"].items()}

        for d in sorted(new_trees.keys()):
            if d not in old_trees or old_trees[d] != new_trees[d]:
                print(f"BRANCH {d}")
        for d in sorted(old_trees.keys()):
            if d not in new_trees:
                print(f"REMOVED BRANCH {d}")

        print(f"Current root: {computed['root_sha256']}")
        return 1

    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(computed, f, indent=4)
        f.write("\n")
    print(f"Indexed {computed['file_count']} files; root {computed['root_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
