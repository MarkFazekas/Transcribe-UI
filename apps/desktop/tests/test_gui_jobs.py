"""The jobs list and the job page's view-models over every seeded scenario (plan m6 M6.5).

Driven like a user on `SyncExecutor`: list (with and without hidden jobs), open a job, press
its buttons. The buttons follow the allowed actions; the ones other parts handle emit their
signal with the right arguments; hide, show, delete (through the backend's confirmation) and
continue to review send their requests. The requests a scripted state builds are request
goldens (`gui_jobs_*`), which the parity runner also sends through the backend and the CLI.
"""

import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from sqlalchemy import select

from support.actions import (
    COPIED_SOURCE,
    COPYING_RETRY,
    PAST_RETRY,
    RETRIED_SOURCE,
    SCENARIOS,
    scenario_batch_id,
    scenario_item_id,
)
from support.golden import GOLDEN_DIR, assert_golden
from support.jobs import JobsArea, jobs_area, scenario_ports
from support.parity import run_parity
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Request
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.payloads import JobPayload, RetryJob
from transcribe_ui.gui.jobs.constants import JobButtonKey
from transcribe_ui.gui.jobs.display import created_text
from transcribe_ui.gui.steps.viewmodel import step_views
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.texts import code_text
from transcribe_ui.state.models import JobRow
from transcribe_ui.state.store import LocalState

SUBPROCESS_TIMEOUT_S = 60
LANGUAGE = "en"
REVIEW: Final[JobButtonKey] = "review"
UPLOAD: Final[JobButtonKey] = "upload"
DOWNLOAD: Final[JobButtonKey] = "download"
RETRY: Final[JobButtonKey] = "retry"
CLEAN_AUDIO: Final[JobButtonKey] = "cleanup_audio"
CLEAN_ALL: Final[JobButtonKey] = "cleanup_everything"
HIDE: Final[JobButtonKey] = "hide"
DELETE: Final[JobButtonKey] = "delete"
CHANGED = "batches_changed"
DONE: Final = "done"
# The job page's buttons per scenario: its allowed actions as buttons, each once.
BUTTONS = MappingProxyType(
    {
        "composing": ("edit_selection", "continue_review", DELETE),
        "reviewing_open": (DELETE, REVIEW),
        "reviewing_done": (DELETE, REVIEW, UPLOAD),
        "reviewing_single": (DELETE, REVIEW, UPLOAD),
        "uploading": (REVIEW, UPLOAD, CLEAN_ALL),
        "uploading_done": (UPLOAD, CLEAN_ALL),
        "ready": (DOWNLOAD, CLEAN_ALL),
        "transcribed_flagged": (DOWNLOAD, RETRY, CLEAN_AUDIO, CLEAN_ALL),
        "transcribed_clean": (DOWNLOAD, CLEAN_AUDIO, CLEAN_ALL),
        "transcribed_audio_pending": (DOWNLOAD, CLEAN_AUDIO, CLEAN_ALL),
        "downloaded_no_speech": (DOWNLOAD, RETRY, CLEAN_AUDIO, CLEAN_ALL, HIDE),
        "downloaded_audio_deleted": (DOWNLOAD, CLEAN_ALL, HIDE),
        "downloaded_hidden": (DOWNLOAD, CLEAN_AUDIO, CLEAN_ALL, "show"),
        "cleaned": (HIDE,),
        "cleaned_pending": (DOWNLOAD, CLEAN_ALL, HIDE),
        COPIED_SOURCE: (DOWNLOAD, RETRY),
        COPYING_RETRY: (REVIEW, UPLOAD, CLEAN_ALL),
        RETRIED_SOURCE: (DOWNLOAD, CLEAN_ALL, HIDE),
        PAST_RETRY: (DOWNLOAD, CLEAN_ALL),
    },
)
# The Qt-free modules of the jobs pages and the step header.
QT_FREE_MODULES = (
    "transcribe_ui.gui.jobs.commands",
    "transcribe_ui.gui.jobs.constants",
    "transcribe_ui.gui.jobs.display",
    "transcribe_ui.gui.jobs.viewmodel",
    "transcribe_ui.gui.steps.constants",
    "transcribe_ui.gui.steps.viewmodel",
)


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return ports over a database holding every scenario's batch (closed afterwards)."""
    created = scenario_ports(tmp_path)
    with closing(created.state):
        yield created


@pytest.fixture
def area(ports: Ports) -> JobsArea:
    """Return the jobs pages' view-models over the seeded backend."""
    return jobs_area(Backend(ports))


def _documents(requests: Sequence[Request]) -> str:
    documents = [request.model_dump(mode="json") for request in requests]
    return f"{json.dumps(documents, indent=2, ensure_ascii=False)}\n"


def _opened(area: JobsArea, name: str) -> JobsArea:
    area.page.open_batch(scenario_batch_id(name))
    return area


def _sent(area: JobsArea) -> int:
    return len(area.runner.requests)


def _button_keys(area: JobsArea) -> tuple[str, ...]:
    return tuple(button.key for button in area.page.buttons)


def _label(area: JobsArea, key: str) -> str:
    return next(button.label for button in area.page.buttons if button.key == key)


def test_the_view_models_do_not_load_qt() -> None:
    """The jobs pages' and the step header's view-models import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_the_list_shows_every_job_newest_first(area: JobsArea) -> None:
    """The list keeps the backend's order (newest first); hidden jobs only when asked; the start-up step proceeds."""
    jobs_list = area.jobs_list
    proceeded: list[bool] = []
    jobs_list.reload(lambda: proceeded.append(True))
    shown = [row.batch_id for row in jobs_list.rows]
    expected = sorted((scenario_batch_id(name) for name in SCENARIOS), reverse=True)
    hidden = scenario_batch_id("downloaded_hidden")
    assert (proceeded, shown) == ([True], [batch_id for batch_id in expected if batch_id != hidden])
    jobs_list.include_hidden(shown=True)
    assert [row.batch_id for row in jobs_list.rows] == expected
    assert_golden("gui_jobs_list.json", _documents(area.runner.requests))


def test_a_list_row_shows_the_job(area: JobsArea) -> None:
    """A row shows the creation time, stage, phase, counts, languages and labels from the catalog."""
    jobs_list = area.jobs_list
    jobs_list.include_hidden(shown=True)
    rows = {row.batch_id: row for row in jobs_list.rows}
    pending = rows[scenario_batch_id("transcribed_audio_pending")]
    shown = (pending.stage_text, pending.status, pending.files, pending.transcriptions, pending.languages)
    assert shown == ("Download", "Transcribed", "2", "2", LANGUAGE)
    expected_labels = ("Cloud cleanup interrupted; repeat it", "Subtitles ready to download")
    assert pending.labels == (*expected_labels, "Audio deleted from the cloud")
    summary = next(listed for listed in jobs_list.summaries if listed.batch_id == pending.batch_id)
    assert pending.created == created_text(summary.created_at)
    assert rows[scenario_batch_id("downloaded_hidden")].labels == ("Hidden",)


def test_every_phase_renders_its_page(area: JobsArea) -> None:
    """Each scenario's page shows its stage, phase, labels, one row per transcription and its buttons."""
    for name, buttons in BUTTONS.items():
        page = _opened(area, name).page
        assert page.detail is not None, name
        assert page.header is not None, name
        summary = page.detail.summary
        labels = tuple(code_text("batch_label", label) for label in summary.labels)
        expected = (code_text("batch_stage", summary.stage), code_text("batch_phase", summary.phase), labels)
        assert (page.header.stage_text, page.header.status, page.header.labels) == expected, name
        shown = (len(page.rows), _button_keys(area), page.error)
        assert shown == (len(SCENARIOS[name].members), buttons, None), name


def test_flagged_files_show_their_reason(area: JobsArea, ports: Ports) -> None:
    """A flagged job shows its reason (the catalog text of the desktop's code, else the worker's error) and a tick box."""
    flagged = "transcribed_flagged"
    missing = JobPayload(mode="normal", result_error="missing result", result_error_code="result_missing")
    _fail(ports.state, flagged, missing)
    worker_error = JobPayload(mode="normal", result_error="CUDA out of memory")
    _fail(ports.state, "downloaded_audio_deleted", worker_error)
    page = _opened(area, flagged).page
    first, failed = page.rows
    assert (first.flagged, first.retry, first.reason, first.status) == (False, None, "", "Done")
    reason = "The cloud machine left no result for the transcription."
    retry = RetryJob(item_id=scenario_item_id(flagged, 1), language=LANGUAGE)
    assert (failed.flagged, failed.status, failed.reason, failed.retry) == (True, "Failed", reason, retry)
    assert (failed.kind, failed.file_status, failed.file_name) == ("Transcription", "Uploaded", "file1.mp3")
    worker = _opened(area, "downloaded_audio_deleted").page.rows[1]
    # The audio is gone, so no retry is allowed: the job is marked, without a tick box.
    assert (worker.flagged, worker.reason, worker.retry) == (True, "CUDA out of memory", None)
    no_speech = _opened(area, "downloaded_no_speech").page.rows[1]
    assert (no_speech.status, no_speech.reason, no_speech.retry is not None) == ("No speech", "", True)


def _fail(state: LocalState, name: str, payload: JobPayload) -> None:
    """Store a failure reason on the scenario's second job (the failed one)."""
    item_id = scenario_item_id(name, 1)
    with state.session() as session:
        job = session.scalars(select(JobRow).where(JobRow.item_id == item_id)).one()
        job.payload = payload.model_dump(mode="json")


def test_signal_buttons_emit_their_signal(area: JobsArea) -> None:
    """Buttons other parts handle only emit their signal with the batch (and the scope or the ticked jobs)."""
    cases: tuple[tuple[str, JobButtonKey, str], ...] = (
        ("composing", "edit_selection", "edit_selection"),
        ("reviewing_open", REVIEW, "open_review"),
        ("reviewing_done", UPLOAD, "run_upload"),
        ("ready", DOWNLOAD, "open_download"),
    )
    for name, key, signal_name in cases:
        _opened(area, name).page.press(key)
        assert area.outlet.emitted[-1] == (signal_name, (scenario_batch_id(name),))
    clean = scenario_batch_id("transcribed_clean")
    _opened(area, "transcribed_clean").page.press(CLEAN_AUDIO)
    assert area.outlet.emitted[-1] == ("clean_up", (clean, "audio"))
    area.page.press(CLEAN_ALL)
    assert area.outlet.emitted[-1] == ("clean_up", (clean, "everything"))
    sent = _sent(area)
    area.page.press("delete")
    assert _sent(area) == sent, "a button the job does not show does nothing"


def test_repeat_cleanup_and_resume_upload(area: JobsArea) -> None:
    """An interrupted cleanup reads "Repeat cleanup" and repeats its scope; an unfinished upload "Resume upload"."""
    audio = _opened(area, "transcribed_audio_pending")
    labels = (_label(audio, CLEAN_AUDIO), _label(audio, CLEAN_ALL))
    assert labels == ("Repeat cleanup", "Delete everything from the cloud")
    audio.page.press(CLEAN_AUDIO)
    assert area.outlet.emitted[-1] == ("clean_up", (scenario_batch_id("transcribed_audio_pending"), "audio"))
    everything = _opened(area, "cleaned_pending")
    assert _label(everything, CLEAN_ALL) == "Repeat cleanup"
    everything.page.press(CLEAN_ALL)
    assert area.outlet.emitted[-1] == ("clean_up", (scenario_batch_id("cleaned_pending"), "everything"))
    uploads = (_label(_opened(area, "uploading"), UPLOAD), _label(_opened(area, "reviewing_done"), UPLOAD))
    assert uploads == ("Resume upload", "Upload")


def test_retry_emits_the_ticked_jobs(area: JobsArea) -> None:
    """Retry emits the ticked failed and no-speech jobs; with none ticked it is disabled and does nothing."""
    name = "transcribed_flagged"
    page = _opened(area, name).page
    job = RetryJob(item_id=scenario_item_id(name, 1), language=LANGUAGE)
    assert page.ticked() == (job,)
    page.press(RETRY)
    assert area.outlet.emitted[-1] == ("retry_jobs", (scenario_batch_id(name), (job,)))
    page.set_ticked(job, ticked=False)
    emitted = len(area.outlet.emitted)
    page.press(RETRY)
    assert (_retry_enabled(area), len(area.outlet.emitted)) == (False, emitted)
    page.reload()
    assert not page.ticked(), "a reload keeps the user's ticks"
    page.set_ticked(job, ticked=True)
    assert _retry_enabled(area)


def _retry_enabled(area: JobsArea) -> bool:
    return next(button.enabled for button in area.page.buttons if button.key == RETRY)


def test_hide_and_show_round_trip(area: JobsArea) -> None:
    """Hide leaves the job out of the default list, Show brings it back; each change emits `batches_changed`."""
    name = "downloaded_no_speech"
    batch_id = scenario_batch_id(name)
    jobs_list = area.jobs_list
    jobs_list.reload()
    assert [button.key for button in jobs_list.buttons(batch_id)] == [HIDE]
    area.runner.requests.clear()
    jobs_list.press(batch_id, HIDE)
    assert area.outlet.emitted[-1] == (CHANGED, ((batch_id,),))
    jobs_list.reload()
    assert batch_id not in [row.batch_id for row in jobs_list.rows]
    _opened(area, name).page.press("show")
    jobs_list.reload()
    assert batch_id in [row.batch_id for row in jobs_list.rows]
    changes = [request for request in area.runner.requests if request.action == "set_batch_hidden"]
    assert_golden("gui_jobs_hide_show.json", _documents(changes))


def test_delete_goes_through_the_backend_confirmation(area: JobsArea) -> None:
    """Delete asks the backend's question; Cancel keeps the job, Confirm deletes it and goes back to the list."""
    name = "composing"
    batch_id = scenario_batch_id(name)
    page = _opened(area, name).page
    area.outlet.accept = False
    page.press(DELETE)
    assert [view.confirmations[0].message for view in area.outlet.asked] == [
        "The job and its 2 files will be deleted from this computer's job list.",
    ]
    page.reload()
    assert page.error is None
    area.outlet.accept = True
    area.runner.requests.clear()
    page.press(DELETE)
    asked, confirmed = area.runner.requests
    assert_golden("gui_jobs_delete.json", _documents([asked, confirmed]))
    assert area.outlet.emitted[-2:] == [("open_jobs", ()), (CHANGED, ((batch_id,),))]
    area.page.close()
    area.jobs_list.include_hidden(shown=True)
    assert batch_id not in [row.batch_id for row in area.jobs_list.rows]


def test_continue_to_review_opens_the_review(area: JobsArea) -> None:
    """Continue to review sends `start_review`, then emits `batches_changed` and `open_review`."""
    name = "composing"
    batch_id = scenario_batch_id(name)
    page = _opened(area, name).page
    area.runner.requests.clear()
    page.press("continue_review")
    assert_golden("gui_jobs_continue_review.json", _documents(area.runner.requests))
    assert area.outlet.emitted[-2:] == [(CHANGED, ((batch_id,),)), ("open_review", (batch_id,))]
    page.reload()
    assert page.detail is not None
    assert page.detail.summary.phase == "reviewing"
    assert _button_keys(area) == (DELETE, REVIEW)


def test_the_page_reloads_only_for_its_job(area: JobsArea) -> None:
    """`batches_changed` reloads the page for its job or any job, not for another job, nor once closed."""
    page = _opened(area, "ready").page
    count = _sent(area)
    page.on_batches_changed((scenario_batch_id("cleaned"),))
    other = _sent(area)
    page.on_batches_changed((scenario_batch_id("ready"),))
    page.on_batches_changed(())
    own = _sent(area)
    page.close()
    page.on_batches_changed(())
    assert (other, own, _sent(area)) == (count, count + 2, count + 2)
    assert_golden("gui_jobs_open.json", _documents(area.runner.requests[:1]))


def test_an_unknown_job_shows_its_error(area: JobsArea) -> None:
    """A job that is gone shows the error of its load in the page, in the active language."""
    area.page.open_batch("batch_20990101_000000")
    assert area.page.error is not None
    assert area.page.error.code == "batch_not_found"
    assert area.page.header is None


def test_the_texts_follow_the_language(area: JobsArea) -> None:
    """Under Hungarian the stage, phase, labels and buttons are Hungarian."""
    activate("hu")
    page = _opened(area, "transcribed_audio_pending").page
    header = page.header
    assert header is not None
    assert (header.stage_text, header.status) == ("Letöltés", "Átírva")
    assert header.labels[0] == "A felhőtakarítás megszakadt; ismételje meg"
    assert _label(area, CLEAN_AUDIO) == "Takarítás megismétlése"


def test_the_step_header_follows_the_stage() -> None:
    """Steps before the job's stage are done, its stage is current; `done` is past every step."""
    states = [step.state for step in step_views("transcription")]
    assert states == [DONE, DONE, "current", "upcoming"]
    assert [step.state for step in step_views(DONE)] == [DONE, DONE, DONE, DONE]
    first = step_views(None)
    assert (first[0].state, first[0].shown, first[0].title) == ("current", True, "Files")
    back = step_views("download", "review_upload")
    assert [step.shown for step in back] == [False, True, False, False]
    assert [step.number for step in back] == [1, 2, 3, 4]


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_jobs_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the jobs pages built gives the same response from the backend and the CLI."""
    documents = json.loads(golden.read_text(encoding="utf-8"))
    templates = [json.dumps(document) for document in documents]
    exchanges = run_parity(tmp_path, templates, ports=scenario_ports)
    statuses = {json.loads(exchange.response)["status"] for exchange in exchanges}
    assert statuses <= {"ok", "needs_confirmation"}
