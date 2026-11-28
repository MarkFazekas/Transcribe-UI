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

from support.batches import FIRST_BATCH_ID
from support.buckets import create_bucket
from support.cleanup import EVERYTHING, cleanup_document, keys_under, patch_store
from support.cleanup import send as send_document
from support.credentials import TEST_BUCKET, moto_bucket, store_request
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import FOREIGN_BATCH_ID, foreign_manifest, ok_as, send, write_foreign
from support.runs import JSON_TYPE, RunsWorld
from support.upload import upload_world
from support.withdrawal import exclude_film, fail_deleting, seed_leftover
from transcribe_contracts.constants import AUDIO_FOLDER
from transcribe_contracts.layout import audio_key, deleting_key, manifest_key
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.batches.cloud_responses import ListCloudBatchesResponse
from transcribe_ui.cleanup.pending import record_pending
from transcribe_ui.cleanup.stages import Purge, delete_unknown
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.state.models import PendingCleanupRow
from transcribe_ui.storage.r2 import R2ObjectStore

# Another bucket of the same account.
OTHER_BUCKET = "another-bucket"
# R2 calls of the confirmed cleanup of the seeded job (manifest and audio): the manifest read, the
# leases, the marker, the uploads, the manifest, `_done.json`, the listings, the audio, the marker.
R2_CALLS = 11
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
    assert fired >= R2_CALLS


def test_a_cancelled_cleanup_is_dropped_by_the_refresh(world: RunsWorld) -> None:
    """Cancelled before the point of no return: the refresh drops the record, the job stays listed."""
    flag = CancelFlag()
    purge = Purge(_CancelAfterMarker(world.store, flag), world.context(flag), "cleanup_cloud")
    record_pending(world.context(), FOREIGN_BATCH_ID)
    with pytest.raises(CancelledError):
        delete_unknown(purge, FOREIGN_BATCH_ID)
    refreshed = ok_as(send(world, "refresh_status", recheck=True), RefreshStatusResponse)
    listed = ok_as(send(world, "list_cloud_batches"), ListCloudBatchesResponse)
    shown = (refreshed.finished_cleanups, _pending(world), [batch.batch_id for batch in listed.batches])
    assert shown == ((), [], [FOREIGN_BATCH_ID])


def test_a_withdrawal_drops_the_marker_first(
    runs_world: RunsWorld, r2_only_world: RunsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another computer's stalled cleanup (marker, record) cannot purge a job its uploader only withdraws.

    The uploader's withdrawal stops at its first delete (the marker): the manifest stays, so the
    other computer's refresh only drops its marker and record; the uploader's audio is kept.
    """
    ids = seed_leftover(runs_world)
    runs_world.store.write(deleting_key(FIRST_BATCH_ID), b"{}", JSON_TYPE)
    record_pending(r2_only_world.context(), FIRST_BATCH_ID)
    fail_deleting(monkeypatch, deleting_key(FIRST_BATCH_ID))
    assert exclude_film(runs_world, ids).status == "ok", "the withdrawal stays pending"
    refreshed = ok_as(send(r2_only_world, "refresh_status", recheck=True), RefreshStatusResponse)
    audio = [key for key in keys_under(runs_world.store, FIRST_BATCH_ID) if f"/{AUDIO_FOLDER}/" in key]
    shown = (refreshed.finished_cleanups, _pending(r2_only_world), bool(audio))
    assert (shown, runs_world.store.head(manifest_key(FIRST_BATCH_ID)) is not None) == (((), [], True), True)


def _use_bucket(world: RunsWorld, bucket: str) -> None:
    assert world.leg.backend.execute_json(store_request(r2_bucket=bucket)).status == "ok"


def test_a_record_waits_for_its_bucket(world: RunsWorld) -> None:
    """A record of another bucket is kept, not dropped, until that bucket is configured again; then it is finished."""
    record_pending(world.context(), FOREIGN_BATCH_ID)
    world.store.write(deleting_key(FOREIGN_BATCH_ID), b"{}", JSON_TYPE)
    world.store.delete(manifest_key(FOREIGN_BATCH_ID))
    create_bucket(OTHER_BUCKET)
    _use_bucket(world, OTHER_BUCKET)
    elsewhere = ok_as(send(world, "refresh_status", recheck=True), RefreshStatusResponse)
    kept = (elsewhere.finished_cleanups, _pending(world))
    _use_bucket(world, TEST_BUCKET)
    back = ok_as(send(world, "refresh_status", recheck=True), RefreshStatusResponse)
    shown = (kept, back.finished_cleanups, _pending(world), keys_under(world.store, FOREIGN_BATCH_ID))
    assert shown == (((), [FOREIGN_BATCH_ID]), (FOREIGN_BATCH_ID,), [], [])
