"""The retry and cloud cleanup handlers over a seeded cloud world (plan m6 M6.10).

Driven like a user on `SyncExecutor`: the job page's Retry with the ticked jobs, the cleanups
with the backend's confirmations (the second one when the subtitles were not downloaded), and
another computer's job deleted from the start dialog. The requests a scripted state builds are
request goldens (`gui_cleanup_*`), which the parity runner also sends through the backend and
the CLI. The orders of answers, refusals and the repeat of an interrupted cleanup are in
`test_gui_cleanup_flow.py`.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from support.batches import FIRST_BATCH_ID, build_batch_tree
from support.cleanup import EVERYTHING, FILM_ID, PODCAST_ID, confirmed, keys_under
from support.cleanup_gui import (
    CHANGED_SIGNAL,
    CleanupArea,
    cleanup_area,
    cleanup_world,
    joined_pages,
    only,
    retry_id_of,
    seed_cleanup_leg,
)
from support.cloud import CloudWorld, documents
from support.credentials import moto_bucket
from support.golden import GOLDEN_DIR, assert_golden
from support.launch import FOREIGN_BATCH_ID
from support.launch_legs import LaunchLegs
from support.parity import run_parity
from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.i18n.catalog import activate

SUBPROCESS_TIMEOUT_S = 60
CLEANUP = "cleanup_cloud"
UNDONE = "This cannot be undone."
AUDIO_TEXT = "The job's audio will be deleted from the cloud; its subtitles stay, but it can no longer be retried."
EVERYTHING_TEXT = "All of the job's data will be deleted from the cloud: audio, subtitles and its description."
NOT_DOWNLOADED_TEXT = (
    "The job's subtitles may not be downloaded yet; after this, only the backup on the cloud volume keeps them."
)
FILM_JOB = RetryJob(item_id=FILM_ID, language="en")
PODCAST_JOB = RetryJob(item_id=PODCAST_ID, language="en")
# The Qt-free modules of the handlers.
QT_FREE_MODULES = (
    "transcribe_ui.gui.cleanup.area",
    "transcribe_ui.gui.cleanup.constants",
    "transcribe_ui.gui.cleanup.display",
    "transcribe_ui.gui.cleanup.panel",
    "transcribe_ui.gui.cleanup.retry",
    "transcribe_ui.gui.cleanup.viewmodel",
)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a leg with the transcribed source batch and another computer's job."""
    with cleanup_world(tmp_path) as created:
        yield created


@pytest.fixture
def area(world: CloudWorld) -> CleanupArea:
    """Return the handlers on `SyncExecutor` over the world."""
    created, _manual = cleanup_area(world.backend)
    return created


def _texts(area: CleanupArea) -> list[tuple[str, bool]]:
    return [(line.text, line.warning) for line in area.outlet.asked[-1].lines]


def _sent_flags(area: CleanupArea) -> list[tuple[bool, bool]]:
    sent = [request.model_dump(mode="json") for request in area.runner.requests]
    return [(document["confirm"], document["confirm_not_downloaded"]) for document in sent]


def test_the_handlers_do_not_load_qt() -> None:
    """The handlers' modules import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_retry_sends_the_ticked_jobs(world: CloudWorld, area: CleanupArea) -> None:
    """Retry sends the ticked jobs only; the panel shows the copy; the jobs reload and the retry job opens."""
    pages = joined_pages(world.backend, area, FIRST_BATCH_ID)
    page = pages.jobs.page
    assert page.ticked() == (FILM_JOB, PODCAST_JOB)
    page.set_ticked(PODCAST_JOB, ticked=False)
    page.press("retry")
    assert pages.jobs.outlet.emitted == [("retry_jobs", (FIRST_BATCH_ID, (FILM_JOB,)))]
    pages.forward()
    entry = only(area.activity.entries)
    event = entry.last_event
    assert event is not None
    shown = (entry.state, entry.title, event.stage, event.completed, event.total)
    assert shown == ("finished", f"Retry of job {FIRST_BATCH_ID}: copying the audio", "upload_item", 1, 1)
    retry_id = retry_id_of(area, FIRST_BATCH_ID)
    assert (area.outlet.signals("open_job"), area.outlet.notes) == ([(retry_id,)], [])
    assert_golden("gui_cleanup_retry.json", documents(area.runner.requests))


def test_no_retry_without_ticked_jobs(area: CleanupArea) -> None:
    """A retry with nothing ticked (or not a tuple of jobs) sends nothing."""
    area.handlers.retry.retry(FIRST_BATCH_ID, ())
    area.handlers.retry.retry(FIRST_BATCH_ID, ["not", "jobs"])
    assert (len(area.runner.requests), len(area.activity.entries)) == (0, 0)


def test_a_retry_after_the_audio_cleanup(world: CloudWorld, area: CleanupArea) -> None:
    """Retry on a page shown before the audio cleanup: the panel explains why, and every job reloads."""
    assert world.backend.execute_json(confirmed("audio")).status == "ok"
    area.handlers.retry.retry(FIRST_BATCH_ID, (FILM_JOB,))
    entry = only(area.activity.entries)
    explained = "The job's audio was deleted from the cloud, so it cannot be retried; create a new job instead."
    assert (entry.state, entry.status, entry.can_resume) == ("failed", explained, True)
    code = None if entry.error is None else entry.error.code
    assert (code, area.outlet.signals("open_job")) == ("audio_deleted", [])
    assert area.outlet.signals(CHANGED_SIGNAL) == [((),)]


def test_cleaning_up_the_audio_asks_then_deletes(world: CloudWorld, area: CleanupArea) -> None:
    """Delete audio asks the backend's confirmation, which names the lost retry; Delete audio deletes it."""
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, "audio")
    question = only(area.outlet.asked)
    heading = f"Delete the audio of job {FIRST_BATCH_ID} from the cloud?"
    assert (question.title, question.heading, question.accept_label) == (
        "Delete from the cloud",
        heading,
        "Delete audio",
    )
    assert _texts(area) == [(AUDIO_TEXT, False), (UNDONE, False)]
    entry = only(area.activity.entries)
    assert (entry.state, entry.title) == ("finished", f"Deleting the audio of job {FIRST_BATCH_ID} from the cloud")
    assert area.outlet.signals(CHANGED_SIGNAL) == [((FIRST_BATCH_ID,),)]
    assert all("/audio/" not in key for key in keys_under(world.store))
    assert_golden("gui_cleanup_audio.json", documents(area.runner.requests))


def test_everything_not_downloaded_asks_twice(world: CloudWorld, area: CleanupArea) -> None:
    """Results not downloaded yet: the question also shows the backend's second confirmation, as a warning."""
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, EVERYTHING)
    assert area.outlet.asked[0].accept_label == "Delete everything"
    assert _texts(area) == [(EVERYTHING_TEXT, False), (NOT_DOWNLOADED_TEXT, True), (UNDONE, False)]
    assert _sent_flags(area) == [(False, False), (True, True)]
    assert not keys_under(world.store)
    assert_golden("gui_cleanup_everything.json", documents(area.runner.requests))


def test_a_declined_question_deletes_nothing(world: CloudWorld, area: CleanupArea) -> None:
    """Cancel in the question sends nothing more; the question's entry has left the panel."""
    kept = keys_under(world.store)
    area.outlet.accept = False
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, EVERYTHING)
    assert (area.runner.actions(), keys_under(world.store)) == ([CLEANUP], kept)
    assert not area.activity.entries
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, "nothing")
    assert area.runner.actions() == [CLEANUP], "an unknown scope sends nothing"


def test_deleting_an_unknown_job_plans_again(world: CloudWorld, area: CleanupArea) -> None:
    """Delete from cloud on another computer's job asks once, deletes it, then emits `plan_invalidated`."""
    area.handlers.cleanup.delete_unknown(FOREIGN_BATCH_ID)
    question = only(area.outlet.asked)
    heading = f"Delete another computer's job {FOREIGN_BATCH_ID} from the cloud?"
    assert (question.heading, question.accept_label) == (heading, "Delete from the cloud")
    unknown = (
        f"The job {FOREIGN_BATCH_ID} is not known on this computer; all its data (1 file) will be deleted from the "
        "cloud. Another computer uploaded this job; it may still need to download the subtitles."
    )
    # Its manifest does not say who uploaded it: the stronger form, shown as a warning (architecture §3.9).
    assert _texts(area) == [(unknown, True), (UNDONE, False)]
    assert (area.outlet.signals("plan_invalidated"), len(area.outlet.signals(CHANGED_SIGNAL))) == ([()], 0)
    assert not keys_under(world.store, FOREIGN_BATCH_ID)
    assert_golden("gui_cleanup_unknown.json", documents(area.runner.requests))


def test_the_question_in_hungarian(area: CleanupArea) -> None:
    """The question, its button and the panel's title follow the language."""
    activate("hu")
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, "audio")
    question = area.outlet.asked[0]
    expected = (f"Törli a felhőből a {FIRST_BATCH_ID} feladat hangját?", "Hang törlése", "Ez nem vonható vissza.")
    assert (question.heading, question.accept_label, question.lines[-1].text) == expected
    assert area.activity.entries[0].title == f"A {FIRST_BATCH_ID} feladat hangjának törlése a felhőből"


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_cleanup_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the handlers built gives the same response from the backend and the CLI."""
    templates = [json.dumps(document) for document in json.loads(golden.read_text(encoding="utf-8"))]
    with moto_bucket():
        legs = LaunchLegs(seed=seed_cleanup_leg)
        exchanges = run_parity(tmp_path, templates, prepare=build_batch_tree, ports=legs)
    statuses = {json.loads(exchange.response)["status"] for exchange in exchanges}
    assert statuses <= {"ok", "needs_confirmation"}
