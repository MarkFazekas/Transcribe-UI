"""Interrupting `run_upload` anywhere, then resuming it (plan m5 M5.10).

The upload is interrupted before and after each of its external calls (R2, ffmpeg), by a
simulated kill and by the cancel token; between those calls lie its persisted steps. Sending
the request again must reach the state of an uninterrupted upload: the same objects with the
same sizes (no duplicate, none missing), the same manifest bytes, the same rows, no temp file
and no open multipart upload.
"""

import itertools
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from functools import partial
from pathlib import Path

import pytest

from support.batch_backend import ids_of, run_ok
from support.batches import FILM, PODCAST, batch_request
from support.buckets import create_bucket
from support.credentials import moto_bucket
from support.interrupt import (
    Crash,
    EditDuringConversion,
    TripMedia,
    TripStore,
    Tripwire,
    patch_upload_store,
    trip_media,
)
from support.parity import RecordingProgress
from support.runs import RunsWorld
from support.storage import LARGE_SIZE, PART_BYTES, make_file, store_client
from support.trees import FIXED_MTIME_NS
from support.upload import (
    audio_keys,
    bucket_state,
    copied_world,
    extracted_count,
    open_uploads,
    outcome_of,
    phase_of,
    recorded_keys,
    review,
    source_path,
    temp_files,
    upload,
    upload_world,
)
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import CancelFlag, Ports
from transcribe_ui.storage.client import open_object_store
from transcribe_ui.storage.r2 import ObjectStore, R2ObjectStore
from transcribe_ui.upload.constants import UPLOAD_BYTES_STAGE

# The outcome of a finished upload.
READY = "ready"
# The upload of the reviewed batch makes more external calls than this (R2 and ffmpeg).
MIN_CALLS = 20
# An item's return to review reads the manifest, aborts uploads, lists and deletes its objects.
MIN_REREVIEW_CALLS = 4
# The film's audio objects (two tracks).
FILM_OBJECTS = 2
# How often `_until_ready` reviews again at most.
RESEND_LIMIT = 3


# Folder (under the test's temp folder) of the reviewed batch every trip starts from.
TEMPLATE = "template"


def _template(tmp_path: Path) -> Path:
    """Build the reviewed batch once (inside `moto_bucket()`); return its root, its state closed.

    Its installation id is created now, so every copy of the state uploads as the same installation.
    """
    create_bucket(TEMPLATE)
    with upload_world(tmp_path / TEMPLATE, TEMPLATE) as world:
        review(world)
        world.ports.installation.current()
    return tmp_path / TEMPLATE


@contextmanager
def _world(tmp_path: Path, name: str) -> Iterator[RunsWorld]:
    """A copy of the reviewed batch on its own bucket, with media tools that go through a tripwire."""
    create_bucket(name)
    with copied_world(tmp_path / TEMPLATE, tmp_path / name, name, wrap=trip_media) as world:
        yield world


def _final(world: RunsWorld, bucket: str) -> dict[str, object]:
    """Return what an upload leaves behind (every copy's items point at the template's tree)."""
    listed, manifest = bucket_state(world.store)
    return {
        "objects": listed,
        "manifest": manifest,
        "recorded": recorded_keys(world),
        "extracted": extracted_count(world),
        "phase": phase_of(world),
        "temp": temp_files(world),
        "open_uploads": open_uploads(bucket),
    }


def _arm(world: RunsWorld, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> None:
    media = world.ports.media
    assert isinstance(media, TripMedia)
    media.wire = wire
    patch_upload_store(monkeypatch, lambda store: TripStore(store, wire))


def _interrupted(world: RunsWorld, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> str:
    """Upload with the tripwire; return how the first attempt ended."""
    _arm(world, monkeypatch, wire)
    try:
        response = upload(world, cancel=wire.cancel)
    except Crash:
        return "crashed"
    return response.error.code if response.status == "error" else outcome_of(response)


def _resumed(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> str:
    _arm(world, monkeypatch, Tripwire())
    return outcome_of(upload(world))


@pytest.mark.parametrize(("cancel", "after"), [(False, False), (False, True), (True, False)])
def test_interrupt_after_every_step_then_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cancel: bool, after: bool
) -> None:
    """Killed (before or after a call) or cancelled at every call: re-sending reaches the uninterrupted state."""
    with moto_bucket():
        _template(tmp_path)
        with _world(tmp_path, "reference") as world:
            assert _resumed(world, monkeypatch) == READY
            reference = _final(world, "reference")
        endings: set[str] = set()
        for at in itertools.count(1):
            name = f"trip-{at}"
            with _world(tmp_path, name) as world:
                wire = Tripwire(at=at, after=after, cancel=CancelFlag() if cancel else None)
                ending = _interrupted(world, monkeypatch, wire)
                if not wire.fired:
                    break
                endings.add(ending)
                if ending != READY:
                    # A cancel after the last check comes too late: the upload finished.
                    assert _resumed(world, monkeypatch) == READY, at
                assert _final(world, name) == reference, at
    assert at > MIN_CALLS
    interrupted = "cancelled" if cancel else "crashed"
    assert endings - {READY} == {interrupted}


def _big_podcast(world: RunsWorld) -> None:
    """Make the podcast span three parts of the test part size (before the batch is created)."""
    podcast = source_path(world.leg.root, PODCAST)
    big = make_file(podcast.parent, LARGE_SIZE)
    big.replace(podcast)
    os.utime(podcast, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))


class _StopAfterFirstPart(RecordingProgress):
    """A progress sink that interrupts the upload once the first part of the podcast is sent."""

    def __init__(self, cancel: CancelFlag | None) -> None:
        """Cancel through this flag, or crash when it is None."""
        super().__init__()
        self.cancel = cancel

    def emit(self, event: ProgressEvent) -> None:
        """Interrupt on the first partial byte count."""
        if event.stage == UPLOAD_BYTES_STAGE and event.completed == PART_BYTES:
            if self.cancel is None:
                raise Crash
            self.cancel.cancel()


@pytest.mark.parametrize("cancel", [False, True])
def test_multipart_upload_interrupted_between_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cancel: bool
) -> None:
    """A multipart upload stopped after its first part: re-sending uploads the object whole, nothing stays open."""
    bucket = "multipart"
    with moto_bucket():
        create_bucket(bucket)
        with upload_world(tmp_path / "leg", bucket) as world:
            _big_podcast(world)
            review(world)
            small_parts = R2ObjectStore(store_client(), bucket, part_bytes=PART_BYTES)
            patch_upload_store(monkeypatch, lambda _store: small_parts)
            flag = CancelFlag() if cancel else None
            stopper = _StopAfterFirstPart(flag)
            if flag is None:
                with pytest.raises(Crash):
                    upload(world, progress=stopper)
            else:
                assert upload(world, cancel=flag, progress=stopper).status == "error"
            # A cancelled upload is aborted at once; a killed one stays open until the next start.
            left_open = open_uploads(bucket)
            assert outcome_of(upload(world)) == READY
            sizes = dict(bucket_state(world.store)[0])
            podcast_keys = [key for key in audio_keys(world.store) if key.endswith(".mp3")]
            assert (left_open, open_uploads(bucket)) == (int(not cancel), 0)
            assert [sizes[key] for key in podcast_keys] == [LARGE_SIZE]


def _rereview_store(monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> None:
    """Make `return_item_to_review` use an object store that goes through the wire."""
    monkeypatch.setattr("transcribe_ui.batches.rereview.object_store", partial(_tripped_store, wire))


def _tripped_store(wire: Tripwire, ports: Ports) -> ObjectStore:
    return TripStore(open_object_store(ports.credentials, ports.r2_clients), wire)


def _until_ready(world: RunsWorld) -> str:
    """Upload, reviewing again whatever went back to review; return the last outcome."""
    outcome = outcome_of(upload(world))
    for _ in range(RESEND_LIMIT):
        if outcome != "items_in_review":
            break
        run_ok(world.leg, batch_request("review_batch"))
        outcome = outcome_of(upload(world))
    return outcome


def _back_to_review_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wire: Tripwire) -> bool:
    """Edit the film mid-upload, interrupt its return to review at the wire, resume; return whether it fired."""
    name = f"back-{wire.at}-{int(wire.cancel is not None)}"
    create_bucket(name)
    with upload_world(tmp_path / name, name, wrap=trip_media) as world:
        review(world)
        media = world.ports.media
        assert isinstance(media, TripMedia)
        plain = media.inner
        media.inner = EditDuringConversion(plain, world.leg.root)
        _rereview_store(monkeypatch, wire)
        with suppress(Crash):
            upload(world, cancel=wire.cancel)
        media.inner = plain
        _rereview_store(monkeypatch, Tripwire())
        if not wire.fired:
            return False
        assert _until_ready(world) == READY, wire
        film = ids_of(world.leg)[FILM]
        film_objects = [key for key in audio_keys(world.store) if film in key]
        assert (len(film_objects), audio_keys(world.store)) == (FILM_OBJECTS, recorded_keys(world)), wire
    return True


@pytest.mark.parametrize("cancel", [False, True])
def test_return_to_review_interrupted_then_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, cancel: bool
) -> None:
    """A kill after, or a cancel at, any call of an item's return to review: resuming leaves no stray object."""
    fired = 0
    with moto_bucket():
        for at in itertools.count(1):
            wire = Tripwire(at=at, after=not cancel, cancel=CancelFlag() if cancel else None)
            if not _back_to_review_interrupted(tmp_path, monkeypatch, wire):
                break
            fired += 1
    assert fired >= MIN_REREVIEW_CALLS
