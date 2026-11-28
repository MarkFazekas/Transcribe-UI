"""Models of the run-activity check and the launch scope (architecture §3.7)."""

from typing import Annotated, Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt, StringConstraints

from transcribe_contracts.classes import TranscriptionMode, Ulid
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.runs.constants import (
    LIST_RUNS_DEFAULT,
    LIST_RUNS_MAX,
    RUNPOD_ID_PATTERN,
    WAIT_DEFAULT_S,
    WAIT_MAX_S,
    WAIT_MIN_S,
)

# Where a sign of a live worker run was found: a local run row, the Runpod pod or queued-pod
# list, a worker heartbeat (`runs/<run_id>/status.json`) or a run lease (`runs/<run_id>/launch.json`) in R2.
type ActivityKind = Literal["local_run", "pod", "queued_pod", "heartbeat", "lease"]
# What a Runpod entry is: a pod or a queued pod ("deploy when available").
type RunpodKind = Literal["pod", "queued_pod"]
# Why a manifest under `batch_metadata/` is not part of the scope: a key that is not a manifest
# key, another schema major version, or an object that does not validate or match its key.
type UnreadableReason = Literal["not_a_manifest", "unsupported_schema", "invalid"]
# Where the price per hour of the estimate comes from: the Runpod catalog, or the built-in fallback.
type PriceSource = Literal["catalog", "fallback"]
# Id of a Runpod pod or queued pod as the requests accept it.
type RunpodId = Annotated[str, StringConstraints(pattern=RUNPOD_ID_PATTERN)]
# Why a run ended (`refresh_status`): its pod went away after the worker reported `finished` or
# `failed`, or without a final report; the desktop stopped it at its deadline; its queued pod
# ended (expired, failed, cancelled or vanished) without a pod; the user stopped it
# (`cancel_worker`, `terminate_pod`, `cancel_queued_pod`); or the pod request created nothing
# (`launch_failed`).
type EndReason = Literal[
    "worker_finished",
    "worker_failed",
    "pod_gone",
    "deadline",
    "queue_ended",
    "cancelled",
    "launch_failed",
]
# Share of a run's followed audio that is done (0 to 1; `runs/remaining.py`).
type ProgressFraction = Annotated[float, Field(ge=0, le=1)]


class ActivitySource(FrozenModel):
    """One sign of a live worker run."""

    kind: ActivityKind
    # Run id (local run), pod or queued-pod name, or the heartbeat's or lease's object key.
    name: str
    # The run's, pod's or queued pod's status, the worker state, or `live` for a lease
    # (`unreadable` for a heartbeat or lease that cannot be read).
    status: str
    # The run it belongs to, so a frontend can name it: the local run, the run in the pod name
    # (None for a name that does not parse) or the run of the heartbeat or lease.
    run_id: str | None = None


class RunActivity(FrozenModel):
    """Every sign of a live worker run found by one check; none means no run is active."""

    sources: tuple[ActivitySource, ...]

    @property
    def active(self) -> bool:
        """Return whether a run is active."""
        return bool(self.sources)


class PendingJob(FrozenModel):
    """A job of a manifest that has no result JSON yet."""

    item_id: str
    language: str
    mode: TranscriptionMode
    # Duration of the job's audio object.
    duration_s: NonNegativeFloat


class ScopeBatch(FrozenModel):
    """A batch the next worker run processes: its manifest has no `_done.json` yet."""

    batch_id: str
    # Whether this database knows the batch (a batch of another computer is unknown).
    known: bool
    # Files (manifest items) of the batch.
    files: NonNegativeInt
    pending_jobs: tuple[PendingJob, ...]
    pending_duration_s: NonNegativeFloat


class UnreadableManifest(FrozenModel):
    """An object under `batch_metadata/` the worker skips too (architecture §4)."""

    key: str
    reason: UnreadableReason


class LaunchScope(FrozenModel):
    """What the next worker run would process (its lease lists the batches): every manifest without `_done.json`."""

    # By batch id.
    batches: tuple[ScopeBatch, ...]
    # By key.
    unreadable: tuple[UnreadableManifest, ...]
    pending_jobs: NonNegativeInt
    pending_duration_s: NonNegativeFloat
    # Hash of the covered batch ids, the pending job keys and the total duration (plan m5 §1
    # "Launch scope fingerprint"): `launch_worker` launches only on the scope it confirmed.
    fingerprint: str


class CostEstimate(FrozenModel):
    """Estimated cost range of a run (plan m5 §1 "Cost estimate"; factors recalibrated at M7)."""

    low_usd: NonNegativeFloat
    high_usd: NonNegativeFloat
    price_per_hour_usd: NonNegativeFloat
    price_source: PriceSource
    # Why the fallback price was used (None with the catalog price).
    note: str | None = None


class WaitBounds(FrozenModel):
    """The wait for a machine `launch_worker` accepts (seconds)."""

    default_s: PositiveInt = WAIT_DEFAULT_S
    min_s: PositiveInt = WAIT_MIN_S
    max_s: PositiveInt = WAIT_MAX_S


class LaunchPlan(FrozenModel):
    """What `launch_worker` would start: the scope, the runtime limit and the cost estimate."""

    scope: LaunchScope
    test_mode: bool
    # Pending audio with `careful` jobs counted twice (what the runtime and cost are sized on).
    weighted_duration_s: NonNegativeFloat
    # `MAX_RUNTIME_S` the worker would get.
    max_runtime_s: PositiveInt
    cost: CostEstimate
    # Hash of the scope's fingerprint, the test mode and `MAX_RUNTIME_S`: what `launch_worker`
    # takes as `confirm_fingerprint`, so a plan confirmed without test mode cannot start one with it.
    fingerprint: str
    wait: WaitBounds = Field(default_factory=WaitBounds)


# Requests


class PlanLaunchRequest(FrozenModel):
    """Show what a worker started now would process, its runtime limit and its estimated cost; changes nothing."""

    action: Literal["plan_launch"]
    # Test mode: `MAX_RUNTIME_S` of 2 h and the pod kept alive until then (for SSH).
    test_mode: bool = False


class LaunchWorkerRequest(FrozenModel):
    """Start one worker for every pending manifest; needs the fingerprint of the confirmed plan.

    Without `confirm_fingerprint`, or when the scope changed since the plan it names, the
    answer is `needs_confirmation` with the current plan, and nothing is changed.
    """

    action: Literal["launch_worker"]
    # How long Runpod may wait for a free machine (queue `expiresAt`), in seconds.
    wait_s: Annotated[int, Field(ge=WAIT_MIN_S, le=WAIT_MAX_S)] = WAIT_DEFAULT_S
    test_mode: bool = False
    # The `fingerprint` of the plan the user confirmed (`plan_launch` with the same `test_mode`).
    confirm_fingerprint: str | None = None


class RefreshStatusRequest(FrozenModel):
    """Follow the runs that are not final (with a Runpod key) and look for results of `ready` batches.

    Past its desktop deadline a run's pod is terminated (verified); a run whose pod is gone is
    ended, its `runs/<run_id>/` objects kept locally and deleted.
    """

    action: Literal["refresh_status"]
    # The fuller check the GUI sends at start-up and on Refresh now: look again for the `ready`
    # batches marked removed from the cloud (one whose manifest or `_done.json` is back is followed
    # again), and finish this computer's interrupted cleanups of other computers' jobs.
    recheck: bool = False


class ListRunsRequest(FrozenModel):
    """List the latest runs as stored, newest first; reads only the database (no Runpod or R2 call).

    Elapsed time and cost are as of each run's end, else its last `refresh_status`.
    """

    action: Literal["list_runs"]
    # How many runs to return at most.
    limit: Annotated[int, Field(ge=1, le=LIST_RUNS_MAX)] = LIST_RUNS_DEFAULT


class CancelWorkerRequest(FrozenModel):
    """Stop a run: cancel its queued pod (terminating a pod it deployed meanwhile) or terminate its pod.

    Without `confirm`, the answer is `needs_confirmation` (the run and its stage), and nothing is changed.
    """

    action: Literal["cancel_worker"]
    run_id: Ulid
    confirm: bool = False


class ReconcileCloudRequest(FrozenModel):
    """List the app's live pods and queued pods, join them with the local runs, resolve `launching` runs."""

    action: Literal["reconcile_cloud"]


class TerminatePodRequest(FrozenModel):
    """Terminate one of the app's pods (a straggler or an overdue one); needs `confirm`."""

    action: Literal["terminate_pod"]
    pod_id: RunpodId
    confirm: bool = False


class CancelQueuedPodRequest(FrozenModel):
    """Cancel one of the app's queued pods, terminating a pod it deployed meanwhile; needs `confirm`."""

    action: Literal["cancel_queued_pod"]
    queued_pod_id: RunpodId
    confirm: bool = False
