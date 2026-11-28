"""The upload from the review page and the job page, in the window over moto (pytest-qt, offscreen; plan m6 M6.8).

The upload runs in the activity panel through `QtExecutor`, paused between two of its steps
where the test acts: Cancel between two parts of an object then Resume, which re-sends the same
request and leaves exactly the objects of an uninterrupted upload; a file changed after its
check goes back to review and the page lists it; closing the window during the upload (Q30)
stops it, keeps what was done, and Resume upload on the job page finishes it.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from support.batch_backend import run_ok
from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.buckets import create_bucket
from support.credentials import moto_bucket
from support.gated import wait_for
from support.gui import window_over
from support.interrupt import patch_upload_store
from support.review import SETUP, PausingRunner
from support.runs import RunsWorld
from support.selection_window import settled, settled_window
from support.storage import LARGE_SIZE, PART_BYTES, make_file, store_client
from support.trees import FIXED_MTIME_NS
from support.upload import (
    audio_keys,
    bucket_state,
    change_source,
    open_uploads,
    phase_of,
    recorded_keys,
    source_path,
    upload_world,
)
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.gui.constants import PAGE_JOBS
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.review.view import ReviewPage
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.constants import UPLOAD_BYTES_STAGE, UPLOAD_ITEM_STAGE

# The bucket of these tests.
BUCKET = "review-upload"
# The files of the batch in review.
FILES = 4


def _first_part(event: ProgressEvent) -> bool:
    return event.stage == UPLOAD_BYTES_STAGE and event.completed == PART_BYTES


def _first_file(event: ProgressEvent) -> bool:
    return event.stage == UPLOAD_ITEM_STAGE


def _never(_event: ProgressEvent) -> bool:
    return False


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunsWorld]:
    """A batch in review on moto, its podcast three parts long at a small part size."""
    with moto_bucket():
        create_bucket(BUCKET)
        with upload_world(tmp_path / "leg", BUCKET) as created:
            podcast = source_path(created.leg.root, PODCAST)
            make_file(podcast.parent, LARGE_SIZE).replace(podcast)
            os.utime(podcast, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
            for document in SETUP:
                run_ok(created.leg, document)
            small_parts = R2ObjectStore(store_client(), BUCKET, part_bytes=PART_BYTES)
            patch_upload_store(monkeypatch, lambda _store: small_parts)
            yield created


def _runner(world: RunsWorld, pause_at: Callable[[ProgressEvent], bool]) -> PausingRunner:
    return PausingRunner(world.leg.backend, pause_at)


def _review_page(qtbot: QtBot, window: MainWindow) -> ReviewPage:
    """Open the first batch's review and wait until its upload can start."""
    window.context.signals.open_review.emit(FIRST_BATCH_ID)
    jobs = window.pages.page(PAGE_JOBS)
    assert isinstance(jobs, JobsPage)
    page = jobs.currentWidget()
    assert isinstance(page, ReviewPage)
    wait_for(qtbot, page.action_row.upload_button.isEnabled)
    return page


def _uploads(runner: PausingRunner) -> list[str]:
    return [request.model_dump_json() for request in runner.requests if request.action == "run_upload"]


def _stop_between_parts(qtbot: QtBot, page: ReviewPage, runner: PausingRunner) -> None:
    """Upload until the podcast's first part is sent, show its progress, then Cancel."""
    page.action_row.upload_button.click()
    wait_for(qtbot, runner.paused.is_set)
    box = page.upload_box
    wait_for(qtbot, lambda: "Current file: 5.2 MB of 10.5 MB sent" in box.progress.text())
    assert box.progress_bar.isVisible()
    box.cancel_button.click()
    runner.resume()
    wait_for(qtbot, box.resume_button.isVisible)


def _bucket(world: RunsWorld) -> tuple[list[int], int, str, bool]:
    """Return the podcast's object sizes, the open multipart uploads, the phase, and whether the bucket holds exactly the recorded objects."""
    sizes = dict(bucket_state(world.store)[0])
    keys = audio_keys(world.store)
    podcast = [sizes[key] for key in keys if key.endswith(".mp3")]
    return podcast, open_uploads(BUCKET), phase_of(world), keys == recorded_keys(world)


def test_upload_cancelled_between_parts_resumes(qtbot: QtBot, world: RunsWorld, tmp_path: Path) -> None:
    """Cancel after the podcast's first part: shown stopped; Resume sends the same request and finishes."""
    runner = _runner(world, _first_part)
    page = _review_page(qtbot, settled_window(qtbot, runner, tmp_path))
    _stop_between_parts(qtbot, page, runner)
    box = page.upload_box
    stopped = (box.state.text(), phase_of(world), open_uploads(BUCKET))
    assert stopped == ("Upload: Stopped; continue any time.", "uploading", 0)
    box.resume_button.click()
    wait_for(qtbot, lambda: box.state.text() == "Upload: Upload complete: the job is ready for transcription.")
    first, second = _uploads(runner)
    assert (first == second, _bucket(world)) == (True, ([LARGE_SIZE], 0, "ready", True))


def _sent_back(qtbot: QtBot, page: ReviewPage) -> None:
    """Wait until the upload reports the film back in review and the page lists it again."""
    box = page.upload_box
    wait_for(qtbot, lambda: "back in review" in box.state.text())
    assert FILM in box.state.text()
    tracks = page.tabs.tables["tracks"].rows
    wait_for(qtbot, lambda: [tracks.row(row).shape.file_name for row in range(tracks.rowCount())] == [FILM, FILM])
    wait_for(qtbot, lambda: page.controls.status.text() == "1 file in review; every one is ready to upload.")


def test_a_changed_file_goes_back_to_review(qtbot: QtBot, world: RunsWorld, tmp_path: Path) -> None:
    """A file edited after its check is sent back to review; the page shows it, and Resume upload finishes."""
    window = settled_window(qtbot, _runner(world, _never), tmp_path)
    page = _review_page(qtbot, window)
    change_source(world.leg.root, FILM)
    page.action_row.upload_button.click()
    _sent_back(qtbot, page)
    assert window.statusBar().currentMessage() == f"Job {FIRST_BATCH_ID}: 1 file is back in review."
    upload = page.action_row.upload_button
    wait_for(qtbot, upload.isEnabled)
    assert upload.text() == "Resume upload"
    upload.click()
    wait_for(qtbot, lambda: phase_of(world) == "ready")
    wait_for(qtbot, lambda: page.controls.status.text() == "No file of this job waits for review.")


def _stop_and_close(qtbot: QtBot, window: MainWindow, runner: PausingRunner) -> None:
    """Close the window while the upload is paused: Stop and close, then let the upload see its flag."""
    window.close()
    question = window.closing.question
    assert question is not None
    question.close_button.click()
    runner.resume()
    wait_for(qtbot, lambda: not window.isVisible())


def _resume_on_the_job_page(qtbot: QtBot, window: MainWindow) -> None:
    """Start the window like the app, open the job and press its Resume upload."""
    window.context.hooks.run()
    window.context.signals.open_job.emit(FIRST_BATCH_ID)
    jobs = window.pages.page(PAGE_JOBS)
    assert isinstance(jobs, JobsPage)
    row = jobs.page_view.button_row
    wait_for(qtbot, lambda: "upload" in row.buttons and row.buttons["upload"].text() == "Resume upload")
    row.buttons["upload"].click()


def test_close_during_the_upload_resumes(qtbot: QtBot, world: RunsWorld, tmp_path: Path) -> None:
    """Stop and close during the upload (Q30): what was uploaded stays; Resume upload on the job page finishes."""
    runner = _runner(world, _first_file)
    window = window_over(qtbot, runner, tmp_path)
    _review_page(qtbot, window).action_row.upload_button.click()
    wait_for(qtbot, runner.paused.is_set)
    _stop_and_close(qtbot, window, runner)
    kept = recorded_keys(world)
    assert (phase_of(world), bool(kept)) == ("uploading", True)
    again = settled_window(qtbot, runner, tmp_path)
    _resume_on_the_job_page(qtbot, again)
    wait_for(qtbot, lambda: phase_of(world) == "ready")
    settled(qtbot, again)
    assert (set(kept) <= set(recorded_keys(world)), _bucket(world)[1:]) == (True, (0, "ready", True))
