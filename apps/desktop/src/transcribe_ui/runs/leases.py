"""Run leases (A21, architecture §3.9 "Run scope"): `runs/<run_id>/launch.json` in R2.

The launching desktop writes a run's lease after committing the run row and before the pod
request: the covered batch ids and the pod name deadline. The worker processes only the batches
listed there, so a manifest published later is never processed by that run, on any computer.

A lease counts (is live) until its deadline, and no longer once the run's `status.json` is final.
Every lease is bounded by its R2 `LastModified` + `LEASE_MAX_SPAN_S` (the longest a run can last):
a readable deadline is capped at that bound, an unreadable lease inside it counts and lists every
batch, and an older lease never counts, so nothing blocks for ever. Reading fails closed: an R2
error is raised, never "no lease".
"""

from datetime import datetime, timedelta

from transcribe_contracts.classes import RunLease
from transcribe_contracts.constants import RUNS_PREFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import run_id_from_lease_key, run_lease_key, run_status_key
from transcribe_contracts.reading import read_run_lease, read_run_status
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.runs.constants import (
    FINAL_WORKER_STATES,
    LEASE_CONTENT_TYPE,
    LEASE_LIVE,
    LEASE_MAX_SPAN_S,
    UNREADABLE_STATE,
)
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.state.classes import LeaseSnapshot, LiveLease
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectInfo, ObjectStore

# How long after its R2 write a lease can count at most.
MAX_SPAN = timedelta(seconds=LEASE_MAX_SPAN_S)


def write_lease(store: ObjectStore, run_id: str, batch_ids: tuple[str, ...], deadline: datetime) -> None:
    """Write the run's lease: the batches it may process, until the pod name deadline.

    Raises:
        StorageError: R2 failed (the object may or may not be stored).
    """
    lease = RunLease(run_id=run_id, batch_ids=batch_ids, deadline_at=deadline)
    store.write(run_lease_key(run_id), lease.model_dump_json().encode(), LEASE_CONTENT_TYPE)


def delete_lease(store: ObjectStore, run_id: str) -> None:
    """Delete the run's lease (deleting a missing one is not an error).

    Raises:
        StorageError: R2 failed.
    """
    store.delete(run_lease_key(run_id))


def live_leases(store: ObjectStore, now: datetime) -> tuple[LiveLease, ...]:
    """Return every lease that counts now, by key.

    Raises:
        StorageError: R2 could not be listed or read.
    """
    listed = [found for found in store.list_objects(RUNS_PREFIX) if run_id_from_lease_key(found.key)]
    leases = [_live(store, found, now) for found in listed]
    return tuple(lease for lease in leases if lease is not None)


def remember_leases(state: LocalState, store: ObjectStore, now: datetime) -> None:
    """Read the live leases and keep them in memory with the reading time (`waits_for_current_run`).

    Raises:
        StorageError: R2 could not be read.
    """
    leases = live_leases(store, now)
    state.leases.remember(LeaseSnapshot(read_at=now, leases=leases))


def lease_sources(leases: tuple[LiveLease, ...]) -> list[ActivitySource]:
    """Return the leases as signs of a live run (`check_activity`)."""
    return [_source(lease) for lease in leases]


def refuse_if_leased(store: ObjectStore, batch_id: str, now: datetime) -> None:
    """Refuse while a live lease lists the batch: its run may be working on it.

    Raises:
        RunActiveError: A live lease lists the batch (`run_active`; nothing was changed).
        StorageError: R2 could not be read (fail closed).
    """
    listing = [lease for lease in live_leases(store, now) if lease.lists(batch_id)]
    if listing:
        raise RunActiveError(lease_sources(tuple(listing)))


def _bound(found: ObjectInfo, now: datetime) -> datetime:
    """Return the latest moment the lease can count: its R2 write time (unknown: now) + the span."""
    written = found.modified or now
    return written + MAX_SPAN


def _live(store: ObjectStore, found: ObjectInfo, now: datetime) -> LiveLease | None:
    key = found.key
    run_id = run_id_from_lease_key(key)
    bound = _bound(found, now)
    if run_id is None or now >= bound:
        return None
    body = store.read(key)
    if body is None:
        return None  # deleted since the listing
    try:
        lease = read_run_lease(key, body)
    except ContractError:
        return LiveLease(run_id=run_id, batch_ids=None, until=bound)
    until = min(lease.deadline_at, bound)
    if now >= until or _finished(store, run_id):
        return None
    return LiveLease(run_id=run_id, batch_ids=frozenset(lease.batch_ids), until=until)


def _finished(store: ObjectStore, run_id: str) -> bool:
    """Return whether the run's worker reported its end (`status.json` final); unreadable or missing: no."""
    key = run_status_key(run_id)
    body = store.read(key)
    if body is None:
        return False
    try:
        status = read_run_status(key, body)
    except ContractError:
        return False
    return status.state in FINAL_WORKER_STATES


def _source(lease: LiveLease) -> ActivitySource:
    status = UNREADABLE_STATE if lease.batch_ids is None else LEASE_LIVE
    key = run_lease_key(lease.run_id)
    return ActivitySource(kind="lease", name=key, status=status, run_id=lease.run_id)
