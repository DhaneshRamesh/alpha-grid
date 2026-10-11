"""Replay one journal session into the published research Parquet dataset."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from quant.config import AppConfig
from quant.logging import configure_logging
from quant.storage.parquet import ParquetSettings, export_journal

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    configure_logging(AppConfig.from_env().logging_level)
    try:
        result = export_journal(ParquetSettings.from_env(os.environ))
    except (OSError, ValueError) as exc:
        _LOGGER.error("parquet_export_failed", extra={"context": {"reason": str(exc)}})
        raise SystemExit(1) from None
    _LOGGER.info("parquet_export_complete", extra={"context": asdict(result)})


if __name__ == "__main__":
    main()
