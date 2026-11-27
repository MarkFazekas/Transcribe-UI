"""Response models of the run requests (architecture §3.7)."""

from typing import Literal

from pydantic import AwareDatetime, PositiveInt

from transcribe_ui.api.base import FrozenModel
from transcribe_ui.runs.classes import LaunchPlan, RunpodKind
from transcribe_ui.state.models import RunStatus

# How `launch_worker` ended: a pod (direct start), a queued pod, an unknown outcome (the run
# stays `launching`; `refresh_status` or `reconcile_cloud` resolves it by its name), or, after a
# lost answer, a queued pod found already ended (the run is `cancelled` or `failed`).
type LaunchOutcome = Literal["pod", "queued", "unconfirmed", "ended"]
# How a live app pod or queued pod relates to the local runs: it belongs to a run that is not
# final, or it is a straggler (no local run, a final one, or a name that does not parse).
type EntryRole = Literal["current", "straggler"]
# What stopping a queued pod did: cancelled it (or it had ended), or terminated the pod it deployed.
type QueuedStop = Literal["cancelled", "pod_terminated"]


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
