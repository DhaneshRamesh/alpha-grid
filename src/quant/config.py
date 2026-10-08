"""Typed process configuration from environment variables."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: str = "development"
    log_level: str = "INFO"
    feed_stale_after_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.environment.strip():
            raise ValueError("environment must not be empty")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be a standard logging level")
        if not 0 < self.feed_stale_after_seconds < float("inf"):
            raise ValueError("feed_stale_after_seconds must be finite and positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        values = os.environ if environ is None else environ
        raw_stale = values.get("QUANT_FEED_STALE_AFTER_SECONDS", "5")
        try:
            stale_after = float(raw_stale)
        except ValueError as exc:
            raise ValueError("QUANT_FEED_STALE_AFTER_SECONDS must be numeric") from exc
        return cls(
            environment=values.get("QUANT_ENVIRONMENT", "development"),
            log_level=values.get("QUANT_LOG_LEVEL", "INFO").upper(),
            feed_stale_after_seconds=stale_after,
        )

    @property
    def logging_level(self) -> int:
        return int(getattr(logging, self.log_level))
