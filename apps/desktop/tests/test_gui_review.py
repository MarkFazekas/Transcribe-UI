"""The review page's view-model over a batch in review (plan m6 M6.8).

Driven like a user on `SyncExecutor` (or a deferred executor where the order of answers
matters): open the review, filter it, choose per transcription, apply, remove a file. The
embedded-subtitle view offers extraction of text streams only; the track view a track per
file and language. The requests a scripted state builds are request goldens (`gui_review_*`),
which the parity runner also sends through the backend and the CLI. Late answers (another job,
another filter, a hidden page) never change what the page shows.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from support.batch_backend import ids_of
from support.batches import EPISODE, FILM, FIRST_BATCH_ID, SILENT, batch_ports, build_batch_tree
from support.golden import GOLDEN_DIR, assert_golden
from support.parity import Leg, run_parity
from support.review import SETUP, ReviewArea, review_area, review_leg
from support.selection import DeferredCall, DeferredExecutor, documents
from transcribe_ui.api.classes import Request
from transcribe_ui.batches.classes import (
    ExcludeItemsRequest,
    ExtractChoice,
    ResolveReviewRequest,
    ReviewBatchRequest,
    TranscribeChoice,
)
from transcribe_ui.gui.review.constants import ReviewTab
from transcribe_ui.gui.review.display import ReviewRow, review_rows
from transcribe_ui.gui.review.status import Buttons, buttons, review_status
from transcribe_ui.i18n.catalog import activate

SUBPROCESS_TIMEOUT_S = 60
EN: Final = "en"
HU: Final = "hu"
CHANGED = "batches_changed"
# The tables of the review.
SUBTITLES: Final[ReviewTab] = "subtitles"
TRACKS: Final[ReviewTab] = "tracks"
ATTENTION: Final[ReviewTab] = "attention"
# The requests of the review page.
GET: Final = "get_batch"
REVIEW: Final = "review_batch"
RESOLVE: Final = "resolve_review"
# The requests of a load: the job, its review, the job again.
LOAD = (GET, REVIEW, GET)
# The batch id of a job the database does not hold.
OTHER_BATCH_ID = "batch_20261127_130001"
# The film in both languages, as the track view lists it.
FILM_ROWS = ((FILM, EN), (FILM, HU))
# The options of each transcription of the film: its two tracks and its English text subtitle (the
# Hungarian picture subtitle can only be transcribed, so it is no option).
FILM_OPTIONS = (
    "Transcribe track 1: hun, aac, 2 channels",
    "Transcribe track 2: eng, opus, 6 channels, default, “Original”",
    "Extract subtitle 1: eng, subrip",
)
# The film's English text subtitle, among its options.
EXTRACT_ENGLISH = 2
# The film's Hungarian track, among its options.
HUNGARIAN_TRACK = 0
# The Qt-free modules of the review page.
QT_FREE_MODULES = (
    "transcribe_ui.gui.review.commands",
    "transcribe_ui.gui.review.constants",
    "transcribe_ui.gui.review.display",
    "transcribe_ui.gui.review.draft",
    "transcribe_ui.gui.review.editing",
    "transcribe_ui.gui.review.folders",
    "transcribe_ui.gui.review.loading",
    "transcribe_ui.gui.review.options",
    "transcribe_ui.gui.review.sender",
    "transcribe_ui.gui.review.status",
    "transcribe_ui.gui.review.uploads",
    "transcribe_ui.gui.review.viewmodel",
)


@pytest.fixture
def leg(tmp_path: Path) -> Iterator[Leg]:
    """Return a leg whose first batch is in review."""
    with review_leg(tmp_path / "leg") as created:
        yield created


@pytest.fixture
def area(leg: Leg) -> ReviewArea:
    """Return the review view-model over the leg, its first batch opened."""
    created = review_area(leg)
    created.model.open_batch(FIRST_BATCH_ID)
    return created


def _rows(area: ReviewArea, tab: ReviewTab) -> tuple[tuple[str, str | None], ...]:
    return tuple((row.shape.file_name, row.shape.language) for row in review_rows(area.model, tab))


def _row(area: ReviewArea, tab: ReviewTab, name: str, language: str) -> ReviewRow:
    rows = review_rows(area.model, tab)
    return next(row for row in rows if (row.shape.file_name, row.shape.language) == (name, language))


def _choose(area: ReviewArea, name: str, language: str, option: int) -> None:
    """Choose the option at `option` of a transcription, as its combo box does."""
    shape = _row(area, SUBTITLES, name, language).shape
    area.model.editor.choose(shape.options[option].choice)


def _extract_english(area: ReviewArea) -> None:
    """Choose extracting the film's English text subtitle for its English transcription."""
    _choose(area, FILM, EN, EXTRACT_ENGLISH)


def _buttons(area: ReviewArea) -> Buttons:
    return buttons(area.model)


def _sent(area: ReviewArea, action: str) -> list[Request]:
    return [request for request in area.runner.requests if request.action == action]


def _resolved(area: ReviewArea) -> ResolveReviewRequest:
    """Return the one `resolve_review` sent."""
    sent = _sent(area, RESOLVE)
    assert len(sent) == 1
    resolved = sent[0]
    assert isinstance(resolved, ResolveReviewRequest)
    return resolved


def _pending(executor: DeferredExecutor, action: str) -> DeferredCall:
    """Return the one call of `action` waiting for its answer."""
    calls = executor.pending(action)
    assert len(calls) == 1
    return calls[0]


def _golden(area: ReviewArea, name: str, actions: tuple[str, ...]) -> None:
    built = [request for request in area.runner.requests if request.action in actions]
    assert_golden(name, documents(built, area.leg.root))


def test_the_view_models_do_not_load_qt() -> None:
    """The review page's view-models import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_opening_shows_both_views(area: ReviewArea) -> None:
    """The job loads, its files are checked, and the views list the subtitles, the tracks and nothing to fix."""
    shown = (_rows(area, SUBTITLES), _rows(area, TRACKS), _rows(area, ATTENTION))
    sent = tuple(request.action for request in area.runner.requests)
    assert (sent, shown) == (LOAD, ((*FILM_ROWS, (SILENT, HU)), FILM_ROWS, ()))
    film = _row(area, SUBTITLES, FILM, EN)
    assert (film.shape.labels, film.chosen, film.shape.folder) == (FILM_OPTIONS, 1, "Movies")
    silent = _row(area, SUBTITLES, SILENT, HU).shape.labels
    assert ("picture-based: transcribe only" in film.shape.details, silent) == (
        True,
        ("Extract subtitle 1: hun, ass, default",),
    )
    state = _buttons(area)
    expected = ("4 files in review; every one is ready to upload.", True, False, "")
    assert (review_status(area.model), state.upload_enabled, state.apply_shown, state.hint) == expected


def test_embedded_subtitle_choice_is_applied(area: ReviewArea) -> None:
    """Extracting the film's English subtitle waits for Apply, which sends it and leaves nothing pending."""
    _choose(area, FILM, EN, EXTRACT_ENGLISH)
    row = _row(area, SUBTITLES, FILM, EN)
    state = buttons(area.model)
    assert (row.chosen, row.status) == (EXTRACT_ENGLISH, "Changed; not applied yet")
    expected = (True, False, "Apply or discard your changes before changing the filters or uploading.")
    assert (state.apply_enabled, state.upload_enabled, state.hint) == expected
    area.model.commands.apply()
    assert [type(choice) for choice in _resolved(area).choices] == [ExtractChoice]
    row = _row(area, SUBTITLES, FILM, EN)
    assert (row.chosen, row.status, bool(area.model.editor.draft.pending())) == (EXTRACT_ENGLISH, "", False)
    assert (CHANGED, ((FIRST_BATCH_ID,),)) in area.outlet.emitted
    _golden(area, "gui_review_subtitles.json", (REVIEW, RESOLVE))


def test_track_choice_is_applied(area: ReviewArea) -> None:
    """The film's English transcription from its Hungarian track, chosen in the track view."""
    _choose(area, FILM, EN, HUNGARIAN_TRACK)
    assert _row(area, SUBTITLES, FILM, EN).chosen == HUNGARIAN_TRACK
    area.model.commands.apply()
    film = ids_of(area.leg)[FILM]
    chosen = TranscribeChoice(kind="transcribe", item_id=film, language=EN, track_index=0)
    assert (_resolved(area).choices, _row(area, TRACKS, FILM, EN).chosen) == ((chosen,), HUNGARIAN_TRACK)
    _golden(area, "gui_review_tracks.json", (REVIEW, RESOLVE))


def test_the_stored_choice_again_is_no_change(area: ReviewArea) -> None:
    """A choice set back to the stored one leaves nothing to apply; Discard drops the others."""
    editor = area.model.editor
    _extract_english(area)
    _choose(area, FILM, EN, 1)
    assert not editor.draft.pending()
    _choose(area, FILM, HU, 1)
    editor.discard()
    assert (bool(editor.draft.pending()), _row(area, TRACKS, FILM, HU).chosen) == (False, 0)


def test_folder_filter_and_extract_all_shown(area: ReviewArea, leg: Leg) -> None:
    """In one folder and one language, "Extract all shown" extracts where a subtitle of the language exists."""
    movies = str(leg.root / "media" / "Movies")
    area.model.filter_by((EN,), movies)
    filtered = _sent(area, REVIEW)[-1]
    assert isinstance(filtered, ReviewBatchRequest)
    assert (filtered.filters.languages, filtered.filters.folder, _rows(area, SUBTITLES)) == (
        (EN,),
        movies,
        ((FILM, EN),),
    )
    area.model.editor.extract_shown()
    assert [type(choice) for choice in area.model.editor.draft.pending()] == [ExtractChoice]
    area.model.commands.apply()
    # `resolve_review` answers unfiltered views: the filtered ones are loaded again.
    reloaded = tuple(request.action for request in area.runner.requests[-4:])
    shown = (reloaded, _rows(area, SUBTITLES), _row(area, SUBTITLES, FILM, EN).chosen)
    assert shown == ((RESOLVE, *LOAD), ((FILM, EN),), EXTRACT_ENGLISH)
    _golden(area, "gui_review_folder_filter.json", (REVIEW, RESOLVE))


def test_transcribe_all_shown_takes_the_suggestion(area: ReviewArea) -> None:
    """After an extraction, "Transcribe all shown" goes back to the pre-selected track; a file without audio keeps extracting."""
    _extract_english(area)
    area.model.commands.apply()
    area.model.editor.transcribe_shown()
    film = ids_of(area.leg)[FILM]
    chosen = TranscribeChoice(kind="transcribe", item_id=film, language=EN, track_index=1)
    assert area.model.editor.draft.pending() == (chosen,)


def test_remove_goes_through_the_confirmation(area: ReviewArea) -> None:
    """Remove asks the backend's question; confirmed, the file leaves the job and the views."""
    area.model.editor.tick(ids_of(area.leg)[SILENT], ticked=True)
    assert (_buttons(area).remove_enabled, _row(area, SUBTITLES, SILENT, HU).ticked) == (True, True)
    area.model.commands.remove_ticked()
    excluded = [request for request in _sent(area, "exclude_items") if isinstance(request, ExcludeItemsRequest)]
    assert ([request.confirm for request in excluded], len(area.outlet.asked)) == ([False, True], 1)
    assert (_rows(area, SUBTITLES), area.model.editor.ticked()) == (FILM_ROWS, ())
    assert SILENT not in ids_of(area.leg)


def test_a_file_that_needs_attention_is_listed(leg: Leg) -> None:
    """A file gone before the check is listed with its problem; the upload waits until it is removed."""
    (leg.root / "media" / "Audio" / EPISODE).unlink()
    area = review_area(leg)
    area.model.open_batch(FIRST_BATCH_ID)
    rows = review_rows(area.model, ATTENTION)
    assert [(row.shape.file_name, row.shape.labels, row.status) for row in rows] == [
        (EPISODE, (), "The file is gone or cannot be read; restore it, or remove it from the job."),
    ]
    state = buttons(area.model)
    assert (state.upload_shown, state.upload_enabled) == (True, False)
    assert state.hint.startswith("Choose for the files that need attention")
    assert review_status(area.model) == "4 files in review; 1 needs attention."


def _held(area: ReviewArea) -> DeferredExecutor:
    """Return the deferred executor of a view-model from `_deferred`."""
    executor = area.executor
    assert isinstance(executor, DeferredExecutor)
    return executor


def _deferred(leg: Leg) -> ReviewArea:
    """Return the view-model on a deferred executor, the job opened and loaded: its review waits for an answer."""
    executor = DeferredExecutor(leg.backend)
    area = review_area(leg, lambda _runner: executor)
    area.model.open_batch(FIRST_BATCH_ID)
    executor.answer(_pending(executor, GET))
    return area


def test_a_late_review_after_a_filter_is_dropped(leg: Leg) -> None:
    """A filter change cancels the review in flight; only the new one, sent after it answered, is shown."""
    area = _deferred(leg)
    executor = _held(area)
    first = _pending(executor, REVIEW)
    area.model.filter_by((HU,), None)
    assert (first.cancelled, executor.pending(REVIEW)) == (True, [first])
    executor.answer(first)
    second = _pending(executor, REVIEW)
    assert area.model.loader.loaded.state == "checking"
    executor.answer_all()
    assert isinstance(second.request, ReviewBatchRequest)
    loaded = area.model.loader.loaded
    assert (second.request.filters.languages, loaded.state, _rows(area, TRACKS)) == ((HU,), "shown", ((FILM, HU),))


def test_answers_about_another_job_are_dropped(leg: Leg) -> None:
    """Reopening another job drops the first job's answers still on their way, also an Apply's."""
    executor = DeferredExecutor(leg.backend)
    area = review_area(leg, lambda _runner: executor)
    area.model.open_batch(FIRST_BATCH_ID)
    executor.answer_all()
    _extract_english(area)
    area.model.commands.apply()
    area.model.open_batch(OTHER_BATCH_ID)
    executor.answer_all()
    loaded = area.model.loader.loaded
    assert (loaded.review, loaded.detail_error is not None) == (None, True)
    assert (CHANGED, ((FIRST_BATCH_ID,),)) in area.outlet.emitted


def test_actions_wait_for_the_check(leg: Leg) -> None:
    """Apply is disabled while the files are checked, and an Apply sent then waits for the check to answer."""
    area = _deferred(leg)
    executor = _held(area)
    executor.answer_all()
    _extract_english(area)
    area.model.loader.load_review()
    state = _buttons(area)
    assert (state.apply_enabled, state.hint) == (False, "Wait until the files are checked.")
    area.model.commands.apply()
    assert not executor.pending(RESOLVE)
    executor.answer(_pending(executor, REVIEW))
    assert len(executor.pending(RESOLVE)) == 1
    executor.answer_all()
    assert not area.model.editor.draft.pending()


def test_stop_checking_and_check_again(leg: Leg) -> None:
    """Stop drops the check in flight (shown as stopped, not failed); Check again continues."""
    area = _deferred(leg)
    executor = _held(area)
    area.model.loader.stop()
    assert review_status(area.model) == "Checking stopped; continue any time with Check again."
    executor.answer_all()
    loaded = area.model.loader.loaded
    assert (loaded.state, loaded.review) == ("stopped", None)
    area.model.loader.load_review()
    executor.answer_all()
    assert (area.model.loader.loaded.state, _rows(area, TRACKS)) == ("shown", FILM_ROWS)


def test_a_hidden_page_sends_nothing(area: ReviewArea) -> None:
    """While hidden, a change of the job only marks the page; shown again, it loads once."""
    model = area.model
    model.visibility_changed(shown=False)
    sent = len(area.runner.requests)
    model.on_batches_changed((FIRST_BATCH_ID,))
    model.on_batches_changed(())
    assert len(area.runner.requests) == sent
    model.visibility_changed(shown=True)
    assert tuple(request.action for request in area.runner.requests[sent:]) == LOAD
    model.on_batches_changed((OTHER_BATCH_ID,))
    assert len(area.runner.requests) == sent + len(LOAD)


def test_choices_wait_while_a_filter_change_is_checked(leg: Leg) -> None:
    """While a filter change is checked, the shown view is old: choices, "Extract all shown" and Apply do nothing."""
    area = _deferred(leg)
    executor = _held(area)
    executor.answer_all()
    area.model.filter_by((), str(leg.root / "media" / "Movies"))
    area.model.editor.extract_shown()
    _extract_english(area)
    area.model.commands.apply()
    assert (area.model.editor.draft.pending(), buttons(area.model).choose_enabled) == ((), False)
    executor.answer_all()
    assert not executor.pending(RESOLVE)
    area.model.editor.extract_shown()
    assert [type(choice) for choice in area.model.editor.draft.pending()] == [ExtractChoice]


def test_choices_wait_for_the_upload(leg: Leg) -> None:
    """During the job's upload nothing can be chosen and the review waits; once it ended, the review runs."""
    area = _deferred(leg)
    executor = _held(area)
    executor.answer_all()
    model = area.model
    model.upload()
    assert _pending(executor, "run_upload").request.action == "run_upload"
    _extract_english(area)
    model.loader.load_review()
    assert (model.editor.draft.pending(), model.loader.loaded.state, executor.pending(REVIEW)) == (
        (),
        "waiting_upload",
        [],
    )
    executor.answer_all()
    # The end of the upload emits `batches_changed` (here it failed without credentials); the bus brings it back.
    assert (CHANGED, ((FIRST_BATCH_ID,),)) in area.outlet.emitted
    model.on_batches_changed((FIRST_BATCH_ID,))
    executor.answer_all()
    assert (model.loader.loaded.state, model.editable(), len(_rows(area, TRACKS))) == ("shown", True, 2)


def test_hiding_during_a_review_reloads_once(leg: Leg) -> None:
    """A review running when the page hides is dropped; shown again, the page loads exactly once."""
    area = _deferred(leg)
    executor = _held(area)
    running = _pending(executor, REVIEW)
    area.model.visibility_changed(shown=False)
    executor.answer_all()
    assert (running.cancelled, area.model.loader.page.stale, executor.pending(GET)) == (True, True, [])
    sent = len(executor.calls)
    area.model.visibility_changed(shown=True)
    executor.answer_all()
    assert tuple(call.request.action for call in executor.calls[sent:]) == LOAD


def test_a_removal_answered_while_hidden_waits(leg: Leg) -> None:
    """Remove confirmed, then Back before the answer: no review is sent until the page shows, then one load."""
    area = _deferred(leg)
    executor = _held(area)
    executor.answer_all()
    area.model.editor.tick(ids_of(area.leg)[SILENT], ticked=True)
    area.model.commands.remove_ticked()
    executor.answer(_pending(executor, "exclude_items"))
    confirmed = _pending(executor, "exclude_items")
    area.model.visibility_changed(shown=False)
    sent = len(executor.calls)
    executor.answer(confirmed)
    assert (len(executor.calls), area.model.loader.page.stale) == (sent, True)
    area.model.visibility_changed(shown=True)
    executor.answer_all()
    assert tuple(call.request.action for call in executor.calls[sent:]) == LOAD
    assert _rows(area, SUBTITLES) == FILM_ROWS


def test_choices_no_longer_listed_are_dropped(area: ReviewArea) -> None:
    """A choice of a file removed elsewhere is dropped by the next review, so Apply never sends it."""
    _extract_english(area)
    area.model.filter_by((HU,), None)
    last = _sent(area, REVIEW)[-1]
    assert isinstance(last, ReviewBatchRequest)
    assert (_buttons(area).filters_enabled, last.filters.languages) == (False, ())
    film = ids_of(area.leg)[FILM]
    excluded = ExcludeItemsRequest(action="exclude_items", batch_id=FIRST_BATCH_ID, item_ids=(film,), confirm=True)
    assert area.leg.backend.execute(excluded).status == "ok"
    area.model.on_batches_changed((FIRST_BATCH_ID,))
    assert (area.model.editor.draft.pending(), buttons(area.model).apply_shown) == ((), False)


def test_choices_go_with_the_review(area: ReviewArea) -> None:
    """When the job no longer allows a review, its views go and so do the choices: nothing stale stays to apply."""
    _choose(area, FILM, EN, EXTRACT_ENGLISH)
    loaded = area.model.loader.loaded
    detail = loaded.detail
    assert detail is not None
    summary = detail.summary.model_copy(update={"actions": ()})
    loaded.detail = detail.model_copy(update={"summary": summary})
    area.model.loader.load_review()
    state = buttons(area.model)
    assert (area.model.editor.draft.pending(), state.apply_shown, state.filters_enabled) == ((), False, True)


def test_the_texts_follow_the_language(leg: Leg) -> None:
    """The options and the status are shown in the language of the texts (chosen at start-up)."""
    activate(HU)
    area = review_area(leg)
    area.model.open_batch(FIRST_BATCH_ID)
    assert _row(area, SUBTITLES, SILENT, HU).shape.labels == ("1. felirat kinyerése: hun, ass, alapértelmezett",)
    assert review_status(area.model) == "4 fájl ellenőrzés alatt; mind feltölthető."


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_review_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the review page built gives the same response from the backend and the CLI."""
    built = [json.dumps(document) for document in json.loads(golden.read_text(encoding="utf-8"))]
    exchanges = run_parity(tmp_path, [*SETUP, *built], prepare=build_batch_tree, ports=batch_ports)
    assert {json.loads(exchange.response)["status"] for exchange in exchanges} == {"ok"}
