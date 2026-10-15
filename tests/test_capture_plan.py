from __future__ import annotations

import pytest

from quant.acquisition.capture_plan import LiveCaptureProfile, estimate_capacity


def test_future_capture_profile_stays_disabled() -> None:
    profile = LiveCaptureProfile(security_ids=("1", "2", "3", "4", "5"))
    profile.validate_pilot()
    with pytest.raises(ValueError, match="activation"):
        LiveCaptureProfile(
            enabled=True, security_ids=profile.security_ids
        ).validate_pilot()
    with pytest.raises(ValueError, match="5-10"):
        LiveCaptureProfile(security_ids=("1",)).validate_pilot()


def test_explicit_capacity_arithmetic_is_not_a_measurement() -> None:
    result = estimate_capacity(
        instruments=5,
        peak_events_per_second_per_instrument=100,
        bytes_per_event=516,
        retention_sessions=20,
        copies=2,
    )
    assert result.raw_events_per_session == 11_250_000
    assert result.raw_bytes_per_session == 5_805_000_000
    assert result.retained_bytes_with_copies == 232_200_000_000
    assert result.assumptions_only
