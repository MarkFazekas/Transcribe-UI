"""The `downloaded` rule and the safety of the writes (plan m5 M5.13 review)."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID
from support.download import (
    EPISODE_ID,
    FAILURE,
    FILM_ID,
    PENDING_JOBS,
    PODCAST_ID,
    RUN_DOWNLOAD,
    ok_document,
    outcomes,
    phase_of,
    placement,
    recorded,
    request,
    seed_download_batch,
    send,
    write_subtitles,
)
from support.interrupt import Crash
from support.monitor import SUBTITLE, refresh, write_done, write_result
from support.parity import Leg
from support.runs import RunsWorld
from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.placement import download, writer
from transcribe_ui.runs.responses import RefreshStatusResponse

# A run id the simulated worker results name.
RUN_ID = "01JDZZZZZZZZZZZZZZZZZZZR01"
# The random part the tests give every temp file name.
TEMP_NAME_PART = "0a1b2c"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


@pytest.fixture
def ready_world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world with a `ready` batch whose two jobs have no result yet."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "ready", PENDING_JOBS)
    runs_world.store.write(manifest_key(FIRST_BATCH_ID), b"{}", "application/json")
    return runs_world


def _fixed_token() -> str:
    return TEMP_NAME_PART


def _crash(*_args: object) -> None:
    raise Crash


def test_planted_link_is_not_written_through(
    world: RunsWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link planted where the temp file goes makes that job fail; the file it points to is untouched."""
    leg = world.leg
    victim = leg.root / "victim.txt"
    victim.write_bytes(b"keep me")
    out = leg.root / "out"
    out.mkdir()
    try:
        (out / f"Film.en.srt.{TEMP_NAME_PART}.tmp").symlink_to(victim)
    except OSError:
        pytest.skip("this system cannot create symbolic links")
    monkeypatch.setattr(writer, "_token", _fixed_token)
    document = ok_document(send(leg, request(RUN_DOWNLOAD, placement=placement("flat"))))
    failures = [(each["item_id"], each["reason"]) for each in document["download"]["failures"]]
    assert failures == [(FILM_ID, "FileExistsError")]
    assert (victim.read_bytes(), (out / "Film.en.srt").exists()) == (b"keep me", False)


def test_kill_before_the_rule_is_settled_by_refresh(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A download killed after its last outcome leaves the batch `transcribed`; the next refresh moves it on."""
    leg = world.leg
    monkeypatch.setattr(download, "mark_downloaded", _crash)
    with pytest.raises(Crash):
        send(leg, request(RUN_DOWNLOAD))
    monkeypatch.undo()
    assert (len(recorded(leg)), phase_of(leg)) == (4, "transcribed")
    refresh(world)
    assert phase_of(leg) == "downloaded"


def test_failed_at_done_does_not_block_downloaded(ready_world: RunsWorld) -> None:
    """A job that fails at `_done.json` (no result) has no outcome row, yet the batch reaches `downloaded`."""
    world = ready_world
    leg = world.leg
    write_result(world.store, FIRST_BATCH_ID, FILM_ID, RUN_ID, world.now())
    refresh(world)
    assert outcomes(ok_document(send(leg, request(RUN_DOWNLOAD, placement=placement("flat"))))) == {
        FILM_ID: "place",
        PODCAST_ID: "not_finished",
    }
    write_done(world.store, FIRST_BATCH_ID, RUN_ID, world.now())
    assert [each.phase for each in refresh(world).batches] == ["downloaded"]


@dataclass
class RefreshDuringDownload:
    """Progress sink that sends `refresh_status` while the download holds the batch lock."""

    leg: Leg
    refreshed: list[RefreshStatusResponse] = field(default_factory=list)

    def emit(self, _event: ProgressEvent) -> None:
        """Refresh once, from inside the download."""
        if self.refreshed:
            return
        response = self.leg.backend.execute_json(json.dumps({"action": "refresh_status"}))
        assert isinstance(response, RefreshStatusResponse), response
        self.refreshed.append(response)


def test_refresh_skips_a_batch_being_downloaded(ready_world: RunsWorld) -> None:
    """`refresh_status` lists a `ready` batch whose download holds its lock as busy and changes nothing."""
    world = ready_world
    leg = world.leg
    for item_id in (FILM_ID, PODCAST_ID):
        write_result(world.store, FIRST_BATCH_ID, item_id, RUN_ID, world.now())
    refresh(world)
    write_done(world.store, FIRST_BATCH_ID, RUN_ID, world.now())
    sink = RefreshDuringDownload(leg)
    document = leg.document(request(RUN_DOWNLOAD, placement=placement("flat")))
    downloaded = leg.backend.execute_json(document, sink, CancelFlag())
    assert downloaded.status == "ok", downloaded
    busy = sink.refreshed[0]
    assert (busy.busy, busy.batches, phase_of(leg)) == ((FIRST_BATCH_ID,), (), "ready")
    assert [each.phase for each in refresh(world).batches] == ["downloaded"]


def test_get_batch_shows_the_failure_reason(world: RunsWorld) -> None:
    """The job view (`get_batch`) shows a failed job's `result_error`."""
    detail = ok_document(send(world.leg, json.dumps({"action": "get_batch", "batch_id": FIRST_BATCH_ID})))
    errors = {each["item_id"]: each["jobs"][0]["result_error"] for each in detail["batch"]["items"]}
    assert errors[EPISODE_ID] == FAILURE
    assert errors[FILM_ID] is None


def test_overwrite_replaces_through_the_temp_file(world: RunsWorld) -> None:
    """Overwriting replaces the file in place and leaves no temp file; the folder is synced."""
    leg = world.leg
    target = leg.root / "out" / "Film.en.srt"
    target.parent.mkdir()
    target.write_bytes(b"old")
    options = placement("flat", overwrite=True)
    document = ok_document(send(leg, request(RUN_DOWNLOAD, placement=options, confirm_overwrite=True)))
    assert (outcomes(document)[FILM_ID], target.read_bytes()) == ("overwrite", SUBTITLE)
    assert sorted(path.name for path in target.parent.iterdir()) == ["Film.en.srt", "Silent.hu.srt"]
    assert not list(Path(leg.root).glob("**/*.tmp"))
