"""Run activity from worker heartbeats: `runs/<run_id>/status.json` in R2 (plan m5 §1).

A heartbeat counts when its worker state is not final and its `updated_at` lies within
`HEARTBEAT_STALE_S` of the clock (or ahead of it), or when it cannot be read (fail closed).
"""

from datetime import datetime, timedelta

from transcribe_contracts.constants import RUNS_PREFIX
from transcribe_contracts.errors import ContractError
from transcribe_contracts.reading import read_run_status
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.runs.constants import FINAL_WORKER_STATES, HEARTBEAT_STALE_S, STATUS_KEY_SUFFIX, UNREADABLE_STATE
from transcribe_ui.storage.r2 import ObjectStore


def heartbeat_sources(store: ObjectStore, now: datetime) -> list[ActivitySource]:
    """Return the heartbeats of live (or unreadable) worker runs, by key.

    Raises:
        StorageError: R2 could not be listed or read.
    """
    listed = store.list_objects(RUNS_PREFIX)
    keys = [found.key for found in listed if found.key.endswith(STATUS_KEY_SUFFIX)]
    sources = [_heartbeat(store, key, now) for key in keys]
    return [source for source in sources if source is not None]


def _heartbeat(store: ObjectStore, key: str, now: datetime) -> ActivitySource | None:
    body = store.read(key)
    if body is None:
        return None  # deleted since the listing
    try:
        status = read_run_status(key, body)
    except ContractError:
        return ActivitySource(kind="heartbeat", name=key, status=UNREADABLE_STATE)
    age = now - status.updated_at
    if status.state in FINAL_WORKER_STATES or age > timedelta(seconds=HEARTBEAT_STALE_S):
        return None
    return ActivitySource(kind="heartbeat", name=key, status=status.state)
