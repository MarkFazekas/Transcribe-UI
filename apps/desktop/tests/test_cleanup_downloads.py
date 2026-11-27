"""Cloud cleanup and downloads (plans m5 M5.13 and M5.14).

The second confirmation of `everything` follows the `downloaded` rule of the placements: a job
that did not fail and has no placement outcome may not be downloaded yet. A download after an
interrupted `audio` cleanup still places the results (they stay in R2); after `everything` only
extracted subtitles could be placed, so the source's transcribed results are not.
"""

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID, media_dir
from support.cleanup import (
    AUDIO,
    EVERYTHING,
    cleanup_document,
    cleanup_pending,
    confirmed,
    flags,
    patch_store,
    seed_world,
    send,
)
from support.credentials import moto_bucket
from support.interrupt import Crash, TripStore, Tripwire
from support.launch import ok_as
from support.monitor import SUBTITLE
from support.runs import RunsWorld
from support.upload import upload_world
from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.placement.classes import RunDownloadResponse

# Where the podcast's English subtitle goes with `next_to_media`.
PODCAST_SUBTITLE = "Audio/podcast.en.srt"
# The download request of the source batch (`next_to_media`).
DOWNLOAD = json.dumps({"action": "run_download", "batch_id": FIRST_BATCH_ID})


class _CrashingDeletes(TripStore):
    """The bucket, but every delete is a kill of the app."""

    def delete(self, key: str) -> None:
        """Crash instead of deleting the key."""
        raise Crash(key)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """The transcribed source batch (the film failed, the podcast ok) over moto and the fake Runpod API."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        seed_world(created)
        yield created


def _downloaded(world: RunsWorld) -> RunDownloadResponse:
    return ok_as(send(world, DOWNLOAD), RunDownloadResponse)


def _fields(world: RunsWorld) -> list[str]:
    asked = ok_as(send(world, cleanup_document(EVERYTHING)), NeedsConfirmation)
    return [confirmation.field for confirmation in asked.confirmations]


def test_downloaded_batch_needs_one_confirmation(world: RunsWorld) -> None:
    """Every subtitle placed (the failed job needs none): `everything` asks only for `confirm`."""
    assert _fields(world) == ["confirm", "confirm_not_downloaded"]
    assert _downloaded(world).batch.phase == "downloaded"
    assert _fields(world) == ["confirm"]


def test_download_after_an_interrupted_audio_cleanup(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """An `audio` cleanup killed at its first delete keeps the results: the download places them."""
    patch_store(monkeypatch, "known", lambda store: _CrashingDeletes(store, Tripwire()))
    with contextlib.suppress(Crash):
        send(world, confirmed(AUDIO))
    assert (cleanup_pending(world), flags(world)) == (AUDIO, ("transcribed", True))
    downloaded = _downloaded(world)
    assert (downloaded.download.written, downloaded.batch.cleanup_pending) == (1, AUDIO)
    assert (media_dir(world.leg.root) / PODCAST_SUBTITLE).read_bytes() == SUBTITLE
    assert downloaded.batch.phase == "downloaded"


def test_cleaned_batch_places_no_transcribed_subtitle(world: RunsWorld) -> None:
    """After `everything` the results are gone: a download of the `cleaned` batch writes nothing."""
    assert send(world, confirmed(EVERYTHING)).status == "ok"
    downloaded = _downloaded(world)
    assert (downloaded.batch.phase, downloaded.download.written) == ("cleaned", 0)
    assert not (media_dir(world.leg.root) / PODCAST_SUBTITLE).exists()
