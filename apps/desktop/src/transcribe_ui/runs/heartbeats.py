"""Run activity from worker heartbeats: `runs/<run_id>/status.json` in R2 (plan m5 §1).

The worker overwrites its heartbeat every ~30 s. One counts as a live run when:

- it was written (R2's `LastModified`) within `HEARTBEAT_STALE_S` + `HEARTBEAT_MARGIN_S` of the
  clock: an older object is a dead worker's, even if it cannot be read, so nothing blocks for
  ever; and
- it cannot be read (invalid, or another schema major version: fail closed), or its state is
  not final and its `updated_at` lies within `HEARTBEAT_STALE_S` of the clock. An `updated_at`
  ahead of the clock counts, but is taken as at most the run's `deadline_at` plus the margin and
  `LastModified` plus `HEARTBEAT_STALE_S`, so a worker clock far in the future cannot block
  either.
"""

from datetime import datetime, timedelta

from transcribe_contracts.constants import RUNS_PREFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.reading import read_run_status
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.runs.constants import (
    FINAL_WORKER_STATES,
    HEARTBEAT_MARGIN_S,
    HEARTBEAT_STALE_S,
    STATUS_KEY_SUFFIX,
    UNREADABLE_STATE,
)
from transcribe_ui.storage.r2 import ObjectInfo, ObjectStore

# How long after its last write a heartbeat can still count.
STALE = timedelta(seconds=HEARTBEAT_STALE_S)
# Clock difference and upload delay tolerated on top.
MARGIN = timedelta(seconds=HEARTBEAT_MARGIN_S)


def heartbeat_sources(store: ObjectStore, now: datetime) -> list[ActivitySource]:
    """Return the heartbeats of live (or unreadable, recently written) worker runs, by key.

    Raises:
        StorageError: R2 could not be listed or read.
    """
    listed = store.list_objects(RUNS_PREFIX)
    heartbeats = [found for found in listed if found.key.endswith(STATUS_KEY_SUFFIX)]
    recent = [found for found in heartbeats if _recent(found, now)]
    sources = [_heartbeat(store, found, now) for found in recent]
    return [source for source in sources if source is not None]


def _recent(found: ObjectInfo, now: datetime) -> bool:
    """Return whether the object was written recently enough to be a live worker's (unknown: yes)."""
    written = found.modified
    return written is None or now - written <= STALE + MARGIN


def _run_id(key: str) -> str:
    return key.removeprefix(RUNS_PREFIX).removesuffix(STATUS_KEY_SUFFIX)


def _heartbeat(store: ObjectStore, found: ObjectInfo, now: datetime) -> ActivitySource | None:
    key = found.key
    body = store.read(key)
    if body is None:
        return None  # deleted since the listing
    try:
        status = read_run_status(key, body)
    except ContractError:
        return ActivitySource(kind="heartbeat", name=key, status=UNREADABLE_STATE, run_id=_run_id(key))
    latest = min(status.updated_at, status.deadline_at + MARGIN)
    if found.modified is not None:
        latest = min(latest, found.modified + STALE)
    if status.state in FINAL_WORKER_STATES or now - latest > STALE:
        return None
    return ActivitySource(kind="heartbeat", name=key, status=status.state, run_id=status.run_id)
