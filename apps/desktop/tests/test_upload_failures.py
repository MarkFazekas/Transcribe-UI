"""Files that cannot be read, objects that do not verify, media failures and a taken prefix (plan m5 M5.10).

A file that cannot be read but did not change (a network share that is briefly unreachable)
fails the request with a retryable error and keeps its item; only a file that really changed or
is gone goes back to review.
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import override

import pytest

from support.batch_backend import error_of, item_phase
from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.buckets import shared_client
from support.credentials import TEST_BUCKET, moto_bucket
from support.interrupt import TripMedia, patch_upload_store, trip_media
from support.runs import RunsWorld
from support.upload import (
    change_source,
    outcome_of,
    phase_of,
    recorded_keys,
    review,
    source_path,
    upload,
    upload_world,
)
from transcribe_contracts.layout import audio_key
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import ConvertedAudio, ProbeResult
from transcribe_ui.media.constants import ERROR_MEDIA_OUTPUT_UNWRITABLE, ERROR_MEDIA_TOOL_FAILED
from transcribe_ui.media.ports import MediaTools
from transcribe_ui.storage.multipart import UploadProgress
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# What the failing calls say.
HOST_DOWN = "Host is down"
IO_ERROR = "Input/output error"
CONVERT_FAILED = "Converting failed."
# The video converted first: one untagged track.
EPISODE = "Episode.mkv"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend on the upload tree over moto and the fake Runpod API (fixed clock)."""
    with moto_bucket(), upload_world(tmp_path / "leg", wrap=trip_media) as created:
        yield created


def _failure(world: RunsWorld) -> tuple[str, dict[str, object]]:
    """Upload; return the error code and details."""
    return error_of(upload(world))


def _done(world: RunsWorld) -> str:
    """Upload; return the outcome."""
    return outcome_of(upload(world))


class _FlakyStat:
    """`Path.stat`, but one file's share is down."""

    def __init__(self, down: Path, real: Callable[..., os.stat_result]) -> None:
        """Fail for `down`, pass every other path to `real`."""
        self.down = down
        self.real = real

    def __call__(self, path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        """Stat the path, or fail like an unreachable share."""
        if path == self.down:
            raise OSError(HOST_DOWN)
        return self.real(path, follow_symlinks=follow_symlinks)

    def __get__(self, path: Path | None, owner: type | None = None) -> Callable[..., os.stat_result]:
        """Bind to the path, as a method would be."""
        return self if path is None else partial(self, path)


class _UnreadableUpload(R2ObjectStore):
    """The test bucket, but reading the local file of keys with this ending fails; may delete the source first."""

    def __init__(self, ending: str, gone: Path | None = None) -> None:
        """Fail the uploads of keys ending in `ending`, deleting `gone` first when given."""
        super().__init__(shared_client(), TEST_BUCKET)
        self.ending = ending
        self.gone = gone

    @override
    def upload_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        progress: UploadProgress,
        cancel: CancelToken,
    ) -> int:
        if not key.endswith(self.ending):
            return super().upload_file(key, source, content_type, progress, cancel)
        if self.gone is not None:
            self.gone.unlink()
        raise OSError(IO_ERROR)


def test_unreachable_source_fails_and_keeps_the_item(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The podcast cannot be stat-ed (the share is down): `source_unreadable`, it stays resolved; later it uploads."""
    ids = review(world)
    monkeypatch.setattr(Path, "stat", _FlakyStat(source_path(world.leg.root, PODCAST), Path.stat))
    code, details = _failure(world)
    monkeypatch.undo()
    assert (code, details, item_phase(world.leg, ids[PODCAST])) == (
        "source_unreadable",
        {"item_id": ids[PODCAST]},
        "reviewed",
    )
    assert _done(world) == "ready"


def test_read_failure_unchanged_source_kept(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading the podcast fails while it is unchanged: `source_unreadable`, nothing recorded for it; re-sending uploads."""
    ids = review(world)
    patch_upload_store(monkeypatch, lambda _store: _UnreadableUpload(".mp3"))
    code, details = _failure(world)
    podcast_rows = [key for key in recorded_keys(world) if ids[PODCAST] in key]
    assert (code, details, podcast_rows, item_phase(world.leg, ids[PODCAST])) == (
        "source_unreadable",
        {"item_id": ids[PODCAST]},
        [],
        "reviewed",
    )
    patch_upload_store(monkeypatch, lambda store: store)
    assert _done(world) == "ready"


def test_read_failure_vanished_source_back_in_review(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading fails because the podcast was deleted: the item goes back to review, the others go on."""
    ids = review(world)
    podcast = source_path(world.leg.root, PODCAST)
    patch_upload_store(monkeypatch, lambda _store: _UnreadableUpload(".mp3", gone=podcast))
    response = upload(world)
    in_review = response.model_dump(mode="json")["items_in_review"]
    assert (outcome_of(response), in_review, item_phase(world.leg, ids[PODCAST])) == (
        "items_in_review",
        [ids[PODCAST]],
        "reviewing",
    )


def test_unreadable_temp_file_is_retryable(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The converted temp file cannot be read back: `media_output_unwritable` with the item, the item stays."""
    ids = review(world)
    patch_upload_store(monkeypatch, lambda _store: _UnreadableUpload(".a0.mka"))
    code, details = _failure(world)
    assert (code, details["item_id"], item_phase(world.leg, ids[EPISODE])) == (
        ERROR_MEDIA_OUTPUT_UNWRITABLE,
        ids[EPISODE],
        "reviewed",
    )


class _FilmChangesWhileVerified(R2ObjectStore):
    """The test bucket, but the film's English object reads one byte long and the film is edited meanwhile."""

    def __init__(self, root: Path) -> None:
        """Edit the film under this world root."""
        super().__init__(shared_client(), TEST_BUCKET)
        self.root = root

    @override
    def head(self, key: str) -> ObjectInfo | None:
        found = super().head(key)
        if found is None or not key.endswith(".a1.mka"):
            return found
        change_source(self.root, FILM)
        return ObjectInfo(found.key, found.size + 1)


def test_unverified_changed_source_back_in_review(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """An object that does not verify is first checked against the source: the edited film goes back to review."""
    ids = review(world)
    patch_upload_store(monkeypatch, lambda _store: _FilmChangesWhileVerified(world.leg.root))
    response = upload(world)
    assert (outcome_of(response), response.model_dump(mode="json")["items_in_review"]) == (
        "items_in_review",
        [ids[FILM]],
    )
    assert all(ids[FILM] not in key for key in recorded_keys(world))


@dataclass(frozen=True)
class _FailingConversion:
    """Media tools whose conversions fail like a broken ffmpeg (after writing, which the next start cleans)."""

    inner: MediaTools

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Pass on."""
        return self.inner.probe(source, cancel)

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Pass on."""
        return self.inner.extract(source, stream_index, cancel)

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Convert, then fail."""
        self.inner.convert(source, track_index, output_base, cancel)
        raise DomainError(ERROR_MEDIA_TOOL_FAILED, CONVERT_FAILED, {"tool": "ffmpeg", "operation": "convert"})


def test_media_errors_name_the_item(world: RunsWorld) -> None:
    """A failed conversion fails the request; its details name the item."""
    ids = review(world)
    media = world.ports.media
    assert isinstance(media, TripMedia)
    media.inner = _FailingConversion(media.inner)
    code, details = _failure(world)
    assert (code, details) == (
        ERROR_MEDIA_TOOL_FAILED,
        {"tool": "ffmpeg", "operation": "convert", "item_id": ids[EPISODE]},
    )


def test_open_multipart_upload_under_the_id_takes_it(world: RunsWorld) -> None:
    """Another computer's upload in progress under the batch's id: `batch_id_taken`, the batch stays in review."""
    review(world)
    key = audio_key(FIRST_BATCH_ID, "01JDZZZZZZZZZZZZZZZZZZZZ09", "mka", 0)
    shared_client().create_multipart_upload(Bucket=TEST_BUCKET, Key=key)
    assert (_failure(world)[0], phase_of(world)) == ("batch_id_taken", "reviewing")
