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

from support.discovery import PODCAST
from support.selection import (
    DeferredExecutor,
    SelectionArea,
    media_path,
    media_ports,
    scanned_page,
    selection_area,
)
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
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
