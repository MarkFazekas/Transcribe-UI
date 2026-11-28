"""Manifests built from the database and the manifest step (plan m5 M5.9).

The `uploading` batch is seeded through the state store (the upload is M5.10), its audio in moto.
"""

from datetime import timedelta

import pytest
from sqlalchemy import update

from support.batches import FILM, FILM_PROBE, FILM_SECONDS, FIRST_BATCH_ID, PODCAST
from support.fakes import FIXED_NOW
from support.runs import (
    MANIFEST,
    READY,
    UPLOADING,
    RunsWorld,
    batch_state,
    read_back,
    seed_uploaded,
    set_item_phase,
)
from transcribe_contracts.classes import Manifest
from transcribe_contracts.layout import audio_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.batches.errors import WrongPhaseError
from transcribe_ui.state.models import JobRow
from transcribe_ui.upload.errors import NothingToTranscribeError, UploadIncompleteError
from transcribe_ui.upload.manifest import build_manifest


def _read_back(world: RunsWorld) -> Manifest:
    return read_manifest(MANIFEST, read_back(world))


def _built(world: RunsWorld) -> Manifest:
    with world.ports.state.session() as session:
        return build_manifest(session, FIRST_BATCH_ID)


def test_published_manifest_reads_back(runs_world: RunsWorld) -> None:
    """With no run active, the manifest is uploaded and the batch is `ready`; the worker's reader accepts it."""
    ids = seed_uploaded(runs_world)
    runs_world.step()
    assert batch_state(runs_world) == READY
    manifest = _read_back(runs_world)
    media = runs_world.leg.root / "media"
    film, podcast = manifest.items
    film_key = audio_key(FIRST_BATCH_ID, ids[FILM], "mka", 1)
    film_audio = [(audio.object_key, audio.track_index, audio.codec, audio.duration_s) for audio in film.audio]
    header = (manifest.batch_id, manifest.created_at, manifest.scan_root, film.item_id, film.original_path)
    assert header == (FIRST_BATCH_ID, FIXED_NOW, str(media), ids[FILM], str(media / "Movies" / FILM))
    assert film_audio == [(film_key, 1, FILM_PROBE.audio_tracks[1].codec, FILM_SECONDS)]
    assert [(job.language, job.mode, job.audio_object_key) for job in film.jobs] == [("en", "normal", film_key)]
    assert (podcast.item_id, podcast.media_kind, podcast.audio[0].track_index) == (ids[PODCAST], "audio", None)


def test_manifest_is_the_same_when_built_again(runs_world: RunsWorld) -> None:
    """The same database state gives the same manifest at any time, so a rewrite after a crash changes nothing."""
    seed_uploaded(runs_world)
    first = _built(runs_world)
    runs_world.cloud.clock.advance(timedelta(hours=1))
    runs_world.step()
    assert _read_back(runs_world) == first


def test_no_manifest_while_an_item_is_in_review(runs_world: RunsWorld) -> None:
    """An item back in review (or not uploaded yet) blocks the manifest; nothing is changed."""
    ids = seed_uploaded(runs_world)
    set_item_phase(runs_world, ids[FILM], "reviewing")
    with pytest.raises(UploadIncompleteError) as raised:
        runs_world.step()
    assert (raised.value.details, batch_state(runs_world)) == (
        {"batch_id": FIRST_BATCH_ID, "item_ids": [ids[FILM]]},
        UPLOADING,
    )


def test_no_manifest_for_an_extract_only_batch(runs_world: RunsWorld) -> None:
    """A batch without `transcribe` jobs takes the extract-only path: no manifest."""
    seed_uploaded(runs_world)
    with runs_world.ports.state.session() as session:
        session.execute(update(JobRow).values(kind="extract"))
    with pytest.raises(NothingToTranscribeError):
        _built(runs_world)


def test_step_on_a_ready_batch_is_refused(runs_world: RunsWorld) -> None:
    """Only an `uploading` batch has a manifest step."""
    seed_uploaded(runs_world)
    runs_world.step()
    with pytest.raises(WrongPhaseError):
        runs_world.step()
