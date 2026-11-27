"""Pydantic payload of the `runs` rows (the JSON column of `state/models.py`)."""

from pydantic import AwareDatetime, NonNegativeFloat, PositiveInt

from transcribe_ui.api.base import FrozenModel
from transcribe_ui.runs.classes import CostEstimate


class RunPayload(FrozenModel):
    """What a run row keeps besides its id, status and pod name.

    Written before the pod is requested (§3.7); the pod or queued-pod id follows once known.
    """

    # `MAX_RUNTIME_S` given to the worker.
    max_runtime_s: PositiveInt
    test_mode: bool
    # Wait for a machine the run was started with.
    wait_s: PositiveInt
    # The queue's `expiresAt`: after it, a queued pod that has not deployed never will.
    queue_expires_at: AwareDatetime
    # Deadline in the pod name (rounded up to the minute), after which the reaper terminates it.
    name_deadline: AwareDatetime
    # Batches whose manifest the run covers (the confirmed launch scope).
    covered_batch_ids: tuple[str, ...]
    # Fingerprint of the confirmed launch scope.
    fingerprint: str
    # Pending audio of the confirmed scope (seconds) and the confirmed cost estimate.
    pending_duration_s: NonNegativeFloat
    estimate: CostEstimate
    # Runpod ids once known.
    pod_id: str | None = None
    queued_pod_id: str | None = None
    # When the pod request had been sent at the latest (its answer or failure arrived).
    posted_at: AwareDatetime | None = None
    # First lookup after the settle time that found the pod name in neither list.
    first_empty_lookup_at: AwareDatetime | None = None
