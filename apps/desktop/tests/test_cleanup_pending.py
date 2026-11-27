"""An interrupted cloud cleanup blocks every launch until it is repeated; its deletes never hold the runs lock.

`cleanup_cloud` records `cleanup_pending` before its first delete and clears it after its last,
so a manifest or audio left in R2 by a kill or an R2 failure is never transcribed meanwhile
(`plan_launch` and `launch_worker` refuse with `cleanup_pending`). The runs lock is given up
once the cleanup is recorded, so a slow delete never blocks `refresh_status`.
"""

import contextlib
import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import pytest

from support.batch_backend import error_of, run_ok
from support.batches import FIRST_BATCH_ID, batch_request
from support.cleanup import EVERYTHING, cleanup_pending, confirmed, keys_under, patch_store, seed_world, send
from support.credentials import moto_bucket
from support.interrupt import Crash, StoreWrap, TripStore, Tripwire
from support.launch import LAUNCH, PLAN, document
from support.monitor import REFRESH
from support.runs import RunsWorld, seed_uploaded
from support.upload import upload_world
from transcribe_ui.storage.r2 import ObjectStore

# How long the slow delete takes: longer than the test backends' runs lock wait (0.5 s).
SLOW_DELETE_S = 1.0
# A fingerprint the launch never gets to compare.
ANY_FINGERPRINT = hashlib.sha256(b"any plan").hexdigest()
# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10


@dataclass
class _CrashingDeletes(TripStore):
    """The bucket, but every delete is a kill of the app."""

    def delete(self, key: str) -> None:
        """Crash instead of deleting the key."""
        raise Crash(key)


@dataclass
class _SlowDeletes(TripStore):
    """The bucket, but the first delete waits a while (and says when it starts)."""

    started: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def wrapping(cls, started: threading.Event) -> "StoreWrap":
        """Return a store wrap whose stores share `started`."""
        return partial(cls._wrapped, started)

    def delete(self, key: str) -> None:
        """Wait before the first delete, then delete."""
        if not self.started.is_set():
            self.started.set()
            threading.Event().wait(SLOW_DELETE_S)
        self.inner.delete(key)

    @classmethod
    def _wrapped(cls, started: threading.Event, store: ObjectStore) -> ObjectStore:
        return cls(store, Tripwire(), started)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto and the fake Runpod API (fixed clock) with the transcribed source batch."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def test_interrupted_cleanup_blocks_launches(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Killed at its first delete: the batch shows the marker and no plan or launch starts; repeating ends it."""
    patch_store(monkeypatch, "known", lambda store: _CrashingDeletes(store, Tripwire()))
    with contextlib.suppress(Crash):
        send(world, confirmed(EVERYTHING))
    patch_store(monkeypatch, "known", lambda store: store)
    summary = run_ok(world.leg, batch_request("get_batch"))["batch"]["summary"]
    assert (summary["phase"], summary["cleanup_pending"]) == ("cleaned", EVERYTHING)
    refusal = ("cleanup_pending", {"batch_ids": [FIRST_BATCH_ID]})
    for request in (document(PLAN), document(LAUNCH, confirm_fingerprint=ANY_FINGERPRINT)):
        assert error_of(send(world, request)) == refusal
    assert send(world, confirmed(EVERYTHING)).status == "ok"
    assert (cleanup_pending(world), keys_under(world.store)) == (None, [])
    assert send(world, document(PLAN)).status == "ok"


def test_refresh_runs_while_a_cleanup_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deletes run without the runs lock: `refresh_status` answers meanwhile, not `runs_busy`."""
    with moto_bucket(), upload_world(tmp_path / "leg") as world:
        seed_uploaded(world)
        started = threading.Event()
        patch_store(monkeypatch, "known", _SlowDeletes.wrapping(started))
        cleanup = threading.Thread(target=send, args=(world, confirmed(EVERYTHING)))
        cleanup.start()
        assert started.wait(THREAD_TIMEOUT_S)
        refreshed = send(world, document(REFRESH))
        cleanup.join(THREAD_TIMEOUT_S)
        assert refreshed.status == "ok", refreshed
        assert (cleanup_pending(world), keys_under(world.store)) == (None, [])
