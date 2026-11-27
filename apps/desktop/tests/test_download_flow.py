"""`plan_download` and `run_download` (plan m5 M5.13): strategies, phases and the `downloaded` rule."""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from support.batches import FIRST_BATCH_ID, media_dir
from support.download import (
    DOWNLOADED,
    EXTRACTED_SRT,
    FILM_ID,
    PENDING_JOBS,
    PLAN_DOWNLOAD,
    PODCAST_ID,
    RUN_DOWNLOAD,
    SILENT_ID,
    TRANSCRIBED_JOBS,
    error_document,
    expected_outcomes,
    ok_document,
    outcomes,
    phase_of,
    placement,
    request,
    seed_download_batch,
    send,
    write_subtitles,
)
from support.monitor import SUBTITLE, refresh, write_done, write_result
from support.parity import Leg
from support.runs import RunsWorld
from transcribe_contracts.layout import manifest_key

# Outcome of a job whose subtitle was written.
PLACE = "place"
# A run id the simulated worker results name.
RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR01"
# The extracted subtitle as written.
EXTRACTED = EXTRACTED_SRT.encode()
# What every strategy places for the seeded transcribed batch (below the leg's root), with
# empty results included: the film, the podcast (empty) and the silent film's extracted subtitle.
PLACED: Mapping[str, Mapping[str, bytes]] = MappingProxyType(
    {
        "mirror": {
            "out/Movies/Film.en.srt": SUBTITLE,
            "out/Audio/podcast.en.srt": b"",
            "out/Movies/Silent.hu.srt": EXTRACTED,
        },
        "flat": {"out/Film.en.srt": SUBTITLE, "out/podcast.en.srt": b"", "out/Silent.hu.srt": EXTRACTED},
        "flat_with_folders": {
            "out/Movies_Film.en.srt": SUBTITLE,
            "out/Audio_podcast.en.srt": b"",
            "out/Movies_Silent.hu.srt": EXTRACTED,
        },
    },
)


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, fake Runpod, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


def _download(leg: Leg, strategy: str = "next_to_media", **fields: object) -> dict[str, str]:
    """Run a download; return the outcome per item id."""
    options = placement(strategy, **fields)
    return outcomes(ok_document(send(leg, request(RUN_DOWNLOAD, placement=options))))


def _files_below(root: Path, folder: str) -> dict[str, bytes]:
    found = (root / folder).glob("**/*")
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in found if path.is_file()}


@pytest.mark.parametrize("strategy", sorted(PLACED))
def test_strategy_places_every_subtitle(world: RunsWorld, strategy: str) -> None:
    """Mirror and both flat strategies place the subtitles with their names; the batch is then downloaded."""
    leg = world.leg
    assert _download(leg, strategy, include_empty=True) == expected_outcomes(podcast=PLACE)
    assert (_files_below(leg.root, "out"), phase_of(leg)) == (PLACED[strategy], DOWNLOADED)


def test_next_to_media_skips_the_existing_sidecar(world: RunsWorld) -> None:
    """Next to the media the film's existing `Film.en.srt` is skipped; skips do not block `downloaded`."""
    leg = world.leg
    movies = media_dir(leg.root) / "Movies"
    assert _download(leg) == expected_outcomes(film="skip_existing")
    assert ((movies / "Film.en.srt").read_bytes(), phase_of(leg)) == (b"", DOWNLOADED)
    assert (movies / "Silent.hu.srt").read_bytes() == EXTRACTED


def test_download_again_with_another_strategy(world: RunsWorld) -> None:
    """A downloaded batch can be downloaded again with another strategy (Q25); the phase stays."""
    leg = world.leg
    _download(leg)
    assert (_download(leg, "flat"), phase_of(leg)) == (expected_outcomes(), DOWNLOADED)
    assert sorted(_files_below(leg.root, "out")) == ["out/Film.en.srt", "out/Silent.hu.srt"]


def test_plan_writes_nothing(world: RunsWorld) -> None:
    """`plan_download` lists the plan and writes nothing; the batch stays `transcribed`."""
    leg = world.leg
    planned = ok_document(send(leg, request(PLAN_DOWNLOAD, placement=placement("mirror"))))
    found = {entry["item_id"]: entry["outcome"] for entry in planned["plan"]["entries"]}
    assert (found, planned["plan"]["errors"]) == (expected_outcomes(), [])
    assert ((leg.root / "out").exists(), phase_of(leg)) == (False, "transcribed")


@pytest.fixture
def ready_world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world with a `ready` batch whose two jobs have no result yet."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "ready", PENDING_JOBS)
    runs_world.store.write(manifest_key(FIRST_BATCH_ID), b"{}", "application/json")
    return runs_world


def test_partial_download_then_the_rest(ready_world: RunsWorld) -> None:
    """On `ready` only finished jobs are placed (requirements "Subtitle download options"); after `_done.json` a second download reaches `downloaded`."""
    world = ready_world
    leg = world.leg
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, RUN_ID, world.now())
    refresh(world)
    first = _download(leg, "mirror")
    assert (first, phase_of(world.leg)) == ({FILM_ID: PLACE, PODCAST_ID: "not_finished"}, "ready")
    write_result(world.store, FIRST_BATCH_ID, PODCAST_ID, RUN_ID, world.now())
    write_done(world.store, FIRST_BATCH_ID, RUN_ID, world.now())
    assert [each.phase for each in refresh(world).batches] == ["transcribed"]
    rest = _download(leg, "mirror")
    assert (rest, phase_of(world.leg)) == ({FILM_ID: "skip_existing", PODCAST_ID: PLACE}, DOWNLOADED)


def test_full_download_on_ready_moves_on_with_done(ready_world: RunsWorld) -> None:
    """A `ready` batch downloaded in full moves to `downloaded` as soon as `refresh_status` sees `_done.json`."""
    world = ready_world
    leg = world.leg
    for item_id in (FILM_ID, PODCAST_ID):
        write_result(world.store, FIRST_BATCH_ID, item_id, RUN_ID, world.now())
    refresh(world)
    assert (_download(leg, "flat"), phase_of(leg)) == ({FILM_ID: PLACE, PODCAST_ID: PLACE}, "ready")
    write_done(world.store, FIRST_BATCH_ID, RUN_ID, world.now())
    refreshed = [each.phase for each in refresh(world).batches]
    assert (refreshed, phase_of(leg)) == ([DOWNLOADED], DOWNLOADED)


def test_extracted_subtitles_from_a_cleaned_batch(batch_leg: Leg) -> None:
    """A `cleaned` batch places only its extracted subtitles (kept in SQLite), without any credential."""
    seed_download_batch(batch_leg.ports.state, batch_leg.root, "cleaned", TRANSCRIBED_JOBS)
    assert _download(batch_leg, "flat") == {SILENT_ID: PLACE}
    assert _files_below(batch_leg.root, "out") == {"out/Silent.hu.srt": EXTRACTED}
    assert phase_of(batch_leg) == "cleaned"


def test_wrong_phase_is_refused(batch_leg: Leg) -> None:
    """A batch still uploading has nothing to download."""
    seed_download_batch(batch_leg.ports.state, batch_leg.root, "uploading", PENDING_JOBS)
    body = error_document(send(batch_leg, request(RUN_DOWNLOAD)))
    assert (body["code"], body["details"]["phase"]) == ("wrong_phase", "uploading")
