"""The marker of a cleanup of another computer's job, and its local record (architecture §3.3 "Unknown batch").

A cancel after the marker is written but before the manifest is deleted removes the marker again,
so the job stays as it was. An interruption at any R2 call leaves a local record that the
refresh with `recheck` settles: past the manifest delete it finishes the deletion without a
question, before it the job stays intact and visible.
"""

import contextlib
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from support.buckets import create_bucket
from support.cleanup import EVERYTHING, cleanup_document, keys_under, patch_store
from support.cleanup import send as send_document
from support.credentials import moto_bucket
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import FOREIGN_BATCH_ID, foreign_manifest, ok_as, send, write_foreign
from support.runs import JSON_TYPE, RunsWorld
from support.upload import upload_world
from transcribe_contracts.layout import audio_key, deleting_key, manifest_key
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.batches.cloud_responses import ListCloudBatchesResponse
from transcribe_ui.cleanup.pending import record_pending
from transcribe_ui.cleanup.stages import Purge, delete_unknown
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.storage.r2 import R2ObjectStore

# The objects of the other computer's job: its manifest and audio.
SEEDED_KEYS = 2
# The audio object of the other computer's job.
AUDIO = audio_key(FOREIGN_BATCH_ID, foreign_manifest().items[0].item_id, "flac", None)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto with another computer's published job (manifest and audio)."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        _seed(created)
        yield created


class _CancelAfterMarker(R2ObjectStore):
    """The world's store; writing the marker sets the request's cancel flag (a cancel right after the PUT)."""

    def __init__(self, store: R2ObjectStore, flag: CancelFlag) -> None:
        """Delegate to `store`'s client and bucket."""
        super().__init__(store._client, store._bucket)  # noqa: SLF001 -- the test bucket behind the world's store
        self._flag = flag

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Write, then cancel once the marker is in R2."""
        super().write(key, body, content_type)
        if key == deleting_key(FOREIGN_BATCH_ID):
            self._flag.cancel()


def test_a_cancel_after_the_marker_keeps_the_job(world: RunsWorld) -> None:
    """Cancelled before the manifest delete: the marker is removed again, the manifest and audio stay."""
    flag = CancelFlag()
    purge = Purge(_CancelAfterMarker(world.store, flag), world.context(flag), "cleanup_cloud")
    with pytest.raises(CancelledError):
        delete_unknown(purge, FOREIGN_BATCH_ID)
    left = keys_under(world.store, FOREIGN_BATCH_ID)
    assert (left, purge.deleted) == ([manifest_key(FOREIGN_BATCH_ID), AUDIO], 0)


def _seed(world: RunsWorld) -> list[str]:
    write_foreign(world.store)
    world.store.write(AUDIO, b"{}", JSON_TYPE)
    return keys_under(world.store, FOREIGN_BATCH_ID)


def _pending(world: RunsWorld) -> list[str]:
    with world.ports.state.session() as session:
        return list(session.scalars(select(PendingCleanupRow.batch_id)))


def _killed_then_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> bool:
    """Kill the cleanup at the wire, refresh with `recheck`; check the job is gone or intact; return whether it fired."""
    bucket = f"pending-{wire.at}-{int(wire.after)}"
    create_bucket(bucket)
    with upload_world(tmp_path / bucket, bucket) as world:
        seeded = _seed(world)
        patch_store(monkeypatch, "unknown", lambda store: TripStore(store, wire))
        with contextlib.suppress(Crash):
            send_document(world, cleanup_document(EVERYTHING, FOREIGN_BATCH_ID, confirm=True))
        patch_store(monkeypatch, "unknown", lambda store: store)
        if not wire.fired:
            return False
        before = set(keys_under(world.store, FOREIGN_BATCH_ID))
        # Past the point of no return: the marker is there and the manifest is gone.
        passed = deleting_key(FOREIGN_BATCH_ID) in before and manifest_key(FOREIGN_BATCH_ID) not in before
        refreshed = ok_as(send(world, "refresh_status", recheck=True), RefreshStatusResponse)
        left = keys_under(world.store, FOREIGN_BATCH_ID)
        assert left in ([], seeded), (wire, left)
        expected = (FOREIGN_BATCH_ID,) if passed else ()
        assert (refreshed.finished_cleanups, _pending(world)) == (expected, []), wire
    return True


@pytest.mark.parametrize("after", [False, True])
def test_a_refresh_settles_an_interrupted_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, after: bool
) -> None:
    """A kill before or after any R2 call: the refresh finishes the deletion, or the job stays as it was."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            if not _killed_then_refreshed(tmp_path, monkeypatch, Tripwire(at=at, after=after)):
                break
            fired += 1
    assert fired >= SEEDED_KEYS


def test_a_cancelled_cleanup_is_dropped_by_the_refresh(world: RunsWorld) -> None:
    """Cancelled before the point of no return: the refresh drops the record, the job stays listed."""
    flag = CancelFlag()
    purge = Purge(_CancelAfterMarker(world.store, flag), world.context(flag), "cleanup_cloud")
    record_pending(world.ports.state, FOREIGN_BATCH_ID, world.now())
    with pytest.raises(CancelledError):
        delete_unknown(purge, FOREIGN_BATCH_ID)
    refreshed = ok_as(send(world, "refresh_status", recheck=True), RefreshStatusResponse)
    listed = ok_as(send(world, "list_cloud_batches"), ListCloudBatchesResponse)
    shown = (refreshed.finished_cleanups, _pending(world), [batch.batch_id for batch in listed.batches])
    assert shown == ((), [], [FOREIGN_BATCH_ID])
