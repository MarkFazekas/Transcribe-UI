"""Run activity from the Runpod lists: live `transcribe-ui-*` pods and queued pods (plan m5 §1).

A pod counts in any status but `TERMINATED` (`EXITED` and `ERROR` pods still exist). A queued
pod counts in any status but `FAILED`, `EXPIRED` and `CANCELLED` (an unknown status counts: fail
closed). A `DEPLOYED` one is over once its name deadline has passed (nothing legitimate runs
after it; no lookup is made). Before that:

- its pod is listed (under any name): the pod's status decides (an app pod counts as itself);
- it has no `podId`: it counts;
- otherwise its pod is looked up by id: live counts, `TERMINATED` does not, and a 404 counts
  until `DEPLOY_SETTLE_S` after the deploy (`resolvedAt`; list lag), and also when the deploy
  time is missing or ahead of the clock.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from functools import partial

from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod, QueuedPod
from transcribe_ui.runs.classes import ActivityKind, ActivitySource
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, IGNORED_QUEUED_POD_STATUSES

# Every listed pod (app pods or not), by id.
type PodsById = Mapping[str, Pod]


def pod_sources(pods: PodsApi, now: datetime) -> list[ActivitySource]:
    """Return the live app pods and the queued pods that may still lead to one, by kind and name.

    Raises:
        RunpodApiError: A list or a pod could not be read.
    """
    every_pod = pods.list_pods()
    by_id = {pod.pod_id: pod for pod in every_pod}
    app_pods = [pod for pod in every_pod if is_app_pod_name(pod.name)]
    live = [pod for pod in app_pods if _is_live(pod)]
    queued = [entry for entry in pods.list_queued_pods() if is_app_pod_name(entry.name)]
    still_active = partial(queued_pod_active, listed=by_id, pods=pods, now=now)
    waiting = [entry for entry in queued if still_active(entry)]
    sources = [_source("pod", pod.name, pod.status) for pod in live]
    sources.extend(_queued_source(entry) for entry in waiting)
    return sorted(sources, key=_order)


def _is_live(pod: Pod) -> bool:
    return pod.status != POD_TERMINATED


def _source(kind: ActivityKind, name: str, status: str) -> ActivitySource:
    parsed = parse_pod_name(name)
    run_id = None if parsed is None else parsed.run_id
    return ActivitySource(kind=kind, name=name, status=status, run_id=run_id)


def _queued_source(entry: QueuedPod) -> ActivitySource:
    return _source("queued_pod", entry.name, entry.status)


def _order(source: ActivitySource) -> tuple[str, str]:
    return source.kind, source.name


def queued_pod_active(entry: QueuedPod, listed: PodsById, pods: PodsApi, now: datetime) -> bool:
    """Return whether a queued pod may still lead to a live pod the pod list does not show as itself.

    `listed` holds every listed pod by id. A deployed one's pod may be looked up by id.

    Raises:
        RunpodApiError: The lookup failed.
    """
    if entry.status in IGNORED_QUEUED_POD_STATUSES:
        return False
    if entry.status != QUEUED_POD_DEPLOYED:
        return True  # QUEUED, or a status this version does not know
    if _past_deadline(entry, now):
        return False
    pod_id = entry.pod_id
    if pod_id is None:
        return True  # deployed, but which pod is unknown
    return _deployed_pod_active(entry, pod_id, listed, pods, now)


def _deployed_pod_active(entry: QueuedPod, pod_id: str, listed: PodsById, pods: PodsApi, now: datetime) -> bool:
    pod = listed.get(pod_id)
    if pod is not None:
        # An app pod counts as itself; a pod renamed since counts through this entry.
        return _is_live(pod) and not is_app_pod_name(pod.name)
    found = pods.get_pod(pod_id)
    if found is not None:
        return _is_live(found)
    return not _settled(entry, now)


def _past_deadline(entry: QueuedPod, now: datetime) -> bool:
    parsed = parse_pod_name(entry.name)
    return parsed is not None and parsed.deadline <= now


def _settled(entry: QueuedPod, now: datetime) -> bool:
    """Return whether a 404 for the deployed pod means "gone": long enough after the deploy."""
    deployed = entry.resolved_at
    return deployed is not None and now - deployed >= timedelta(seconds=DEPLOY_SETTLE_S)
