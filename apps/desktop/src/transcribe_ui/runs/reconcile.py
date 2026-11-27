"""`reconcile_cloud`: the app's live pods and queued pods joined with the local runs (§3.7).

The GUI runs it at start-up. It lists `transcribe-ui-*` pods (any status but `TERMINATED`) and
queued pods that may still lead to a pod (not `FAILED`, `EXPIRED` or `CANCELLED`; a `DEPLOYED`
one only while its pod is not listed), resolves `launching` runs by their names
(`runs/settling.py`), and reports each entry as belonging to a run that is not final or as a
straggler, and whether it is past its name deadline. Under the runs lock; it stops nothing
itself (`terminate_pod`, `cancel_queued_pod`).
"""

from collections.abc import Mapping
from datetime import datetime

from transcribe_contracts.pods import is_app_pod_name, parse_pod_name
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runs.classes import ReconcileCloudRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.constants import FINAL_RUN_STATUSES, IGNORED_QUEUED_POD_STATUSES, POD_KIND, QUEUED_POD_KIND
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.records import open_runs, read_run, run_statuses
from transcribe_ui.runs.responses import CloudEntry, ReconcileCloudResponse, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.runs.stopping import CloudObject
from transcribe_ui.state.store import LocalState

# Local run statuses by run id.
type Statuses = Mapping[str, str]


def reconcile_cloud(_request: ReconcileCloudRequest, context: RequestContext) -> ReconcileCloudResponse:
    """Report the app's live pods and queued pods and the local runs; resolve `launching` runs.

    Raises:
        DomainError: The Runpod lists could not be read (nothing was changed).
    """
    ports = context.ports
    state = ports.state
    with state.locks.runs(context.cancel.is_cancelled), open_runpod(context) as client:
        listed = runpod_call(lambda: Listed.read(client.pods))
        now = ports.clock.now()
        runs = _resolve_runs(state, listed, now)
        entries = live_entries(listed, run_statuses(state), now)
    attention = any(_needs_attention(entry) for entry in entries)
    return ReconcileCloudResponse(entries=entries, runs=runs, attention=attention)


def _resolve_runs(state: LocalState, listed: Listed, now: datetime) -> tuple[RunInfo, ...]:
    """Resolve the `launching` runs; return every run that was not final, as it is now."""
    before = open_runs(state)
    for run in before:
        if run.status == "launching":
            resolve_launching(state, run.run_id, listed, now)
    return tuple(read_run(state, earlier.run_id) for earlier in before)


def live_entries(listed: Listed, statuses: Statuses, now: datetime) -> tuple[CloudEntry, ...]:
    """Return the app's live pods and queued pods, by kind and name."""
    listed_ids = {pod.pod_id for pod in listed.pods}
    found = [
        CloudObject(POD_KIND, pod.pod_id, pod.name, pod.status)
        for pod in listed.pods
        if is_app_pod_name(pod.name) and pod.status != POD_TERMINATED
    ]
    found.extend(
        CloudObject(QUEUED_POD_KIND, entry.queued_pod_id, entry.name, entry.status, entry.pod_id)
        for entry in listed.queued
        if is_app_pod_name(entry.name) and _waiting(entry.status, entry.pod_id, listed_ids)
    )
    entries = [_entry(each, statuses, now) for each in found]
    return tuple(sorted(entries, key=_entry_order))


def _entry_order(entry: CloudEntry) -> tuple[str, str, str]:
    return entry.kind, entry.name, entry.entry_id


def _needs_attention(entry: CloudEntry) -> bool:
    return entry.role == "straggler" or entry.overdue


def _waiting(status: str, pod_id: str | None, listed_ids: set[str]) -> bool:
    """Return whether a queued pod may still lead to a pod the pod list does not show."""
    if status in IGNORED_QUEUED_POD_STATUSES:
        return False
    return status != QUEUED_POD_DEPLOYED or pod_id is None or pod_id not in listed_ids


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
