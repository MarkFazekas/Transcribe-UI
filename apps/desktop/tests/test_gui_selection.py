"""The New job page's view-model over the media tree (plan m6 M6.4).

Driven like a user on `SyncExecutor` (or a deferred executor where the order of answers
matters): choose a folder, set filters, tick folders and files, pick languages, create the job,
reopen it. The tick boxes, folder states and totals must be the backend's preview; the requests
a scripted state builds are request goldens (`gui_selection_*`), which the parity runner also
sends through the backend and the CLI.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from support.actions import scenario_batch_id, seeded_root
from support.discovery import EPISODE_ONE, FILM, FILM_SEQUEL, INTERVIEW, PODCAST, build_media_tree, scan_direct
from support.golden import GOLDEN_DIR, assert_golden
from support.jobs import scenario_ports
from support.parity import run_parity
from support.selection import (
    DeferredExecutor,
    ScannedPage,
    SelectionArea,
    documents,
    media_path,
    media_ports,
    scanned_page,
    selection_area,
)
from support.trees import build_tree
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.batches.classes import CreateBatchRequest
from transcribe_ui.discovery.classes import PreviewSelectionRequest, PreviewSelectionResponse
from transcribe_ui.gui.selection.display import save_hint, size_text, subtitles_text, totals_text
from transcribe_ui.gui.selection.filters import FilterInput
from transcribe_ui.gui.selection.marks import Marks
from transcribe_ui.gui.selection.rows import row_dimmed, row_mark
from transcribe_ui.gui.selection.tree import ScanIndex
from transcribe_ui.gui.selection.viewmodel import NewJobModel
from transcribe_ui.i18n.catalog import activate

SUBPROCESS_TIMEOUT_S = 60
ENGLISH = "en"
HUNGARIAN: Final = "hu"
ENGLISH_ONLY = (ENGLISH,)
CHECKED = "checked"
PARTIAL = "partial"
UNCHECKED = "unchecked"
PREVIEW = "preview_selection"
BATCH_ID = "batch_20261127_120000"
TICK_FIRST = "Tick the files to transcribe."
# Folders of the media tree.
AUDIO = "Audio"
MOVIES = "Movies"
SERIES = "Series"
SEASON = (SERIES, "S01")
# Columns of the tree.
NAME_COLUMN = 0
SUBTITLES_COLUMN = 1
TARGETS_COLUMN = 4
# Sizes in bytes of the size texts.
SMALL_SIZE = 999
LARGER_SIZE = 1_234_567
# A tree with a nested folder that has a sibling after it.
NESTED_TREE = (
    "media/Alpha/a.mkv",
    "media/Series/s.mkv",
    "media/Series/S01/e1.mkv",
    "media/Series/S01/Extras/x.webm",
    "media/Zeta/z.mkv",
)
# The Qt-free modules of the New job page.
QT_FREE_MODULES = (
    "transcribe_ui.gui.selection.changes",
    "transcribe_ui.gui.selection.choices",
    "transcribe_ui.gui.selection.constants",
    "transcribe_ui.gui.selection.display",
    "transcribe_ui.gui.selection.editing",
    "transcribe_ui.gui.selection.filters",
    "transcribe_ui.gui.selection.marks",
    "transcribe_ui.gui.selection.rows",
    "transcribe_ui.gui.selection.saving",
    "transcribe_ui.gui.selection.scanning",
    "transcribe_ui.gui.selection.targets",
    "transcribe_ui.gui.selection.ticks",
    "transcribe_ui.gui.selection.tree",
    "transcribe_ui.gui.selection.viewmodel",
)


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
def page(area: SelectionArea, tmp_path: Path) -> ScannedPage:
    """Return the view-model with the media tree scanned."""
    return scanned_page(area, tmp_path)


def _last_preview(page: ScannedPage) -> PreviewSelectionRequest:
    request = page.area.sent(PREVIEW)[-1]
    assert isinstance(request, PreviewSelectionRequest)
    return request


def _creates(page: ScannedPage) -> list[CreateBatchRequest]:
    return [request for request in page.area.runner.requests if isinstance(request, CreateBatchRequest)]


def _golden(name: str, page: ScannedPage) -> None:
    assert_golden(name, documents([_last_preview(page)], page.root))


def _problem(model: NewJobModel) -> str | None:
    return model.filters.problem


def _operations(model: NewJobModel) -> list[tuple[str, str]]:
    return [(operation.operation, operation.path) for operation in model.ticks.operations]


def _deferred(ports: Ports) -> tuple[DeferredExecutor, NewJobModel]:
    executor = DeferredExecutor(Backend(ports))
    return executor, NewJobModel(executor, selection_area(Backend(ports)).outlet)


def test_the_view_models_do_not_load_qt() -> None:
    """The New job page's view-model and its parts import no Qt."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_the_scan_fills_the_tree(page: ScannedPage) -> None:
    """The scan takes the settings' options; the tree has the root, its folders first, then its files."""
    index = page.index
    root_rows = [index.path(index.child(0, row)) for row in range(index.child_count(0))]
    assert (page.model.scan.status, index.child_count(None)) == ("ready", 1)
    assert root_rows == [page.path(AUDIO), page.path(MOVIES), page.path(SERIES)]
    assert page.cell(NAME_COLUMN) == page.path()
    episode = page.node(*SEASON, EPISODE_ONE)
    folder = index.parent(episode)
    assert (index.row(episode), folder) == (1, page.node(*SEASON))
    assert index.child(page.node(*SEASON), 1) == episode
    assert_golden("gui_selection_scan.json", documents(page.area.runner.requests, page.root))


def test_each_row_shows_the_subtitles_it_has(page: ScannedPage) -> None:
    """A file row lists its sidecars' languages, the forced ones marked and the unknown ones counted."""
    shown = (
        page.cell(SUBTITLES_COLUMN, MOVIES, FILM),
        page.cell(SUBTITLES_COLUMN, MOVIES, FILM_SEQUEL),
        page.cell(SUBTITLES_COLUMN, AUDIO, PODCAST),
    )
    assert shown == ("en, hu, hu (forced)", "pt, unknown language", "")
    activate(HUNGARIAN)
    film = page.index.media_file(page.node(MOVIES, FILM))
    assert film is not None
    assert subtitles_text(film) == "en, hu, hu (kényszerített)"


def test_a_folder_ticked_with_an_active_filter(page: ScannedPage) -> None:
    """A folder ticked while "missing English" is on selects only the files without an English subtitle."""
    page.model.change_filters(FilterInput(subtitle_languages=ENGLISH_ONLY, subtitle_rule="missing_one"))
    page.tick(MOVIES)
    marks = (page.mark(MOVIES), page.mark(MOVIES, FILM), page.mark(MOVIES, FILM_SEQUEL))
    assert marks == (PARTIAL, UNCHECKED, CHECKED)
    _golden("gui_selection_filtered_folder.json", page)


def test_a_file_excluded_inside_an_included_folder(page: ScannedPage) -> None:
    """Ticking a ticked file inside a ticked folder excludes it; the folder becomes partial."""
    page.tick(SERIES)
    assert page.mark(SERIES) == CHECKED
    page.tick(*SEASON, EPISODE_ONE, folder=False)
    episode = page.path(*SEASON, EPISODE_ONE)
    assert _operations(page.model) == [("include", page.path(SERIES)), ("exclude", episode)]
    assert (page.mark(SERIES), page.mark(*SEASON, EPISODE_ONE)) == (PARTIAL, UNCHECKED)
    _golden("gui_selection_excluded_file.json", page)


def test_a_filter_change_between_clicks(page: ScannedPage) -> None:
    """Each operation keeps the filters active when it was made; a file tick carries none."""
    model = page.model
    model.change_filters(FilterInput(media_kind="audio"))
    page.tick()
    model.change_filters(FilterInput(name_match="starts_with", name_text="film"))
    page.tick(MOVIES)
    page.tick(*SEASON, EPISODE_ONE, folder=False)
    filters = [operation.filters for operation in model.ticks.operations]
    kinds = [chosen.media_kind for chosen in filters]
    assert (kinds, [chosen.name is None for chosen in filters]) == (["audio", "any", "any"], [True, False, True])
    # The two audio files, the three films and the episode.
    assert totals_text(model.ticks.counts) == "6 files selected (4 video, 2 audio), 0 B."
    _golden("gui_selection_filter_change.json", page)


def test_the_tick_boxes_are_the_preview(page: ScannedPage, ports: Ports) -> None:
    """Every row's tick box equals what the backend's preview of the same operations says."""
    model = page.model
    model.change_filters(FilterInput(media_kind="video"))
    page.tick()
    page.tick(MOVIES, FILM, folder=False)
    model.change_filters(FilterInput())
    page.tick(AUDIO, INTERVIEW, folder=False)
    answer = Backend(ports).execute(_last_preview(page))
    assert isinstance(answer, PreviewSelectionResponse)
    expected = Marks.of(answer)
    index = page.index
    for node in range(index.folder_count + len(index.tree.files)):
        path = index.path(node)
        assert row_mark(model, index, node) == expected.mark(path, folder=index.is_folder(node)), path
    assert totals_text(model.ticks.counts) == "7 files selected (6 video, 1 audio), 0 B."


def test_the_filters_dim_what_they_do_not_match(page: ScannedPage) -> None:
    """With a filter on, files it does not match and folders without a match are dimmed; off, nothing is."""
    model = page.model
    model.change_filters(FilterInput(subtitle_languages=(HUNGARIAN,), subtitle_rule="all"))
    entries = ((MOVIES, FILM), (AUDIO, PODCAST), (AUDIO,), (SERIES,))
    dimmed = [row_dimmed(model, page.index, page.node(*parts)) for parts in entries]
    assert dimmed == [False, True, True, False]
    model.change_filters(FilterInput())
    assert not row_dimmed(model, page.index, page.node(AUDIO, PODCAST))


def test_a_bad_regular_expression_stops_folder_ticks(page: ScannedPage) -> None:
    """A name filter that is not a valid regular expression is shown; folder ticks wait, file ticks go on."""
    model = page.model
    model.change_filters(FilterInput(name_match="regex", name_text="(film"))
    assert _problem(model) == "The name filter is not a valid regular expression (at most 200 characters)."
    page.tick(MOVIES)
    assert not model.ticks.operations
    page.tick(AUDIO, PODCAST, folder=False)
    model.change_filters(FilterInput(name_match="regex", name_text="^film"))
    assert (len(model.ticks.operations), _problem(model)) == (1, None)


def test_preview_latest_wins_under_fast_clicking(ports: Ports, tmp_path: Path) -> None:
    """Fast clicks cancel the previews in flight; a stale answer never replaces the latest one's."""
    executor, model = _deferred(ports)
    model.choose_folder(media_path(tmp_path))
    executor.answer_all()
    movies = media_path(tmp_path, MOVIES)
    audio = media_path(tmp_path, AUDIO)
    model.tick(movies, folder=True)
    assert model.ticks.mark(movies, folder=True) == CHECKED, "the clicked row shows the click until the answer"
    model.tick(movies, folder=True)
    model.tick(audio, folder=True)
    first, second, latest = executor.pending(PREVIEW)
    assert (first.cancelled, second.cancelled, latest.cancelled) == (True, True, False)
    assert _operations(model) == [("include", movies), ("exclude", movies), ("include", audio)]
    executor.answer(latest)
    shown = (model.ticks.mark(movies, folder=True), model.ticks.mark(audio, folder=True))
    executor.answer(first)
    executor.answer(second)
    later = (model.ticks.mark(movies, folder=True), model.ticks.mark(audio, folder=True))
    assert later == shown == (UNCHECKED, CHECKED)
    assert totals_text(model.ticks.counts) == "2 files selected (0 video, 2 audio), 0 B."


def test_create_without_a_confirmation(page: ScannedPage) -> None:
    """A selection without subtitles in the target language is created at once; the job page opens."""
    model = page.model
    assert save_hint(model) == TICK_FIRST
    page.tick(AUDIO, PODCAST, folder=False)
    assert save_hint(model) == "Choose at least one language to transcribe into."
    model.targets.use_languages(ENGLISH_ONLY)
    model.save()
    outlet = page.area.outlet
    created = _creates(page)
    assert (len(created), outlet.asked) == (1, [])
    assert outlet.emitted == [("batches_changed", ((BATCH_ID,),)), ("open_job", (BATCH_ID,))]
    assert (model.ticks.operations, model.targets.languages) == ((), ENGLISH_ONLY), "a new job keeps the languages"
    assert_golden("gui_selection_create.json", documents(created, page.root))


def test_create_through_the_existing_languages(page: ScannedPage) -> None:
    """A file that already has an English subtitle makes the backend ask; Cancel creates nothing, Confirm creates."""
    model = page.model
    outlet = page.area.outlet
    page.tick(AUDIO)
    model.targets.use_languages(ENGLISH_ONLY)
    outlet.accept = False
    model.save()
    asked = [confirmation.message for view in outlet.asked for confirmation in view.confirmations]
    assert (asked, outlet.emitted) == (["1 selected file already has a subtitle in a target language."], [])
    outlet.accept = True
    model.save()
    first, confirmed = _creates(page)[1:]
    assert (first.confirm_existing_languages, confirmed.confirm_existing_languages) == (False, True)
    assert outlet.emitted[-1] == ("open_job", (BATCH_ID,))
    assert_golden("gui_selection_create_confirmed.json", documents([first, confirmed], page.root))


def test_a_file_gets_its_own_languages(page: ScannedPage) -> None:
    """A selected file's own languages go into `item_languages`; a file no longer selected is left out."""
    targets = page.model.targets
    page.tick(AUDIO)
    targets.use_languages((HUNGARIAN, ENGLISH))
    targets.use_own(page.path(AUDIO, INTERVIEW), (HUNGARIAN,))
    targets.use_own(page.path(AUDIO, PODCAST), ENGLISH_ONLY)
    shown = (page.cell(TARGETS_COLUMN, AUDIO, INTERVIEW), page.cell(TARGETS_COLUMN, AUDIO, PODCAST))
    assert shown == ("hu (this file)", "en (this file)")
    page.tick(AUDIO, PODCAST, folder=False)
    request = page.model.save_request()
    assert request is not None
    entries = [(entry.path, entry.languages) for entry in request.item_languages]
    assert entries == [(page.path(AUDIO, INTERVIEW), (HUNGARIAN,))]
    targets.use_own(page.path(AUDIO, INTERVIEW), None)
    request = page.model.save_request()
    assert (request is not None and request.item_languages, page.cell(TARGETS_COLUMN, AUDIO, INTERVIEW)) == (
        (),
        "hu, en",
    )


def _created_for_edit(page: ScannedPage) -> CreateBatchRequest:
    """Create a job of the videos in Movies, in Hungarian, the film in English; then show another folder."""
    model = page.model
    model.change_filters(FilterInput(media_kind="video"))
    page.tick(MOVIES)
    model.targets.use_languages((HUNGARIAN,))
    model.targets.use_own(page.path(MOVIES, FILM), ENGLISH_ONLY)
    model.save()
    model.choose_folder(page.path(AUDIO))
    return _creates(page)[-1]


def test_edit_reopens_the_stored_composition(page: ScannedPage) -> None:
    """Edit selection brings back the job's scan, operations and languages; Save sends `edit_batch`."""
    model = page.model
    created = _created_for_edit(page)
    model.edit_batch(BATCH_ID)
    editing = model.saver.editing
    assert (editing is not None and editing.batch_id, editing is not None and editing.stage) == (BATCH_ID, "files")
    reopened = (model.scan.spec, model.ticks.operations, model.targets.languages)
    assert reopened == (created.scan, created.operations, (HUNGARIAN,))
    assert (model.targets.own(page.path(MOVIES, FILM)), page.mark(MOVIES)) == (ENGLISH_ONLY, CHECKED)
    page.tick(MOVIES, FILM_SEQUEL, folder=False)
    model.save()
    assert page.area.outlet.emitted[-2:] == [("batches_changed", ((BATCH_ID,),)), ("open_job", (BATCH_ID,))]
    assert model.saver.editing is None
    assert_golden("gui_selection_edit.json", documents([created, *page.area.sent("edit_batch")], page.root))


def test_a_job_without_a_composition_reopens_empty(tmp_path: Path) -> None:
    """A job the app kept no selection of reopens on its scan root, nothing ticked, with its languages."""
    ports = scenario_ports(tmp_path)
    with closing(ports.state):
        root = seeded_root(tmp_path)
        build_tree(root, ["file0.mp3", "file1.mp3"])
        model = selection_area(Backend(ports)).model
        model.edit_batch(scenario_batch_id("composing"))
        spec = model.scan.spec
        assert (spec is not None and spec.root, model.ticks.operations) == (str(root), ())
        assert (model.targets.languages, totals_text(model.ticks.counts)) == (ENGLISH_ONLY, "Nothing is selected.")
        assert save_hint(model) == TICK_FIRST


def test_an_unknown_job_shows_its_error(area: SelectionArea) -> None:
    """Editing a job that is gone opens the error dialog and keeps the page as it was."""
    area.model.edit_batch("batch_20991231_000000")
    assert [view.code for view in area.outlet.errors] == ["batch_not_found"]
    assert area.model.saver.editing is None


def test_a_scan_stopped_or_failed(ports: Ports, tmp_path: Path) -> None:
    """Stop scan ends the scan as stopped (not a failure); a missing folder shows its error."""
    executor, model = _deferred(ports)
    model.choose_folder(media_path(tmp_path))
    assert model.scan.status == "scanning"
    model.scan.cancel()
    executor.answer_all()
    assert (model.scan.status, model.scan.error, model.scan.scanned) == ("stopped", None, None)
    model.choose_folder(str(tmp_path / "missing"))
    executor.answer_all()
    error = model.scan.error
    assert (model.scan.status, error is not None and error.code) == ("failed", "root_not_found")


def test_start_new_drops_the_selection(page: ScannedPage) -> None:
    """A new job keeps the folder and the languages and drops the operations and the edited job."""
    model = page.model
    page.tick(AUDIO)
    model.targets.use_languages(ENGLISH_ONLY)
    model.start_new()
    assert (model.ticks.operations, totals_text(model.ticks.counts)) == ((), "Nothing is selected.")
    assert (model.targets.languages, model.scan.scanned is not None) == (ENGLISH_ONLY, True)


def test_the_texts_follow_the_language(page: ScannedPage) -> None:
    """Sizes and totals are written as the language writes numbers."""
    activate(HUNGARIAN)
    assert (size_text(SMALL_SIZE), size_text(LARGER_SIZE)) == ("999 B", "1,2 MB")
    page.tick(AUDIO)
    assert totals_text(page.model.ticks.counts) == "2 fájl kijelölve (0 videó, 2 hang), 0 B."


@pytest.mark.parametrize("golden", sorted(GOLDEN_DIR.glob("gui_selection_*.json")), ids=lambda path: path.stem)
def test_gui_requests_are_valid_cli_requests(tmp_path: Path, golden: Path) -> None:
    """Each request the New job page built gives the same response from the backend and the CLI."""
    templates = [json.dumps(document) for document in json.loads(golden.read_text(encoding="utf-8"))]
    exchanges = run_parity(tmp_path, templates, prepare=build_media_tree)
    statuses = {json.loads(exchange.response)["status"] for exchange in exchanges}
    assert statuses <= {"ok", "needs_confirmation"}


def test_files_below_a_nested_folder(tmp_path: Path) -> None:
    """A folder's files below it are its own and its subfolders', never a later sibling's."""
    build_tree(tmp_path, NESTED_TREE)
    index = ScanIndex.of(scan_direct(tmp_path / "media"))
    below = {
        name: [
            found.name for found in index.files_below(index.nodes[str(tmp_path.joinpath("media", *name.split("/")))])
        ]
        for name in ("Series", "Series/S01", "Series/S01/Extras", "Zeta")
    }
    assert below == {
        "Series": ["s.mkv", "e1.mkv", "x.webm"],
        "Series/S01": ["e1.mkv", "x.webm"],
        "Series/S01/Extras": ["x.webm"],
        "Zeta": ["z.mkv"],
    }
