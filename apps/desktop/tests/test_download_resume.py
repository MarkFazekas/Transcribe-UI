"""`run_download` (plan m5 M5.13): interruptions, confirmations and refusals.

A kill is simulated with `Crash` (a `BaseException` the backend does not catch): what was committed
stays, nothing else runs. Sending the request again must finish the download without a partial or
temp file left behind.
"""

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID, media_dir
from support.confirm import confirmed_document
from support.download import (
    EXTRACTED_SRT,
    FILM_ID,
    RUN_DOWNLOAD,
    SILENT_ID,
    error_document,
    expected_outcomes,
    ok_document,
    outcomes,
    phase_of,
    placement,
    plan_error_codes,
    recorded,
    request,
    seed_download_batch,
    send,
    temp_files,
    write_subtitles,
)
from support.interrupt import Crash
from support.monitor import SUBTITLE
from support.parity import Leg
from support.runs import RunsWorld
from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.placement import download, writer
from transcribe_ui.placement.constants import STALE_TEMP_S

# The mirror strategy with empty results included: three files written, the failed job skipped.
MIRROR = placement("mirror", include_empty=True)
# The writer's function that writes a temp file.
STAGED = "_staged"
# The film's target of the mirror strategy below the leg's root.
FILM_TARGET = "out/Movies/Film.en.srt"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The runs world (moto, every credential) with the transcribed batch and its subtitles."""
    seed_download_batch(runs_world.ports.state, runs_world.leg.root, "transcribed")
    write_subtitles(runs_world.store)
    return runs_world


@dataclass
class CancelAfter:
    """Progress sink that sets the cancel flag after `jobs` download events."""

    flag: CancelFlag
    jobs: int

    def emit(self, event: ProgressEvent) -> None:
        """Count the event; cancel once enough jobs are done."""
        if event.completed >= self.jobs:
            self.flag.cancel()


@dataclass
class CrashOnce:
    """Stands in for a function: its first call crashes, before or after calling the real one."""

    real: object
    before: bool
    calls: list[int] = field(default_factory=list)

    def __call__(self, *args: object) -> object:
        """Call the real function; crash on the first call."""
        self.calls.append(1)
        first = len(self.calls) == 1
        if first and self.before:
            raise Crash
        assert callable(self.real)
        found = self.real(*args)
        if first:
            raise Crash
        return found


def _crash_once(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, *, before: bool) -> None:
    monkeypatch.setattr(owner, name, CrashOnce(getattr(owner, name), before))


@dataclass(frozen=True)
class RefuseFilm:
    """Stands in for the temp file write: the OS refuses the film's subtitle, the others are written."""

    real: object

    def __call__(self, target: Path, body: bytes) -> Path:
        """Refuse the film's target, write the others."""
        if target.name.startswith("Film"):
            raise PermissionError
        assert callable(self.real)
        written = self.real(target, body)
        assert isinstance(written, Path)
        return written


def _failures(document: dict[str, object]) -> list[tuple[object, ...]]:
    download = document["download"]
    assert isinstance(download, dict)
    return [(each["item_id"], each["code"], each["reason"]) for each in download["failures"]]


def _mirror(leg: Leg) -> dict[str, str]:
    return outcomes(ok_document(send(leg, request(RUN_DOWNLOAD, placement=MIRROR))))


def test_cancel_then_resume(world: RunsWorld) -> None:
    """A cancelled download keeps the outcomes it recorded; sending it again places the rest."""
    leg = world.leg
    flag = CancelFlag()
    document = leg.document(request(RUN_DOWNLOAD, placement=MIRROR))
    cancelled = leg.backend.execute_json(document, CancelAfter(flag, 1), flag)
    assert error_document(cancelled)["code"] == "cancelled"
    assert (len(recorded(leg)), (leg.root / FILM_TARGET).read_bytes(), phase_of(leg)) == (1, SUBTITLE, "transcribed")
    assert _mirror(leg) == expected_outcomes(film="skip_existing", podcast="place")
    assert (phase_of(leg), temp_files(leg.root)) == ("downloaded", [])


def test_kill_between_temp_file_and_replace(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A kill after the temp file is written leaves no target; a temp file a kill left is replaced on resume."""
    leg = world.leg
    _crash_once(monkeypatch, writer, "_fill", before=False)
    with pytest.raises(Crash):
        send(leg, request(RUN_DOWNLOAD, placement=MIRROR))
    monkeypatch.undo()
    assert (recorded(leg), (leg.root / FILM_TARGET).exists(), temp_files(leg.root)) == ([], False, [])
    # A hard kill skips the cleanup and leaves the partial temp file behind; once stale it is removed.
    leftover = leg.root / f"{FILM_TARGET}.0a1b2c.tmp"
    leftover.write_bytes(b"partial")
    stale = time.time() - STALE_TEMP_S - 1
    os.utime(leftover, (stale, stale))
    assert _mirror(leg) == expected_outcomes(podcast="place")
    assert ((leg.root / FILM_TARGET).read_bytes(), temp_files(leg.root)) == (SUBTITLE, [])


def test_kill_before_the_outcome_is_recorded(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A kill after a file is placed but before its outcome is recorded: the resume skips it as existing."""
    leg = world.leg
    _crash_once(monkeypatch, download, "record_placement", before=True)
    with pytest.raises(Crash):
        send(leg, request(RUN_DOWNLOAD, placement=MIRROR))
    monkeypatch.undo()
    assert (recorded(leg), (leg.root / FILM_TARGET).read_bytes()) == ([], SUBTITLE)
    assert _mirror(leg) == expected_outcomes(film="skip_existing", podcast="place")
    assert phase_of(leg) == "downloaded"


def test_missing_subtitle_does_not_block_the_others(world: RunsWorld) -> None:
    """A finished job whose subtitle is gone from R2 is listed as failed; the other jobs are placed."""
    leg = world.leg
    key = result_srt_key(FIRST_BATCH_ID, FILM_ID, "en")
    world.store.delete(key)
    first = ok_document(send(leg, request(RUN_DOWNLOAD, placement=MIRROR)))
    assert _failures(first) == [(FILM_ID, "subtitle_missing", None)]
    assert (len(recorded(leg)), phase_of(leg)) == (3, "transcribed")
    world.store.write(key, SUBTITLE, "application/x-subrip")
    again = outcomes(ok_document(send(leg, request(RUN_DOWNLOAD, placement=MIRROR))))
    assert (again[FILM_ID], phase_of(leg)) == ("place", "downloaded")


def test_overwrite_needs_confirmation(world: RunsWorld) -> None:
    """Overwriting existing files needs `confirm_overwrite`; without it nothing changes."""
    leg = world.leg
    sidecar = media_dir(leg.root) / "Movies" / "Film.en.srt"
    options = placement(overwrite=True)
    asked = send(leg, request(RUN_DOWNLOAD, placement=options))
    assert asked.status == "needs_confirmation"
    assert asked.confirmations[0].details == {"targets": ["Film.en.srt"]}
    assert (recorded(leg), sidecar.read_bytes()) == ([], b"")
    confirmed = ok_document(send(leg, confirmed_document(request(RUN_DOWNLOAD, placement=options), asked)))
    assert (outcomes(confirmed)[FILM_ID], sidecar.read_bytes()) == ("overwrite", SUBTITLE)


def test_plan_errors_write_nothing(world: RunsWorld) -> None:
    """A plan with errors (a relative output folder) is refused with its errors; nothing is written."""
    refused = send(world.leg, request(RUN_DOWNLOAD, placement=placement("flat", output_folder="out")))
    assert plan_error_codes(refused) == ["output_not_absolute"]
    assert not recorded(world.leg)


def test_refused_write_does_not_block_the_others(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write the OS refuses (a target held open on Windows) is listed as failed; the others are placed."""
    leg = world.leg
    monkeypatch.setattr(writer, STAGED, RefuseFilm(getattr(writer, STAGED)))
    document = ok_document(send(leg, request(RUN_DOWNLOAD, placement=placement("flat"))))
    assert _failures(document) == [(FILM_ID, "download_write_failed", "PermissionError")]
    assert outcomes(document)[SILENT_ID] == "place"
    assert (temp_files(leg.root), phase_of(leg)) == ([], "transcribed")


def test_busy_batch_is_refused(world: RunsWorld) -> None:
    """Another request holding the batch's lock gives `batch_busy`."""
    with world.ports.state.locks.batch(FIRST_BATCH_ID):
        refused = send(world.leg, request(RUN_DOWNLOAD))
    assert error_document(refused)["code"] == "batch_busy"


def test_extracted_subtitle_needs_no_r2(batch_leg: Leg) -> None:
    """A download that writes only extracted subtitles never opens R2, so it works without R2 credentials."""
    seed_download_batch(batch_leg.ports.state, batch_leg.root, "transcribed")
    found = outcomes(ok_document(send(batch_leg, request(RUN_DOWNLOAD))))
    assert found == expected_outcomes(film="skip_existing")
    assert (media_dir(batch_leg.root) / "Movies" / "Silent.hu.srt").read_text(encoding="utf-8") == EXTRACTED_SRT


def test_missing_original_folder_is_skipped(world: RunsWorld) -> None:
    """Next to the media a vanished original folder skips its files (requirements "Subtitle download options"); the others are placed."""
    leg = world.leg
    movies = media_dir(leg.root) / "Movies"
    shutil.rmtree(movies)
    found = outcomes(ok_document(send(leg, request(RUN_DOWNLOAD))))
    assert (found[FILM_ID], found[SILENT_ID]) == ("skip_folder_missing", "skip_folder_missing")
    assert (phase_of(leg), movies.exists()) == ("downloaded", False)
