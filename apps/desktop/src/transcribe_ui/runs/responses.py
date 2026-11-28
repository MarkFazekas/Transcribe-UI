"""Response models of the run requests (architecture §3.7)."""

from typing import Literal

from pydantic import AwareDatetime, NonNegativeFloat, NonNegativeInt, PositiveInt

from transcribe_ui.api.base import FrozenModel, LocalPath
from transcribe_ui.runs.classes import EndReason, LaunchPlan, ProgressFraction, RunpodKind
from transcribe_ui.state.models import BatchPhase, RunStatus

# How `launch_worker` ended: a pod (direct start), a queued pod, an unknown outcome (the run
# stays `launching`; `refresh_status` or `reconcile_cloud` resolves it by its name), or, after a
# lost answer, a queued pod found already ended (the run is `cancelled` or `failed`).
type LaunchOutcome = Literal["pod", "queued", "unconfirmed", "ended"]
# How a live app pod or queued pod relates to the local runs: it belongs to a run that is not
# final, or it is a straggler (no local run, a final one, or a name that does not parse).
type EntryRole = Literal["current", "straggler"]
# What stopping a queued pod did: cancelled it (or it had ended), or terminated the pod it deployed.
type QueuedStop = Literal["cancelled", "pod_terminated"]
# What the app shows of a run: its pod request unanswered, waiting for a machine, the worker
# starting, transcribing, or done while its pod is being stopped; then how the run ended.
type RunStage = Literal[
    "launching",
    "waiting",
    "starting",
    "transcribing",
    "stopping",
    "finished",
    "failed",
    "cancelled",
    "launch_failed",
]


class OkResponse(FrozenModel):
    """Base of the success responses of this package (`status` "ok", written first)."""

    status: Literal["ok"] = "ok"


class RunInfo(FrozenModel):
    """A local run as the frontends show it."""

    run_id: str
    status: RunStatus
    pod_name: str
    pod_id: str | None
    queued_pod_id: str | None
    test_mode: bool
    max_runtime_s: PositiveInt
    queue_expires_at: AwareDatetime
    name_deadline: AwareDatetime
    covered_batch_ids: tuple[str, ...]
    created_at: AwareDatetime


class PlanLaunchResponse(OkResponse):
    """What a worker started now would process; confirm it by sending `launch_worker` with its fingerprint."""

    action: Literal["plan_launch"] = "plan_launch"
    plan: LaunchPlan


class LaunchWorkerResponse(OkResponse):
    """The run started (or whose outcome is still unknown), and the held manifests published first."""

    action: Literal["launch_worker"] = "launch_worker"
    outcome: LaunchOutcome
    # Whether the pod or queued pod was found by its name after the request's answer was lost.
    adopted: bool = False
    run: RunInfo
    # Held manifests published before the launch, by batch id.
    published: tuple[str, ...] = ()


class CancelWorkerResponse(OkResponse):
    """The run, now `cancelled`."""

    action: Literal["cancel_worker"] = "cancel_worker"
    run: RunInfo
    # The pod terminated by the cancel (a started run, or a queued pod that deployed meanwhile).
    terminated_pod_id: str | None = None


class CloudEntry(FrozenModel):
    """A live `transcribe-ui-*` pod or queued pod on Runpod."""

    kind: RunpodKind
    entry_id: str
    name: str
    status: str
    # For a deployed queued pod whose pod is not listed: the pod's id.
    pod_id: str | None = None
    # Run id and deadline from the name (None when the name does not parse).
    run_id: str | None
    deadline: AwareDatetime | None
    # Status of the local run of that id (None when this database does not know it).
    local_status: RunStatus | None
    role: EntryRole
    # Past its name deadline: nothing legitimate runs any more.
    overdue: bool


class ReconcileCloudResponse(OkResponse):
    """The app's live pods and queued pods, and the local runs that were not final."""

    action: Literal["reconcile_cloud"] = "reconcile_cloud"
    # By kind, then name.
    entries: tuple[CloudEntry, ...]
    # Every local run that was not final before reconciling, as it is now (`launching` ones resolved).
    runs: tuple[RunInfo, ...]
    # Whether an entry is a straggler or overdue (`terminate_pod`, `cancel_queued_pod`).
    attention: bool


class TerminatePodResponse(OkResponse):
    """The pod is terminated (verified)."""

    action: Literal["terminate_pod"] = "terminate_pod"
    pod_id: str
    name: str
    run_id: str | None
    # The local run of that id, as it is now (`cancelled` if it was not final).
    local_status: RunStatus | None


class CancelQueuedPodResponse(OkResponse):
    """The queued pod is cancelled, or the pod it deployed is terminated (verified)."""

    action: Literal["cancel_queued_pod"] = "cancel_queued_pod"
    queued_pod_id: str
    name: str
    outcome: QueuedStop
    terminated_pod_id: str | None = None
    run_id: str | None
    local_status: RunStatus | None


class CurrentJob(FrozenModel):
    """The job the worker reports working on."""

    batch_id: str
    item_id: str
    language: str
    # The file name of the item (None for a batch or item this database does not know).
    name: str | None = None


class WorkerProgress(FrozenModel):
    """The worker's own report (`runs/<run_id>/status.json`)."""

    # `starting`, `transcribing`, `finished`, `failed`, or `unreadable` (invalid, another schema major).
    state: str
    updated_at: AwareDatetime | None = None
    current: CurrentJob | None = None
    done_jobs: NonNegativeInt = 0
    total_jobs: NonNegativeInt = 0
    error: str | None = None


class RunProgress(FrozenModel):
    """A run as the last `refresh_status` left it, with what the app shows while it runs.

    Built from the stored run only: elapsed time and cost are measured to `elapsed_until` (the
    run's end, else its last refresh), never to the moment of the request; the progress fields
    are as the last refresh computed them.
    """

    run: RunInfo
    stage: RunStage
    # The pod's status as last read (None before a pod is known).
    pod_status: str | None
    # The worker's report as last read; None while none was read.
    worker: WorkerProgress | None
    first_running_at: AwareDatetime | None
    # Desktop deadline: first `RUNNING` sighting + `MAX_RUNTIME_S` + margin, capped by the name deadline.
    deadline: AwareDatetime
    # Since the pod's creation, until `elapsed_until` (None before a pod is known or the run refreshed).
    elapsed_s: NonNegativeFloat | None
    price_per_hour_usd: NonNegativeFloat | None
    cost_so_far_usd: NonNegativeFloat | None
    # When `refresh_status` last followed the run (None before the first refresh).
    refreshed_at: AwareDatetime | None = None
    # What elapsed time and cost are measured to: `ended_at`, else `refreshed_at`.
    elapsed_until: AwareDatetime | None = None
    ended_at: AwareDatetime | None
    end_reason: EndReason | None
    # Error code of a stop at the deadline that did not show at the last refresh (the next one tries again).
    stop_error: str | None = None
    # The worker's log, kept in the user log dir once the run's objects were collected.
    worker_log: LocalPath | None = None
    # Weighted audio of the run's followed jobs that is done (failed and no-speech jobs count as done).
    progress_fraction: ProgressFraction | None = None
    # Processing time left as of `refreshed_at`, and that time plus it (UTC); None when unknown or final.
    remaining_estimate_s: NonNegativeInt | None = None
    estimated_finish_at: AwareDatetime | None = None


class ListRunsResponse(OkResponse):
    """The latest runs, newest first, as stored (no cloud call)."""

    action: Literal["list_runs"] = "list_runs"
    runs: tuple[RunProgress, ...]


class BatchResults(FrozenModel):
    """A `ready` batch's transcribe jobs by result, and its phase now (`transcribed` once `_done.json` is there)."""

    batch_id: str
    phase: BatchPhase
    ok: NonNegativeInt
    no_speech: NonNegativeInt
    failed: NonNegativeInt
    pending: NonNegativeInt


class RefreshStatusResponse(OkResponse):
    """The runs that were not final, the `ready` batches' results, and the held manifests published."""

    action: Literal["refresh_status"] = "refresh_status"
    # Every run that was not final before the refresh, as it is now, oldest first.
    runs: tuple[RunProgress, ...]
    # Every batch that was `ready` (not skipped as busy), by batch id.
    batches: tuple[BatchResults, ...]
    # `ready` batches skipped because another request holds their lock (checked next time).
    busy: tuple[str, ...] = ()
    # Held manifests published now that no run is active, by batch id.
    published: tuple[str, ...] = ()
    # Held manifests still held (a run is active, or their batch was busy or incomplete).
    held: tuple[str, ...] = ()
