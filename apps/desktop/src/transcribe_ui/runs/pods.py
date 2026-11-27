"""Run activity from the Runpod lists: live `transcribe-ui-*` pods and queued pods (plan m5 §1).

A pod counts in any status but `TERMINATED` (`EXITED` and `ERROR` pods still exist). A queued
pod counts in any status but `FAILED`, `EXPIRED` and `CANCELLED` (an unknown status counts: fail
closed). A `DEPLOYED` one counts until its pod is listed (the pod's own entry decides then),
reads `TERMINATED`, or has stayed out of the pod list for `DEPLOY_SETTLE_S` after the deploy
(or past its name deadline): a pod missing from the list, or a 404 for it, may be list lag
before that.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from functools import partial

from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import QueuedPod
from transcribe_ui.runs.classes import ActivityKind, ActivitySource
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, IGNORED_QUEUED_POD_STATUSES


def pod_sources(pods: PodsApi, now: datetime) -> list[ActivitySource]:
    """Return the live app pods and the queued pods that may still lead to one, by kind and name.

    Raises:
        RunpodApiError: A list or a pod could not be read.
    """
    listed = [pod for pod in pods.list_pods() if is_app_pod_name(pod.name)]
    queued = [entry for entry in pods.list_queued_pods() if is_app_pod_name(entry.name)]
    listed_ids = frozenset(pod.pod_id for pod in listed)
    live = [pod for pod in listed if pod.status != POD_TERMINATED]
    still_active = partial(_queued_active, listed_ids=listed_ids, pods=pods, now=now)
    waiting = [entry for entry in queued if still_active(entry)]
    sources = [_source("pod", pod.name, pod.status) for pod in live]
    sources.extend(_queued_source(entry) for entry in waiting)
    return sorted(sources, key=_order)


def _source(kind: ActivityKind, name: str, status: str) -> ActivitySource:
    return ActivitySource(kind=kind, name=name, status=status)


def _queued_source(entry: QueuedPod) -> ActivitySource:
    return _source("queued_pod", entry.name, entry.status)


def _order(source: ActivitySource) -> tuple[str, str]:
    return source.kind, source.name


def _queued_active(entry: QueuedPod, listed_ids: Collection[str], pods: PodsApi, now: datetime) -> bool:
    if entry.status in IGNORED_QUEUED_POD_STATUSES:
        return False
    pod_id = entry.pod_id
    if entry.status != QUEUED_POD_DEPLOYED or pod_id is None:
        return True  # QUEUED, a status this version does not know, or a deploy without its pod id
    if pod_id in listed_ids or _settled(entry, now):
        return False  # the pod's own entry counts if it is live; or the pod is gone
    found = pods.get_pod(pod_id)
    return found is None or found.status != POD_TERMINATED


def _settled(entry: QueuedPod, now: datetime) -> bool:
    """Return whether the deployed pod would be listed by now: long after the deploy, or past its deadline."""
    settle = timedelta(seconds=DEPLOY_SETTLE_S)
    deployed = entry.resolved_at
    if deployed is not None and now - deployed >= settle:
        return True
    parsed = parse_pod_name(entry.name)
    return parsed is not None and parsed.deadline <= now
