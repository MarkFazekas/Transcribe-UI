"""The `runs` rows: written before the pod is requested, then following it (§3.7).

Every change happens under the runs lock, so a run row has one writer at a time; each change is
its own short transaction, never open across a Runpod call.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.runs.constants import FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import RunNotFoundError
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.responses import RunInfo
from transcribe_ui.state.models import RunRow, RunStatus
from transcribe_ui.state.store import LocalState

# Payload fields a change sets, by name.
type PayloadChanges = Mapping[str, object]


def payload_of(row: RunRow) -> RunPayload:
    """Return the run's payload."""
    return RunPayload.model_validate(row.payload)


def run_info(row: RunRow) -> RunInfo:
    """Return the run as the frontends show it."""
    payload = payload_of(row)
    return RunInfo.model_validate(
        {
            "run_id": row.run_id,
            "status": row.status,
            "pod_name": row.pod_name,
            "pod_id": payload.pod_id,
            "queued_pod_id": payload.queued_pod_id,
            "test_mode": payload.test_mode,
            "max_runtime_s": payload.max_runtime_s,
            "queue_expires_at": payload.queue_expires_at,
            "name_deadline": payload.name_deadline,
            "covered_batch_ids": payload.covered_batch_ids,
            "created_at": row.created_at,
        },
    )


def load_run(session: Session, run_id: str) -> RunRow:
    """Return the run's row.

    Raises:
        RunNotFoundError: The database does not know the run.
    """
    row = session.get(RunRow, run_id)
    if row is None:
        raise RunNotFoundError(run_id)
    return row


def read_payload(state: LocalState, run_id: str) -> RunPayload:
    """Return the run's payload (its own transaction)."""
    with state.session() as session:
        return payload_of(load_run(session, run_id))


def read_run(state: LocalState, run_id: str) -> RunInfo:
    """Return the run (its own transaction)."""
    with state.session() as session:
        return run_info(load_run(session, run_id))


def insert_run(state: LocalState, run_id: str, pod_name: str, now: datetime, payload: RunPayload) -> RunInfo:
    """Record a new run in `launching` and commit it (before the pod is requested)."""
    status: RunStatus = "launching"
    row = RunRow(
        run_id=run_id,
        status=status,
        pod_name=pod_name,
        created_at=now,
        updated_at=now,
        payload=payload.model_dump(mode="json"),
    )
    with state.session() as session:
        session.add(row)
        session.flush()
        return run_info(row)


def update_run(
    state: LocalState,
    run_id: str,
    now: datetime,
    status: RunStatus | None = None,
    changes: PayloadChanges | None = None,
) -> RunInfo:
    """Set the run's status (if given) and payload fields, and commit.

    A pod id the run records for the first time also records `pod_recorded_at` (now).
    """
    with state.session() as session:
        row = load_run(session, run_id)
        earlier = payload_of(row)
        updates = dict(changes or {})
        recorded = updates.get("pod_id")
        if recorded is not None and recorded != earlier.pod_id:
            # The settle anchor of a pod first known from its queued pod without `resolvedAt`.
            updates.setdefault("pod_recorded_at", now)
        payload = earlier.model_copy(update=updates)
        # Validated again, so a wrong field type fails here rather than when read back.
        row.payload = RunPayload.model_validate(payload.model_dump()).model_dump(mode="json")
        if status is not None:
            row.status = status
        row.updated_at = now
        session.flush()
        return run_info(row)


def run_statuses(state: LocalState) -> dict[str, str]:
    """Return every local run's status, by run id."""
    with state.session() as session:
        rows = session.execute(select(RunRow.run_id, RunRow.status)).all()
    return dict(rows)


def _stopped_is_recorded(payload: RunPayload, changes: PayloadChanges) -> bool:
    recorded = {payload.pod_id, payload.queued_pod_id} - {None}
    stopped_ids = (changes.get("pod_id"), changes.get("queued_pod_id"))
    stopped = set(stopped_ids) - {None}
    return not recorded or bool(recorded & stopped)


def open_runs(state: LocalState) -> list[RunInfo]:
    """Return every local run that is not final, oldest first."""
    not_final = RunRow.status.not_in(FINAL_RUN_STATUSES)
    query = select(RunRow).where(not_final).order_by(RunRow.run_id)
    with state.session() as session:
        return [run_info(row) for row in session.scalars(query)]


def cancel_local_run(state: LocalState, run_id: str | None, now: datetime, changes: PayloadChanges) -> RunStatus | None:
    """Mark the local run of that id `cancelled` (with the payload fields that are set) if it is not final.

    Only when what was stopped (the `pod_id` and `queued_pod_id` of `changes`) is what the run
    recorded, or the run recorded neither: a stray pod carrying the run's name does not end it.
    Return its status now; None when the id is None or this database does not know the run.
    """
    if run_id is None:
        return None
    status = run_statuses(state).get(run_id)
    if status is None:
        return None
    if status in FINAL_RUN_STATUSES or not _stopped_is_recorded(read_payload(state, run_id), changes):
        return read_run(state, run_id).status
    known = {field: change for field, change in changes.items() if change}
    cancelled = update_run(state, run_id, now, "cancelled", known)
    return cancelled.status


def has_uncollected(state: LocalState) -> bool:
    """Return whether a final run's objects are still to be collected."""
    return bool(uncollected_runs(state))


class Uncollected(NamedTuple):
    """A final run whose objects are still to be collected."""

    run_id: str
    pod_name: str
    payload: RunPayload


def _uncollected_of(row: RunRow) -> Uncollected:
    return Uncollected(row.run_id, row.pod_name, payload_of(row))


def uncollected_runs(state: LocalState) -> list[Uncollected]:
    """Return the final runs not collected yet, by run id."""
    final = RunRow.status.in_(FINAL_RUN_STATUSES)
    with state.session() as session:
        query = select(RunRow).where(final).order_by(RunRow.run_id)
        rows = list(session.scalars(query))
        found = [_uncollected_of(row) for row in rows]
    return [each for each in found if not each.payload.objects_collected]


def reopen_collection(state: LocalState, run_id: str | None, now: datetime) -> None:
    """Mark a final, collected run uncollected: a straggler carrying its name was listed or stopped.

    It may have written run objects again; `refresh_status` collects them once no live straggler
    carries the name any more (`runs/lingering.py`).
    """
    if run_id is None:
        return
    final = RunRow.status.in_(FINAL_RUN_STATUSES)
    with state.session() as session:
        query = select(RunRow).where(RunRow.run_id == run_id, final)
        row = session.scalar(query)
        collected = row is not None and payload_of(row).objects_collected
    if collected:
        update_run(state, run_id, now, changes={"objects_collected": False})
