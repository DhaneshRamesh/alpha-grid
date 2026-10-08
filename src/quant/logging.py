"""Structured UTC JSON logging for process entry points."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context is not None:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, allow_nan=False)


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Configure the root logger once for a process entry point."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
