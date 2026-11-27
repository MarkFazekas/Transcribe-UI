"""The app's live pods and queued pods as `reconcile_cloud` reports them, joined with the local runs."""

from collections.abc import Mapping
from datetime import datetime
from functools import partial

from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES, POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.pods import queued_pod_active
from transcribe_ui.runs.responses import CloudEntry
from transcribe_ui.runs.settling import Listed
from transcribe_ui.runs.stopping import CloudObject

# Local run statuses by run id.
type Statuses = Mapping[str, str]


def live_entries(listed: Listed, pods: PodsApi, statuses: Statuses, now: datetime) -> tuple[CloudEntry, ...]:
    """Return the app's live pods and queued pods, by kind and name.

    Queued pods follow the activity check's rule (`runs/pods.py`), so a `DEPLOYED` one shows only
    while its pod may be live and is not listed as itself.

    Raises:
        RunpodApiError: A deployed pod's lookup failed.
    """
    by_id = {pod.pod_id: pod for pod in listed.pods}
    found = [
        CloudObject(POD_KIND, pod.pod_id, pod.name, pod.status)
        for pod in listed.pods
        if is_app_pod_name(pod.name) and pod.status != POD_TERMINATED
    ]
    waiting = partial(queued_pod_active, listed=by_id, pods=pods, now=now)
    found.extend(
        CloudObject(QUEUED_POD_KIND, entry.queued_pod_id, entry.name, entry.status, entry.pod_id)
        for entry in listed.queued
        if is_app_pod_name(entry.name) and waiting(entry)
    )
    entries = [_entry(each, statuses, now) for each in found]
    return tuple(sorted(entries, key=_entry_order))


def _entry_order(entry: CloudEntry) -> tuple[str, str, str]:
    return entry.kind, entry.name, entry.entry_id


def needs_attention(entry: CloudEntry) -> bool:
    """Return whether the entry is a straggler or past its name deadline."""
    return entry.role == "straggler" or entry.overdue


def _entry(found: CloudObject, statuses: Statuses, now: datetime) -> CloudEntry:
    parsed = parse_pod_name(found.name)
    run_id = None if parsed is None else parsed.run_id
    deadline = None if parsed is None else parsed.deadline
    local = None if run_id is None else statuses.get(run_id)
    current = local is not None and local not in FINAL_RUN_STATUSES
    return CloudEntry.model_validate(
        {
            "kind": found.kind,
            "entry_id": found.object_id,
            "name": found.name,
            "status": found.status,
            "pod_id": found.pod_id,
            "run_id": run_id,
            "deadline": deadline,
            "local_status": local,
            "role": "current" if current else "straggler",
            "overdue": deadline is not None and deadline <= now,
        },
    )
