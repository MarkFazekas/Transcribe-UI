"""Resolving a `launching` run by its pod name (plan m5 §1 "The run is recorded before the pod is requested").

A run stays `launching` while the answer to its pod request is unknown. A pod or queued pod with
the run's name in the Runpod lists is adopted: its id is stored and the run follows it. The run
becomes `launch_failed` only when `LAUNCH_SETTLE_S` has passed since the request and two lookups
after that, at least `LAUNCH_LOOKUP_GAP_S` apart, found the name in neither list (one empty list
may be lag or a glitch). A pod that shows up after that is a straggler (`reconcile_cloud`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_CANCELLED, QUEUED_POD_QUEUED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod, QueuedPod
from transcribe_ui.runs.constants import (
    FINAL_RUN_STATUSES,
    IGNORED_QUEUED_POD_STATUSES,
    LAUNCH_LOOKUP_GAP_S,
    LAUNCH_SETTLE_S,
)
from transcribe_ui.runs.errors import LaunchUnresolvedError, runpod_call
from transcribe_ui.runs.records import PayloadChanges, load_run, payload_of, read_run, update_run
from transcribe_ui.runs.responses import RunInfo
from transcribe_ui.state.models import RunStatus
from transcribe_ui.state.store import LocalState

# Time after the pod request before an empty lookup counts.
SETTLE = timedelta(seconds=LAUNCH_SETTLE_S)
# Time between the two empty lookups.
LOOKUP_GAP = timedelta(seconds=LAUNCH_LOOKUP_GAP_S)


@dataclass(frozen=True)
class Listed:
    """The Runpod pod and queued-pod lists as read once."""

    pods: tuple[Pod, ...]
    queued: tuple[QueuedPod, ...]

    @classmethod
    def read(cls, pods: PodsApi) -> "Listed":
        """Read both lists.

        Raises:
            RunpodApiError: A list could not be read.
        """
        return cls(pods.list_pods(), pods.list_queued_pods())


def live_pod_named(listed: Listed, name: str, excluding: frozenset[str]) -> Pod | None:
    """Return a live pod (not `TERMINATED`) listed under the name, other than those `excluding`; None if none."""
    named = [pod for pod in listed.pods if pod.name == name]
    others = [pod for pod in named if pod.pod_id not in excluding]
    live = [pod for pod in others if pod.status != POD_TERMINATED]
    return live[0] if live else None


def queue_pod(listed: Listed, queued_pod_id: str | None, excluding: frozenset[str]) -> QueuedPod | None:
    """Return the listed queued pod of the id when it names a pod not excluded and not listed `TERMINATED`.

    Such a pod may still bill even when the pod list does not show it yet (lag after a deploy).
    """
    entries = [entry for entry in listed.queued if entry.queued_pod_id == queued_pod_id]
    deployed = [entry for entry in entries if entry.pod_id is not None]
    named = [entry for entry in deployed if entry.pod_id not in excluding]
    terminated = {pod.pod_id for pod in listed.pods if pod.status == POD_TERMINATED}
    found = [entry for entry in named if entry.pod_id not in terminated]
    return found[0] if found else None


def deployed_pod_changes(entry: QueuedPod) -> dict[str, object]:
    """Return the payload fields that make a run follow the pod its queued pod names (not seen yet)."""
    return {"pod_id": entry.pod_id, "pod_seen": False, "pod_deployed_at": entry.resolved_at}


def queue_waiting(listed: Listed, queued_pod_id: str | None) -> bool:
    """Return whether the queued pod is listed and still waits (`QUEUED`): it may still deploy."""
    waiting = [entry for entry in listed.queued if entry.queued_pod_id == queued_pod_id]
    return any(entry.status == QUEUED_POD_QUEUED for entry in waiting)


@dataclass(frozen=True)
class Adoption:
    """The pod or queued pod found under a run's name, and the run status it gives."""

    status: RunStatus
    pod_id: str | None
    queued_pod_id: str | None
    # Whether the pod list showed the pod (False: known only from the queued pod's `podId`).
    pod_seen: bool = False
    # The queued pod's deploy time, for a pod that was not seen.
    pod_deployed_at: datetime | None = None

    def changes(self) -> PayloadChanges:
        """Return the payload fields the adoption sets."""
        return {
            "pod_id": self.pod_id,
            "queued_pod_id": self.queued_pod_id,
            "pod_seen": self.pod_seen,
            "pod_deployed_at": self.pod_deployed_at,
        }


def find_by_name(name: str, listed: Listed) -> Adoption | None:
    """Return what the lists hold under this name (a live pod before a terminated one), or None."""
    named = [pod for pod in listed.pods if pod.name == name]
    pods = sorted(named, key=_terminated_last)
    queued_named = [entry for entry in listed.queued if entry.name == name]
    queued = queued_named[0] if queued_named else None
    queued_pod_id = None if queued is None else queued.queued_pod_id
    if pods:
        # A pod exists (maybe ended already): the run follows it (`refresh_status` reads its end).
        return Adoption("running", pods[0].pod_id, queued_pod_id, pod_seen=True)
    if queued is None:
        return None
    if queued.pod_id is not None:
        return Adoption("running", queued.pod_id, queued_pod_id, pod_deployed_at=queued.resolved_at)
    return Adoption(_queued_run_status(queued.status), None, queued_pod_id)


def _terminated_last(pod: Pod) -> bool:
    return pod.status == POD_TERMINATED


def _queued_run_status(status: str) -> RunStatus:
    if status == QUEUED_POD_CANCELLED:
        return "cancelled"
    if status in IGNORED_QUEUED_POD_STATUSES:
        return "failed"  # expired or failed without a pod
    if status == QUEUED_POD_QUEUED:
        return "queued"
    # DEPLOYED without a pod id yet, or a status this version does not know: it may run.
    return "running"


def resolve_launching(state: LocalState, run_id: str, listed: Listed, now: datetime) -> RunInfo:
    """Adopt the run's pod or queued pod from the lists, or apply the settling rule; return the run."""
    with state.session() as session:
        row = load_run(session, run_id)
        name = row.pod_name
        payload = payload_of(row)
        sent = (payload.posted_at, payload.sending_at, row.created_at)
        # The latest moment the request may have been sent (a retry after a 429 waits).
        settle_from = max(moment for moment in sent if moment is not None)
    found = find_by_name(name, listed)
    if found is not None:
        # Found: an empty lookup recorded before no longer counts.
        changes = {**found.changes(), "first_empty_lookup_at": None}
        if found.status in FINAL_RUN_STATUSES:
            # A queued pod found ended (cancelled, expired or failed) without a pod.
            changes = {**changes, "ended_at": now, "end_reason": "queue_ended"}
        return update_run(state, run_id, now, found.status, changes)
    first = payload.first_empty_lookup_at
    if now - settle_from < SETTLE:
        return read_run(state, run_id)  # too early: the pod may not be listed yet
    if first is None:
        return update_run(state, run_id, now, changes={"first_empty_lookup_at": now})
    if now - first >= LOOKUP_GAP:
        return update_run(state, run_id, now, "launch_failed", {"ended_at": now, "end_reason": "launch_failed"})
    return read_run(state, run_id)


def resolve_for_stop(state: LocalState, pods: PodsApi, run_id: str, now: datetime) -> RunInfo:
    """Resolve a `launching` run before it is stopped (one lookup of both lists).

    Raises:
        LaunchUnresolvedError: Its pod is not listed (yet); nothing can be stopped.
        DomainError: The lists could not be read.
    """
    listed = runpod_call(lambda: Listed.read(pods))
    run = resolve_launching(state, run_id, listed, now)
    if run.status == "launching":
        raise LaunchUnresolvedError(run_id)
    return run
