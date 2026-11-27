"""The runtime limit and the cost estimate of a run (plan m5 §1; factors recalibrated at M7)."""

from transcribe_ui.runs.classes import PendingJob
from transcribe_ui.runs.constants import (
    MAX_RUNTIME_CAP_S,
    STARTUP_ALLOWANCE_S,
    TEST_MODE_RUNTIME_S,
)
from transcribe_ui.runs.estimate import cost_estimate, max_runtime_s, weighted_duration

# One hour of audio.
HOUR_S = 3600.0
# Per job: audio x 0.2 x 2 + 60 s overhead.
NORMAL_HOUR_RUNTIME_S = 1500
# Price per hour of the tests.
PRICE = 0.3
# Audio long enough to pass the cap.
LONG_S = float(MAX_RUNTIME_CAP_S * 3)


def _job(mode: str, duration_s: float = HOUR_S) -> PendingJob:
    return PendingJob.model_validate({"item_id": "item", "language": "en", "mode": mode, "duration_s": duration_s})


def test_careful_jobs_count_twice() -> None:
    """A `careful` job weighs twice a `normal` one in the runtime and the duration the cost is sized on."""
    normal = [_job("normal")]
    careful = [_job("careful")]
    assert max_runtime_s(normal, test_mode=False) == STARTUP_ALLOWANCE_S + NORMAL_HOUR_RUNTIME_S
    assert max_runtime_s(careful, test_mode=False) == STARTUP_ALLOWANCE_S + 2 * NORMAL_HOUR_RUNTIME_S
    assert weighted_duration(careful) == 2 * weighted_duration(normal)


def test_runtime_is_capped_and_fixed_in_test_mode() -> None:
    """At most 24 h; a fixed 2 h in test mode; the start-up allowance alone without jobs."""
    assert max_runtime_s([_job("normal", LONG_S)], test_mode=False) == MAX_RUNTIME_CAP_S
    assert max_runtime_s([_job("normal")], test_mode=True) == TEST_MODE_RUNTIME_S
    assert max_runtime_s([], test_mode=False) == STARTUP_ALLOWANCE_S


def test_cost_range() -> None:
    """Price x (5 to 20 min start-up + audio x 0.05 to 0.2), in dollars with cents."""
    estimate = cost_estimate(HOUR_S, PRICE, None, test_mode=False)
    assert (estimate.low_usd, estimate.high_usd, estimate.price_source) == (0.04, 0.16, "catalog")
    fallback = cost_estimate(HOUR_S, PRICE, "no price list", test_mode=True)
    assert (fallback.low_usd, fallback.high_usd, fallback.price_source) == (0.62, 0.7, "fallback")
