"""Read-only offline research dataset builder."""

from __future__ import annotations

import argparse
from pathlib import Path

from quant.research.dataset import build_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-jsonl", type=Path, required=True)
    parser.add_argument("--paper-qp", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--source-kind", choices=("SYNTHETIC", "REAL_UNVERIFIED"), required=True
    )
    args = parser.parse_args()
    result = build_dataset(
        market_jsonl=args.market_jsonl,
        paper_qp=args.paper_qp,
        output_dir=args.output_dir,
        source_kind=args.source_kind,
    )
    print(f"{result.status}: {result.feature_rows} rows; {result.dataset_sha256}")


if __name__ == "__main__":
    main()
