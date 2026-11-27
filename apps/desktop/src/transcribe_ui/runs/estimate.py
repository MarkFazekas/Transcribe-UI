"""Runtime limit and cost estimate of a run (plan m5 §1 "Run deadline and wait", "Cost estimate").

Both are sized on the pending transcribe jobs of the launch scope, `careful` jobs (two passes)
counted twice. The factors are placeholders recalibrated at M7.
"""

import math
from collections.abc import Iterable

from transcribe_ui.runpod.account import CatalogApi
from transcribe_ui.runpod.errors import RunpodApiError
from transcribe_ui.runs.classes import CostEstimate, LaunchPlan, LaunchScope, PendingJob
from transcribe_ui.runs.constants import (
    CAREFUL_WEIGHT,
    COST_DECIMALS,
    COST_FACTOR_HIGH,
    COST_FACTOR_LOW,
    COST_STARTUP_HIGH_S,
    COST_STARTUP_LOW_S,
    FALLBACK_PRICE_PER_HOUR,
    GPU_TYPE_ID,
    MAX_RUNTIME_CAP_S,
    RUNTIME_JOB_OVERHEAD_S,
    RUNTIME_MARGIN,
    RUNTIME_REAL_TIME_FACTOR,
    SECONDS_PER_HOUR,
    STARTUP_ALLOWANCE_S,
    TEST_MODE_RUNTIME_S,
)
from transcribe_ui.runs.manifests import total_duration


def job_weight(job: PendingJob) -> int:
    """Return how many passes a job takes (`careful`: two)."""
    return CAREFUL_WEIGHT if job.mode == "careful" else 1


def weighted_duration(jobs: Iterable[PendingJob]) -> float:
    """Return the pending audio duration with `careful` jobs counted twice."""
    return total_duration(job.duration_s * job_weight(job) for job in jobs)


def max_runtime_s(jobs: Iterable[PendingJob], *, test_mode: bool) -> int:
    """Return `MAX_RUNTIME_S`: start-up allowance + per job (audio x factor x margin + overhead), capped.

    Test mode: a fixed 2 h (the pod is kept alive until then).
    """
    if test_mode:
        return TEST_MODE_RUNTIME_S
    per_job = [
        (job.duration_s * RUNTIME_REAL_TIME_FACTOR * RUNTIME_MARGIN + RUNTIME_JOB_OVERHEAD_S) * job_weight(job)
        for job in jobs
    ]
    seconds = math.ceil(STARTUP_ALLOWANCE_S + total_duration(per_job))
    return min(seconds, MAX_RUNTIME_CAP_S)


def catalog_price(catalog: CatalogApi) -> tuple[float, str | None]:
    """Return the secure-cloud price per hour of the worker's GPU, and why the fallback was used (or None).

    A catalog that cannot be read gives the fallback price with a note, never an error: the
    estimate is shown either way. A cancelled request still stops.
    """
    try:
        price = catalog.secure_price(GPU_TYPE_ID)
    except RunpodApiError:
        return FALLBACK_PRICE_PER_HOUR, "The Runpod price list could not be read; the estimate uses the usual price."
    if price is None:
        return (
            FALLBACK_PRICE_PER_HOUR,
            "The Runpod price list does not list the GPU; the estimate uses the usual price.",
        )
    return price, None


def cost_estimate(weighted_s: float, price: float, note: str | None, *, test_mode: bool) -> CostEstimate:
    """Return the cost range: price x (start-up + weighted audio x real-time factor), low and high.

    In test mode the pod stays up for its whole runtime limit, so that replaces the audio term.
    """
    low_s: float
    high_s: float
    if test_mode:
        low_s = COST_STARTUP_LOW_S + TEST_MODE_RUNTIME_S
        high_s = COST_STARTUP_HIGH_S + TEST_MODE_RUNTIME_S
    else:
        low_s = COST_STARTUP_LOW_S + weighted_s * COST_FACTOR_LOW
        high_s = COST_STARTUP_HIGH_S + weighted_s * COST_FACTOR_HIGH
    return CostEstimate(
        low_usd=round(price * low_s / SECONDS_PER_HOUR, COST_DECIMALS),
        high_usd=round(price * high_s / SECONDS_PER_HOUR, COST_DECIMALS),
        price_per_hour_usd=price,
        price_source="catalog" if note is None else "fallback",
        note=note,
    )


def launch_plan(scope: LaunchScope, catalog: CatalogApi, *, test_mode: bool) -> LaunchPlan:
    """Return the plan of a launch over this scope, priced from the catalog (or the fallback)."""
    jobs = [job for batch in scope.batches for job in batch.pending_jobs]
    weighted_s = weighted_duration(jobs)
    price, note = catalog_price(catalog)
    return LaunchPlan(
        scope=scope,
        test_mode=test_mode,
        weighted_duration_s=weighted_s,
        max_runtime_s=max_runtime_s(jobs, test_mode=test_mode),
        cost=cost_estimate(weighted_s, price, note, test_mode=test_mode),
    )
