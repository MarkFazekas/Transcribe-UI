"""`reconcile_cloud`: the app's live pods and queued pods joined with the local runs (§3.7).

The GUI runs it at start-up. It lists `transcribe-ui-*` pods (any status but `TERMINATED`) and
queued pods that may still lead to a pod (the activity check's rule: not `FAILED`, `EXPIRED` or
`CANCELLED`; a `DEPLOYED` one only before its name deadline, while its pod is not listed as
itself and a lookup does not show it terminated or settled away), resolves `launching` runs
by their names (`runs/settling.py`), and reports each entry as belonging to a run that is not final or as a
straggler, and whether it is past its name deadline. A straggler of a final run whose objects
were collected marks the run uncollected, since it may have written some again (the next
`refresh_status` collects them; refresh never calls reconcile, so nothing loops). Under the runs
lock; it stops nothing itself (`terminate_pod`, `cancel_queued_pod`).
"""

from datetime import datetime

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.classes import ReconcileCloudRequest
from transcribe_ui.runs.cloud import open_runpod
from transcribe_ui.runs.collect import reopen_collection
from transcribe_ui.runs.entries import live_entries, needs_attention
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.records import open_runs, read_run, run_statuses
from transcribe_ui.runs.responses import CloudEntry, ReconcileCloudResponse, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.state.store import LocalState


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
        statuses = run_statuses(state)
        entries = runpod_call(lambda: live_entries(listed, client.pods, statuses, now))
        _reopen_stragglers(state, entries, now)
    attention = any(needs_attention(entry) for entry in entries)
    return ReconcileCloudResponse(entries=entries, runs=runs, attention=attention)


def _reopen_stragglers(state: LocalState, entries: tuple[CloudEntry, ...], now: datetime) -> None:
    """Mark the final, collected runs of stragglers uncollected: they may have written run objects again."""
    for straggler in entries:
        if straggler.role == "straggler":
            reopen_collection(state, straggler.run_id, now)


def _resolve_runs(state: LocalState, listed: Listed, now: datetime) -> tuple[RunInfo, ...]:
    """Resolve the `launching` runs; return every run that was not final, as it is now."""
    before = open_runs(state)
    for run in before:
        if run.status == "launching":
            resolve_launching(state, run.run_id, listed, now)
    return tuple(read_run(state, earlier.run_id) for earlier in before)
