from __future__ import annotations

import pytest

from quant.config import AppConfig


def test_config_reads_environment() -> None:
    config = AppConfig.from_env(
        {
            "QUANT_ENVIRONMENT": "test",
            "QUANT_LOG_LEVEL": "debug",
            "QUANT_FEED_STALE_AFTER_SECONDS": "2.5",
        }
    )
    assert config.environment == "test"
    assert config.log_level == "DEBUG"
    assert config.feed_stale_after_seconds == 2.5


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "bad"])
def test_config_rejects_invalid_staleness(raw: str) -> None:
    with pytest.raises(ValueError):
        AppConfig.from_env({"QUANT_FEED_STALE_AFTER_SECONDS": raw})
