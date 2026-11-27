"""Following a run's queued pod and pod in Runpod (`refresh_status`, architecture §3.7).

A `launching` run is resolved by its name first (`runs/settling.py`). A run without a pod id
follows its queued pod: a pod listed under the run's name is adopted (seen); otherwise the queued
pod (listed, else read by id) decides: still waiting, deployed (its pod recorded as not seen),
or ended without a pod (the run fails; a queued pod Runpod no longer knows counts as ended only
`DEPLOY_SETTLE_S` after the queue's `expiresAt`, when a pod deployed just before it would be
listed). A run with a pod id follows that pod (listed, else read by id): a read records the
sighting, its status and price, and the first `RUNNING` read (the desktop deadline counts from
it). The pod is gone when it reads `TERMINATED`, or is missing after it was seen or after its
deploy settled (`KnownPod`); a missing pod that was never seen may be list lag and is followed.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from transcribe_ui.runpod.constants import POD_RUNNING, POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.records import PayloadChanges, read_payload, read_run, update_run
from transcribe_ui.runs.responses import RunInfo
from transcribe_ui.runs.settling import Adoption, Listed, find_by_name, resolve_launching
from transcribe_ui.runs.stopping import KnownPod
from transcribe_ui.state.store import LocalState

# Time after the queue's `expiresAt` after which a queued pod Runpod no longer knows has ended.
QUEUE_SETTLE = timedelta(seconds=DEPLOY_SETTLE_S)


@dataclass(frozen=True)
class Followed:
    """A run after following it once, and its pod as read now."""

    run: RunInfo
    # The pod as read now (list or lookup); None when no pod is known or it was not found.
    pod: Pod | None = None
    # Whether the pod is gone for sure: it reads `TERMINATED`, or is missing after it was seen or
    # after its deploy settled.
    gone: bool = False


def follow_run(state: LocalState, pods: PodsApi, run_id: str, listed: Listed, now: datetime) -> Followed:
    """Bring a run that is not final up to date with the lists (read once) and lookups by id.

    Raises:
        DomainError: A lookup by id failed (`cloud_unreachable` or a key error); what was
            recorded before it stays.
    """
    run = read_run(state, run_id)
    if run.status == "launching":
        run = resolve_launching(state, run_id, listed, now)
    if _settled(run):
        return Followed(run)
    if read_payload(state, run_id).pod_id is None:
        run = _follow_queued(state, pods, run, listed, now)
    if _settled(run):
        return Followed(run)
    return _follow_pod(state, pods, run, listed, now)


def _settled(run: RunInfo) -> bool:
    """Return whether there is nothing to follow: the run is final, or its pod request still unresolved."""
    return run.status in FINAL_RUN_STATUSES or run.status == "launching"


def _follow_queued(state: LocalState, pods: PodsApi, run: RunInfo, listed: Listed, now: datetime) -> RunInfo:
    payload = read_payload(state, run.run_id)
    found = find_by_name(run.pod_name, listed)
    queued_pod_id = payload.queued_pod_id
    if found is None and queued_pod_id is not None:
        entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
        if entry is None:
            return _queue_vanished(state, run, payload, now)
        found = find_by_name(run.pod_name, Listed((), (entry,)))
    if found is None:
        return run
    return update_run(state, run.run_id, now, found.status, _adopted(found, now))


def _adopted(found: Adoption, now: datetime) -> PayloadChanges:
    """Return the payload fields of an adoption, with the end of a queued pod that ended without a pod."""
    changes = dict(found.changes())
    if found.status in FINAL_RUN_STATUSES:
        changes.update({"ended_at": now, "end_reason": "queue_ended"})
    return changes


def _queue_vanished(state: LocalState, run: RunInfo, payload: RunPayload, now: datetime) -> RunInfo:
    """Fail a run whose queued pod Runpod no longer knows, once no pod of it can still show up."""
    if now - payload.queue_expires_at < QUEUE_SETTLE:
        return run
    return update_run(state, run.run_id, now, "failed", {"ended_at": now, "end_reason": "queue_ended"})


def _follow_pod(state: LocalState, pods: PodsApi, run: RunInfo, listed: Listed, now: datetime) -> Followed:
    payload = read_payload(state, run.run_id)
    pod_id = payload.pod_id
    if pod_id is None:
        return Followed(run)
    pod = _listed_pod(listed, pod_id)
    if pod is None:
        pod = runpod_call(lambda: pods.get_pod(pod_id))
    if pod is None:
        deployed = payload.pod_deployed_at
        known = KnownPod(pod_id, seen=payload.pod_seen, deployed_at=deployed)
        return Followed(run, gone=known.gone_when_missing(now))
    changes = _sighting(payload, pod, now)
    followed = update_run(state, run.run_id, now, changes=changes)
    return Followed(followed, pod, gone=pod.status == POD_TERMINATED)


def _listed_pod(listed: Listed, pod_id: str) -> Pod | None:
    found = [pod for pod in listed.pods if pod.pod_id == pod_id]
    return found[0] if found else None


def _sighting(payload: RunPayload, pod: Pod, now: datetime) -> PayloadChanges:
    """Return what a pod read records: the sighting, the status and price, and the first `RUNNING` read."""
    changes: dict[str, object] = {"pod_seen": True, "pod_status": pod.status}
    if pod.cost is not None and pod.cost >= 0:
        changes["price_per_hour_usd"] = pod.cost
    if payload.pod_created_at is None:
        changes["pod_created_at"] = pod.created_at or now
    if pod.status == POD_RUNNING and payload.first_running_at is None:
        changes["first_running_at"] = now
    return changes
