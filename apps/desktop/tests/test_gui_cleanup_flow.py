"""The retry and cleanup handlers' answers in every order, refusals and the repeat of a cleanup (plan m6 M6.10).

A manual executor answers only when the test says so: an answer after the user went elsewhere
opens nothing, one cloud step per job runs at a time, a stopped cleanup stays with Resume. A
refusal opens the error dialog with its fix; an interrupted cleanup, repeated from the job
page, clears its label and lets a launch through.
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.actions import COPIED_SOURCE, COPYING_RETRY, scenario_batch_id
from support.batches import FIRST_BATCH_ID
from support.cleanup import EVERYTHING, FILM_ID, confirmed, keys_under, patch_store
from support.cleanup_gui import (
    CHANGED_SIGNAL,
    CleanupArea,
    cleanup_area,
    cleanup_world,
    joined_pages,
    only,
    retry_id_of,
)
from support.cloud import CloudWorld, ManualExecutor, ManualTimers
from support.cloud import RecordingOutlet as CloudOutlet
from support.interrupt import Crash, TripStore, Tripwire
from support.jobs import scenario_ports
from support.launch import FOREIGN_BATCH_ID
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse
from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.gui.cleanup.display import busy_text, not_asked_text, retry_created_text
from transcribe_ui.gui.cloud.area import build_models
from transcribe_ui.gui.cloud.start import StartModel
from transcribe_ui.gui.executor import SyncExecutor

RETRY = "create_retry_batch"
CLEANUP = "cleanup_cloud"
INTERRUPTED = "Cloud cleanup interrupted; repeat it"
FILM_JOB = RetryJob(item_id=FILM_ID, language="en")
# The cleanups in flight once another computer's job is deleted too.
TWO_CLEANUPS = 2


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a leg with the transcribed source batch and another computer's job."""
    with cleanup_world(tmp_path) as created:
        yield created


def _manual(world: CloudWorld) -> tuple[CleanupArea, ManualExecutor]:
    created, manual = cleanup_area(world.backend, manual=True)
    assert manual is not None
    return created, manual


def _error(code: str) -> ErrorResponse:
    return ErrorResponse(action=CLEANUP, error=ErrorBody(code=code, message="Refused."))


def _error_code(start: StartModel) -> str | None:
    return None if start.error is None else start.error.code


def test_a_late_retry_opens_nothing(world: CloudWorld) -> None:
    """A retry that answers after the user went to another page reloads the jobs and leaves a note instead."""
    area, manual = _manual(world)
    area.handlers.retry.retry(FIRST_BATCH_ID, (FILM_JOB,))
    area.handlers.steps.navigated()
    manual.answer(RETRY)
    retry_id = retry_id_of(area, FIRST_BATCH_ID)
    assert (area.outlet.signals("open_job"), area.outlet.notes) == ([], [retry_created_text(retry_id)])


def test_late_answers_ask_nothing(world: CloudWorld) -> None:
    """A question or a refusal that arrives after the user went elsewhere opens nothing: a note says so."""
    area, manual = _manual(world)
    cleanup = area.handlers.cleanup
    cleanup.clean_up(FIRST_BATCH_ID, "audio")
    area.handlers.steps.navigated()
    manual.answer(CLEANUP)
    note = not_asked_text(FIRST_BATCH_ID)
    assert (len(area.outlet.asked), area.outlet.notes, len(area.activity.entries)) == (0, [note], 0)
    cleanup.clean_up(FIRST_BATCH_ID, "audio")
    area.handlers.steps.navigated()
    manual.answer(CLEANUP, _error("run_active"))
    assert (len(area.outlet.errors), area.outlet.notes, len(area.activity.entries)) == (0, [note, note], 0)


def test_the_question_is_checked_first(world: CloudWorld) -> None:
    """The unconfirmed request is titled as a check, the confirmed one by what the question's codes delete."""
    area, manual = _manual(world)
    area.handlers.cleanup.clean_up(FOREIGN_BATCH_ID, EVERYTHING)
    assert only(area.activity.entries).title == f"Checking the cleanup of job {FOREIGN_BATCH_ID}"
    manual.answer(CLEANUP)
    question = only(area.outlet.asked)
    assert (question.kind, question.accept_label) == ("unknown", "Delete from the cloud")
    deleting = f"Deleting another computer's job {FOREIGN_BATCH_ID} from the cloud"
    assert only(area.activity.entries).title == deleting
    manual.answer(CLEANUP)
    # The codes say another computer's job, the signal a job of this computer: both sides reload.
    changed = [((FOREIGN_BATCH_ID,),)]
    assert (area.outlet.signals("plan_invalidated"), area.outlet.signals(CHANGED_SIGNAL)) == ([()], changed)


def test_one_cloud_step_per_job(world: CloudWorld) -> None:
    """While a cleanup of a job is in flight, its retry is refused with a note; another job is not held up."""
    area, manual = _manual(world)
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, EVERYTHING)
    area.handlers.retry.retry(FIRST_BATCH_ID, (FILM_JOB,))
    assert (len(manual.pending(CLEANUP)), len(manual.pending(RETRY))) == (1, 0)
    assert area.outlet.notes == [busy_text(FIRST_BATCH_ID)]
    area.handlers.cleanup.delete_unknown(FOREIGN_BATCH_ID)
    assert len(manual.pending(CLEANUP)) == TWO_CLEANUPS


def test_a_stopped_cleanup_resumes(world: CloudWorld) -> None:
    """A stopped cleanup stays in the panel with Resume and reloads the job; Resume finishes it."""
    area, manual = _manual(world)
    area.handlers.cleanup.clean_up(FIRST_BATCH_ID, EVERYTHING)
    manual.answer(CLEANUP)
    manual.answer(CLEANUP, _error("cancelled"))
    entry = only(area.activity.entries)
    assert (entry.state, entry.can_resume, area.outlet.signals(CHANGED_SIGNAL)) == (
        "stopped",
        True,
        [((FIRST_BATCH_ID,),)],
    )
    assert area.activity.resume(entry)
    manual.answer(CLEANUP)
    assert (entry.state, len(keys_under(world.store))) == ("finished", 0)


def test_a_refusal_opens_the_retry_job(tmp_path: Path) -> None:
    """A cleanup refused while a retry still copies the audio: the error dialog opens that retry job."""
    ports = scenario_ports(tmp_path)
    with contextlib.closing(ports.state):
        area, _manual_executor = cleanup_area(Backend(ports))
        area.handlers.cleanup.clean_up(scenario_batch_id(COPIED_SOURCE), EVERYTHING)
    error = only(area.outlet.errors)
    assert error.fix is not None
    assert (error.code, error.fix.kind, error.fix.label) == ("retry_copying", "open_batch", "Open the retry job")
    assert (error.fix.argument, len(area.activity.entries)) == (scenario_batch_id(COPYING_RETRY), 0)
    assert area.outlet.retries[0] is not None


@dataclass
class _CrashingDeletes(TripStore):
    """The bucket, but every delete is a kill of the app."""

    def delete(self, key: str) -> None:
        """Crash instead of deleting the key."""
        raise Crash(key)


def test_a_repeat_lets_a_launch_through(world: CloudWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """Interrupted cleanup: labelled, no launch; Repeat cleanup on the job page finishes it, and a launch starts."""
    patch_store(monkeypatch, "known", lambda store: _CrashingDeletes(store, Tripwire()))
    with contextlib.suppress(Crash):
        world.backend.execute_json(confirmed(EVERYTHING))
    patch_store(monkeypatch, "known", lambda store: store)
    area, _manual_executor = cleanup_area(world.backend)
    pages = joined_pages(world.backend, area, FIRST_BATCH_ID)
    page = pages.jobs.page
    repeat = next(button for button in page.buttons if button.key == "cleanup_everything")
    labels = () if page.header is None else page.header.labels
    assert (repeat.label, INTERRUPTED in labels) == ("Repeat cleanup", True)
    start = build_models(SyncExecutor(world.backend), CloudOutlet(), ManualTimers()).start
    start.show_dialog()
    assert _error_code(start) == "cleanup_pending"
    page.press("cleanup_everything")
    pages.forward()
    labels = () if page.header is None else page.header.labels
    start.show_dialog()
    assert (INTERRUPTED in labels, _error_code(start), start.can_start) == (False, None, True)
    start.start()
    assert start.phase == "launched"
