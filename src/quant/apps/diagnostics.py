"""Configuration and logging smoke check; no network or order actions."""

from __future__ import annotations

import logging

from quant.config import AppConfig
from quant.logging import configure_logging


def main() -> None:
    config = AppConfig.from_env()
    configure_logging(config.logging_level)
    logging.getLogger(__name__).info(
        "diagnostics_ready", extra={"context": {"environment": config.environment}}
    )


if __name__ == "__main__":
    main()
