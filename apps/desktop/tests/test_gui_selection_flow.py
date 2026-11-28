"""The New job page's view-model when answers come late or fail, and around edit mode (plan m6 M6.4).

A deferred executor answers the calls when the test says so: Create waits for the preview in
flight, a preview of the previous folder never lands on the next one, a job loaded or saved
after the user started a new job no longer changes the page, a partly ticked folder can be
unticked under a filter, and each warning is listed once.
"""

import os
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from support.discovery import FILM, FILM_SEQUEL, PODCAST
from support.selection import (
    DeferredCall,
    DeferredExecutor,
    RetryingOutlet,
    SelectionArea,
    media_path,
    media_ports,
    scanned_page,
    selection_area,
)
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse
from transcribe_ui.api.ports import Ports
from transcribe_ui.gui.selection.display import save_hint, shown_warnings, totals_text
from transcribe_ui.gui.selection.filters import FilterInput
from transcribe_ui.gui.selection.viewmodel import NewJobModel

ENGLISH = "en"
AUDIO = "Audio"
MOVIES = "Movies"
PREVIEW = "preview_selection"
BATCH_ID = "batch_20261127_120000"
UPDATING = "Updating the selection…"
NOTHING = "Nothing is selected."
# The totals of every film of Movies.
MOVIES_SELECTED = "4 files selected (4 video, 0 audio), 0 B."


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return seeded ports with the media tree (closed afterwards)."""
    created = media_ports(tmp_path)
    with closing(created.state):
        yield created


@pytest.fixture
def area(ports: Ports) -> SelectionArea:
    """Return the New job view-model over the seeded backend, its settings loaded."""
    return selection_area(Backend(ports))


@pytest.fixture
def deferred(ports: Ports, area: SelectionArea, tmp_path: Path) -> tuple[DeferredExecutor, NewJobModel]:
    """Return a view-model on a deferred executor (sharing the area's outlet), the media tree scanned."""
    executor = DeferredExecutor(Backend(ports))
    model = NewJobModel(executor, area.outlet)
    model.choose_folder(media_path(tmp_path))
    executor.answer_all()
    return executor, model


def test_a_partly_ticked_folder_clears_under_a_filter(area: SelectionArea, tmp_path: Path) -> None:
    """Tick a folder under a filter, tick it again: nothing is selected, and no operation repeats."""
    page = scanned_page(area, tmp_path)
    page.model.change_filters(FilterInput(subtitle_languages=(ENGLISH,), subtitle_rule="missing_one"))
    page.tick(MOVIES)
    assert page.mark(MOVIES) == "partial"
    page.tick(MOVIES)
    kinds = [operation.operation for operation in page.model.ticks.operations]
    assert (kinds, page.mark(MOVIES), totals_text(page.model.ticks.counts)) == (
        ["include", "exclude"],
        "unchecked",
        NOTHING,
    )


def test_create_waits_for_the_preview(deferred: tuple[DeferredExecutor, NewJobModel], tmp_path: Path) -> None:
    """While a preview is on its way, Create is not offered; once it answered, it is."""
    executor, model = deferred
    model.targets.use_languages((ENGLISH,))
    model.tick(media_path(tmp_path, AUDIO), folder=True)
    assert (model.ticks.busy, save_hint(model), model.save_request()) == (True, UPDATING, None)
    executor.answer_all()
    assert (model.ticks.busy, save_hint(model)) == (False, None)


def test_create_waits_for_a_failed_preview(area: SelectionArea, tmp_path: Path) -> None:
    """A preview that failed keeps Create disabled until it is retried."""
    page = scanned_page(area, tmp_path)
    page.model.targets.use_languages((ENGLISH,))
    page.model.tick(str(tmp_path / "elsewhere"), folder=True)
    error = page.model.ticks.error
    assert (error is not None and error.code, save_hint(page.model)) == (
        "path_outside_root",
        "The selection could not be updated; retry it.",
    )


def test_a_preview_of_the_previous_folder_is_dropped(
    deferred: tuple[DeferredExecutor, NewJobModel],
    tmp_path: Path,
) -> None:
    """A preview still on its way when another folder is chosen never shows its totals there."""
    executor, model = deferred
    model.tick(media_path(tmp_path, AUDIO), folder=True)
    old = executor.pending(PREVIEW)
    model.choose_folder(media_path(tmp_path, MOVIES))
    for call in old:
        executor.answer(call)
    assert (model.ticks.counts, model.ticks.operations, model.ticks.busy) == (None, (), False)
    executor.answer_all()
    assert totals_text(model.ticks.counts) == NOTHING


def _editing(model: NewJobModel) -> str | None:
    editing = model.saver.editing
    return None if editing is None else editing.batch_id


def _create_audio_job(area: SelectionArea, root: Path) -> None:
    """Create a job of the podcast in English through the synchronous view-model."""
    page = scanned_page(area, root)
    page.tick(AUDIO, PODCAST, folder=False)
    page.model.targets.use_languages((ENGLISH,))
    page.model.save()


def test_a_late_job_load_is_dropped(
    deferred: tuple[DeferredExecutor, NewJobModel],
    area: SelectionArea,
    tmp_path: Path,
) -> None:
    """A job still loading when the user starts a new job does not reopen."""
    executor, model = deferred
    _create_audio_job(area, tmp_path)
    model.edit_batch(BATCH_ID)
    model.start_new()
    executor.answer_all()
    assert (model.saver.editing, model.ticks.operations) == (None, ())


def test_a_save_after_leaving_the_edit_only_reloads(
    deferred: tuple[DeferredExecutor, NewJobModel],
    area: SelectionArea,
    tmp_path: Path,
) -> None:
    """The save of an edited job answered after the user left the edit changes the lists, not the page."""
    executor, model = deferred
    _create_audio_job(area, tmp_path)
    area.outlet.emitted.clear()
    model.edit_batch(BATCH_ID)
    executor.answer_all()
    model.targets.use_languages(("hu",))
    model.save()
    model.choose_folder(media_path(tmp_path, MOVIES))
    executor.answer_all()
    assert "edit_batch" in [call.request.action for call in executor.calls]
    assert area.outlet.emitted == [("batches_changed", ((BATCH_ID,),))]
    assert (model.saver.editing, model.ticks.operations) == (None, ())


def test_choose_folder_leaves_the_edit(area: SelectionArea, tmp_path: Path) -> None:
    """Choosing a folder while a job is edited starts a new job there, with an empty selection."""
    _create_audio_job(area, tmp_path)
    model = area.model
    model.edit_batch(BATCH_ID)
    editing = _editing(model)
    model.choose_folder(media_path(tmp_path))
    assert (editing, _editing(model), model.ticks.operations) == (BATCH_ID, None, ())


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux file systems accept names that are not UTF-8")
def test_each_warning_is_listed_once(tmp_path: Path) -> None:
    """The scan's warnings show before the preview answers; after it, its list (scan's first) is shown once."""
    build_tree(tmp_path, ["media/good.mkv"])
    bad = b"/".join((os.fsencode(tmp_path / "media"), b"bad\xff.mkv"))
    Path(os.fsdecode(bad)).touch()
    ports = media_ports(tmp_path / "state")
    with closing(ports.state):
        executor = DeferredExecutor(Backend(ports))
        model = NewJobModel(executor, selection_area(Backend(ports)).outlet)
        model.choose_folder(str(tmp_path / "media"))
        executor.answer(executor.calls[0])
        before = [row[0] for row in shown_warnings(model)]
        executor.answer_all()
        model.tick(str(tmp_path / "media" / "nothing"), folder=True)
        executor.answer_all()
        after = [row[0] for row in shown_warnings(model)]
    unicode = "The name is not valid Unicode; rename it to include it."
    assert (before, after) == ([unicode], [unicode, "No media file is at or below this path."])


def test_a_filtered_click_on_the_root_undoes_itself(area: SelectionArea, tmp_path: Path) -> None:
    """Everything ticked, then "Audio only": a root click drops the audio files, a second brings them back."""
    page = scanned_page(area, tmp_path)
    page.tick()
    page.model.change_filters(FilterInput(media_kind="audio"))
    page.tick()
    dropped = (page.mark(), page.mark(AUDIO))
    page.tick()
    kinds = [operation.operation for operation in page.model.ticks.operations]
    assert (dropped, kinds, page.mark()) == (("partial", "unchecked"), ["include", "exclude", "include"], "checked")


def test_a_filtered_click_adds_the_matching_files(area: SelectionArea, tmp_path: Path) -> None:
    """One film ticked, then "missing English": a Movies click adds the matching films, a second drops them."""
    page = scanned_page(area, tmp_path)
    page.tick(MOVIES, FILM, folder=False)
    page.model.change_filters(FilterInput(subtitle_languages=(ENGLISH,), subtitle_rule="missing_one"))
    page.tick(MOVIES)
    added = (page.mark(MOVIES, FILM_SEQUEL), page.mark(MOVIES, FILM), totals_text(page.model.ticks.counts))
    page.tick(MOVIES)
    assert added == ("checked", "checked", "3 files selected (3 video, 0 audio), 0 B.")
    assert (page.mark(MOVIES, FILM_SEQUEL), page.mark(MOVIES, FILM)) == ("unchecked", "checked")


def _create_then_tick_another_folder(executor: DeferredExecutor, model: NewJobModel, root: Path) -> DeferredCall:
    """Create a job of the podcast, then (before the create answers) scan Movies and tick it; return the create."""
    model.tick(media_path(root, AUDIO, PODCAST), folder=False)
    model.targets.use_languages((ENGLISH,))
    executor.answer_all()
    model.save()
    create = executor.pending("create_batch")[0]
    model.choose_folder(media_path(root, MOVIES))
    executor.answer_others(create)
    model.tick(media_path(root, MOVIES), folder=True)
    executor.answer_others(create)
    return create


def test_a_create_after_another_folder_only_reloads(
    deferred: tuple[DeferredExecutor, NewJobModel],
    area: SelectionArea,
    tmp_path: Path,
) -> None:
    """A job created after the user chose another folder and ticked there changes the lists, not that selection."""
    executor, model = deferred
    create = _create_then_tick_another_folder(executor, model, tmp_path)
    executor.answer(create)
    assert area.outlet.emitted == [("batches_changed", ((BATCH_ID,),))]
    assert (len(model.ticks.operations), totals_text(model.ticks.counts)) == (1, MOVIES_SELECTED)


def test_a_retried_create_keeps_the_new_selection(ports: Ports, tmp_path: Path) -> None:
    """A create refused as busy and retried after the user ticked another folder keeps that selection."""
    executor = DeferredExecutor(Backend(ports))
    outlet = RetryingOutlet()
    model = NewJobModel(executor, outlet)
    model.choose_folder(media_path(tmp_path))
    executor.answer_all()
    create = _create_then_tick_another_folder(executor, model, tmp_path)
    busy = ErrorResponse(action="create_batch", error=ErrorBody(code="app_busy", message="busy"))
    executor.answer_with(create, busy)
    outlet.retries[-1]()
    executor.answer_all()
    assert "create_batch" in [call.request.action for call in executor.calls if call is not create]
    assert outlet.emitted == [("batches_changed", ((BATCH_ID,),))]
    assert (len(model.ticks.operations), totals_text(model.ticks.counts)) == (1, MOVIES_SELECTED)


def test_a_click_waits_for_the_new_filters_matches(
    deferred: tuple[DeferredExecutor, NewJobModel],
    tmp_path: Path,
) -> None:
    """Videos ticked; with "Video only" asked again but its matches not answered, the "Audio only" matches do not decide.

    The old matches would say "not every audio file is selected" and include (no change); the
    tick boxes say the root is partly ticked, so the click excludes the videos.
    """
    executor, model = deferred
    video = FilterInput(media_kind="video")
    model.change_filters(video)
    executor.answer_all()
    model.tick(media_path(tmp_path), folder=True)
    model.change_filters(FilterInput(media_kind="audio"))
    executor.answer_all()
    model.change_filters(video)
    assert model.filters.current_matches() is None
    model.tick(media_path(tmp_path), folder=True)
    executor.answer_all()
    kinds = [operation.operation for operation in model.ticks.operations]
    assert (kinds, totals_text(model.ticks.counts)) == (["include", "exclude"], NOTHING)
