"""Pydantic payload of the `runs` rows (the JSON column of `state/models.py`)."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeFloat, NonNegativeInt, PositiveInt

from transcribe_contracts.classes import RunStatus as WorkerStatus
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.runs.classes import CostEstimate, EndReason, ProgressFraction
from transcribe_ui.runs.stopping import KnownPod


class StoredCurrentJob(BaseModel):
    """The job a stored worker report names, with its file name (None when unknown here).

    Plain stored fields, unknown keys ignored: mapped to `CurrentJob` when shown.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    batch_id: str
    item_id: str
    language: str
    name: str | None = None


class StoredWorkerReport(BaseModel):
    """The worker report a refresh read, as stored in the run payload (mapped to `WorkerProgress` when shown).

    Plain stored fields, unknown keys ignored, so a later change of the response model does not
    make stored runs unreadable.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    state: str
    updated_at: AwareDatetime | None = None
    current: StoredCurrentJob | None = None
    done_jobs: int = 0
    total_jobs: int = 0
    error: str | None = None


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
    # Pending audio of the covered batches this database knows, `careful` jobs counted twice
    # (seconds): the jobs whose progress the app follows. None for a run launched before it was kept.
    weighted_known_duration_s: NonNegativeFloat | None = None
    # Runtime of the pending jobs of the covered batches this database does not know (another
    # computer's), by the launch estimate's factors without the margin: in the remaining time until
    # the worker's pace is measured. None for a run launched before it was kept.
    unknown_runtime_s: NonNegativeFloat | None = None
    # Runpod ids once known.
    pod_id: str | None = None
    queued_pod_id: str | None = None
    # Whether a pod read (create answer, pod list or lookup) showed the pod; False for a pod id
    # known only from the queued pod, whose deploy time (`resolvedAt`) is then kept.
    pod_seen: bool = False
    pod_deployed_at: AwareDatetime | None = None
    # When this run first recorded its pod id (set by `update_run`): the settle anchor of a pod
    # that was not seen when the queued pod gives no `resolvedAt`.
    pod_recorded_at: AwareDatetime | None = None
    # When the latest attempt of the pod request was about to be sent (written before each one).
    sending_at: AwareDatetime | None = None
    # When the pod request had been sent at the latest (its answer or failure arrived).
    posted_at: AwareDatetime | None = None
    # First lookup after the settle time that found the pod name in neither list.
    first_empty_lookup_at: AwareDatetime | None = None
    # First time `refresh_status` read the pod `RUNNING`: the desktop deadline counts from it.
    first_running_at: AwareDatetime | None = None
    # The pod's creation (`createdAt`, else its first sighting): elapsed time and cost count from it.
    pod_created_at: AwareDatetime | None = None
    # The pod's price (USD per hour) and status as last read.
    price_per_hour_usd: NonNegativeFloat | None = None
    pod_status: str | None = None
    # When `refresh_status` (or the deadline stop) saw the run end, and why.
    ended_at: AwareDatetime | None = None
    end_reason: EndReason | None = None
    # The worker's last `runs/<run_id>/status.json` (when readable), kept once the run has ended.
    final_status: WorkerStatus | None = None
    # Whether `runs/<run_id>/` was kept locally (status here, `worker.log` in the log dir) and deleted.
    objects_collected: bool = False
    # The worker's report as last read (by `refresh_status`, or by the collection of a final run),
    # as the frontends show it; None while none was read. `list_runs` shows it after a restart.
    worker: StoredWorkerReport | None = None
    # Error code of the deadline stop of the last refresh that did not show yet; None otherwise.
    stop_error: str | None = None
    # When `refresh_status` last followed the run: elapsed time and cost are as of then (or `ended_at`).
    refreshed_at: AwareDatetime | None = None
    # When a cancel of the run's queued pod was first acknowledged (recorded only after the success
    # answer): no pod deploys after it, so a vanished queued pod settles from it (`stopping`).
    cancel_acknowledged_at: AwareDatetime | None = None
    # The progress as of `refreshed_at` (`runs/remaining.py`): the done share of the followed audio,
    # the remaining processing time and when it ends; None when unknown, the last two also once final.
    progress_fraction: ProgressFraction | None = None
    # `updated_at` (worker clock) of the first worker report that showed `transcribing` with a current job.
    transcribing_since: AwareDatetime | None = None
    remaining_estimate_s: NonNegativeInt | None = None
    estimated_finish_at: AwareDatetime | None = None

    def known_pod(self) -> KnownPod | None:
        """Return the run's pod with what is known of its sightings (None without a pod id).

        A pod not seen settles from its deploy (`resolvedAt`), else from when the run recorded it.
        """
        if self.pod_id is None:
            return None
        deployed = self.pod_deployed_at or self.pod_recorded_at
        return KnownPod(self.pod_id, seen=self.pod_seen, deployed_at=deployed)
