"""Run leases (A21, architecture §3.9 "Run scope"): `runs/<run_id>/launch.json` in R2.

The launching desktop writes a run's lease after committing the run row and before the pod
request: the covered batch ids and the pod name deadline. The worker processes only the batches
listed there, so a manifest published later is never processed by that run, on any computer.

A lease counts (is live) until its deadline, and no longer once the run's `status.json` is final
or stopped being written (a dead worker: the heartbeat rule of `runs/heartbeats.py`).
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
    HEARTBEAT_MARGIN_S,
    LAUNCH_SETTLE_S,
    LEASE_CONTENT_TYPE,
    LEASE_LIVE,
    LEASE_MAX_SPAN_S,
    UNREADABLE_STATE,
)
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.runs.heartbeats import recently_written
from transcribe_ui.state.classes import LiveLease
from transcribe_ui.state.leases import replace_known_leases
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectInfo, ObjectStore

# How long after its R2 write a lease can count at most.
MAX_SPAN = timedelta(seconds=LEASE_MAX_SPAN_S)
# How long after its write a lease counts without a pod of its run in the Runpod lists: the list
# lag, plus the clock difference between the R2 write time and this computer's clock.
LAUNCH_SETTLE = timedelta(seconds=LAUNCH_SETTLE_S + HEARTBEAT_MARGIN_S)


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
    """Read the live leases, then store them (`waits_for_current_run`; R2 is read outside the transaction).

    Raises:
        StorageError: R2 could not be read (the stored leases stay as they were).
    """
    leases = live_leases(store, now)
    with state.session() as session:
        replace_known_leases(session, leases)


def lease_sources(leases: tuple[LiveLease, ...]) -> list[ActivitySource]:
    """Return the leases as signs of a live run (`check_activity`)."""
    return [_source(lease) for lease in leases]


def batch_leases(store: ObjectStore, batch_id: str, now: datetime) -> tuple[LiveLease, ...]:
    """Return the live leases that list the batch: their runs may be working on it.

    Raises:
        StorageError: R2 could not be read (fail closed).
    """
    leases = live_leases(store, now)
    return tuple(lease for lease in leases if lease.lists(batch_id))


def refuse_if_leased(store: ObjectStore, batch_id: str, now: datetime) -> None:
    """Refuse while a live lease lists the batch (the per-batch check of cleanup and withdrawals).

    Raises:
        RunActiveError: A live lease lists the batch (`run_active`; nothing was changed).
        StorageError: R2 could not be read (fail closed).
    """
    listing = batch_leases(store, batch_id, now)
    if listing:
        raise RunActiveError(lease_sources(listing))


def concurrent_leases(store: ObjectStore, run_id: str, now: datetime) -> tuple[LiveLease, ...]:
    """Return the recent live leases of other runs: a launch on another computer at the same moment.

    Older leases were judged by the launch's activity check just before (`may_still_run`).

    Raises:
        StorageError: R2 could not be read (fail closed).
    """
    others = [lease for lease in live_leases(store, now) if lease.run_id != run_id]
    return tuple(lease for lease in others if may_still_run(lease, set(), now))


def may_still_run(lease: LiveLease, in_lists: set[str | None], now: datetime) -> bool:
    """Return whether the lease's run may still be live, given the run ids of the live Runpod entries.

    A lease written less than `LAUNCH_SETTLE_S` + `HEARTBEAT_MARGIN_S` ago (R2's write time
    against this computer's clock) counts: its pod may not be listed yet. An older one counts
    only while a live entry of its run is in the lists.
    """
    written = lease.written_at
    recent = written is None or now - written < LAUNCH_SETTLE
    return recent or lease.run_id in in_lists


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
    if body is None or _ended(store, run_id, now):
        return None  # deleted since the listing, or its worker is over
    written = found.modified
    try:
        lease = read_run_lease(key, body)
    except ContractError:
        return LiveLease(run_id=run_id, batch_ids=None, until=bound, written_at=written)
    until = min(lease.deadline_at, bound)
    if now >= until:
        return None
    listed = frozenset(lease.batch_ids)
    return LiveLease(run_id=run_id, batch_ids=listed, until=until, written_at=written)


def _ended(store: ObjectStore, run_id: str, now: datetime) -> bool:
    """Return whether the run's worker is over: its `status.json` is final or no longer written.

    No heartbeat yet (the pod may still be starting), or one that cannot be read: not over.
    """
    key = run_status_key(run_id)
    found = store.head(key)
    if found is None:
        return False
    if not recently_written(found, now):
        return True
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
