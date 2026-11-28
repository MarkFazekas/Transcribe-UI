"""The review page in the window (pytest-qt, offscreen; plan m6 M6.8).

The window runs over a batch in review through `QtExecutor`: Review on the job page opens the
page on the Jobs page, a combo box's choice waits for Apply (its widget kept in place), Remove
goes through the backend's confirmation dialog, the filters narrow the tables, and a hidden page
sends nothing when its job changes.
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QAbstractItemModelTester
from PySide6.QtWidgets import QComboBox, QPushButton
from pytestqt.qtbot import QtBot

from support.batches import FILM, FIRST_BATCH_ID, SILENT
from support.gated import uploads, wait_for
from support.gui import qt_messages
from support.jobs import RecordingRunner
from support.parity import Leg
from support.review import large_review, review_area, review_leg
from support.selection import HeldUploads
from support.selection_window import ChangedSpans, released, settled_window
from transcribe_ui.batches.responses import BatchDetail, BatchReview
from transcribe_ui.gui.constants import LONG_POOL_THREADS, PAGE_JOBS
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.review import index as index_module
from transcribe_ui.gui.review.constants import CHOICE_COLUMN, STATUS_COLUMN, TICK_COLUMN
from transcribe_ui.gui.review.index import ReviewIndex
from transcribe_ui.gui.review.view import ReviewPage
from transcribe_ui.gui.review.widgets import ReviewTable
from transcribe_ui.gui.shell import MainWindow

EN: Final = "en"
HU: Final = "hu"
# The film's English text subtitle, among its options.
EXTRACT_ENGLISH = 2
# The rows of the embedded-subtitle table: the film in English and Hungarian, the silent video in Hungarian.
SUBTITLE_ROWS = 3
# The silent video's row in the embedded-subtitle table.
SILENT_ROW = 2


@pytest.fixture
def leg(tmp_path: Path) -> Iterator[Leg]:
    """Return a leg whose first batch is in review."""
    with review_leg(tmp_path / "leg") as created:
        yield created


@pytest.fixture
def runner(leg: Leg) -> RecordingRunner:
    """Return a runner that records every request sent to the leg's backend."""
    return RecordingRunner(leg.backend)


@pytest.fixture
def window(qtbot: QtBot, runner: RecordingRunner, tmp_path: Path) -> MainWindow:
    """Return the window over the leg; its calls finish before pytest-qt closes it."""
    return settled_window(qtbot, runner, tmp_path)


def _jobs(window: MainWindow) -> JobsPage:
    page = window.pages.page(PAGE_JOBS)
    assert isinstance(page, JobsPage)
    return page


def _review(window: MainWindow) -> ReviewPage:
    pages = [_jobs(window).widget(index) for index in range(_jobs(window).count())]
    found = [page for page in pages if isinstance(page, ReviewPage)]
    assert len(found) == 1
    return found[0]


def _subtitles(page: ReviewPage) -> ReviewTable:
    return page.tabs.tables["subtitles"]


def _opened(qtbot: QtBot, window: MainWindow) -> ReviewPage:
    """Open the first batch's review and wait for its tables."""
    window.context.signals.open_review.emit(FIRST_BATCH_ID)
    page = _review(window)
    rows = page.tabs.tables["subtitles"].rows
    wait_for(qtbot, lambda: rows.rowCount() == SUBTITLE_ROWS)
    return page


def _text(table: ReviewTable, row: int, column: int) -> object:
    return table.rows.data(table.rows.index(row, column))


def _names(table: ReviewTable) -> list[tuple[str, str | None]]:
    shapes = [table.rows.row(row).shape for row in range(table.rows.rowCount())]
    return [(shape.file_name, shape.language) for shape in shapes]


def _edit(table: ReviewTable, row: int) -> QComboBox:
    """Start editing a row's choice as a click does; return its combo box."""
    index = table.rows.index(row, CHOICE_COLUMN)
    table.clicked.emit(index)
    combo = table.indexWidget(index)
    assert isinstance(combo, QComboBox)
    return combo


def test_review_opens_the_page_on_the_jobs_page(qtbot: QtBot, window: MainWindow) -> None:
    """`open_review` shows the page on the Jobs page at step 2, with the job's files checked."""
    page = _opened(qtbot, window)
    assert window.pages.current_page() == PAGE_JOBS
    assert _jobs(window).currentWidget() is page
    assert _names(_subtitles(page)) == [(FILM, EN), (FILM, HU), (SILENT, HU)]
    assert [step.state for step in page.header.steps.steps] == ["done", "current", "upcoming", "upcoming"]
    wait_for(qtbot, page.action_row.upload_button.isEnabled)
    assert not window.statusBar().currentMessage()


def _choose_in_cell(qtbot: QtBot, page: ReviewPage, option: int) -> None:
    """Pick an option in the first row's combo box, as a click and a choice do; wait until the editor closed."""
    table = _subtitles(page)
    wait_for(qtbot, page.model.editable)
    combo = _edit(table, 0)
    combo.setCurrentIndex(option)
    combo.activated.emit(option)
    wait_for(qtbot, lambda: _text(table, 0, STATUS_COLUMN) == "Changed; not applied yet")
    wait_for(qtbot, lambda: not table.findChildren(QComboBox))


def _defaults(page: ReviewPage) -> list[str]:
    """Return the buttons a stray Enter would press (default or auto-default)."""
    buttons = page.findChildren(QPushButton)
    return [button.text() for button in buttons if button.autoDefault() or button.isDefault()]


def test_a_choice_waits_for_apply(qtbot: QtBot, window: MainWindow) -> None:
    """A choice made in the cell's combo box marks the row; Apply stores it; no button reacts to a stray Enter."""
    page = _opened(qtbot, window)
    table = _subtitles(page)
    _choose_in_cell(qtbot, page, EXTRACT_ENGLISH)
    actions = page.action_row
    wait_for(qtbot, actions.apply_button.isEnabled)
    enabled = (actions.upload_button.isEnabled(), actions.hint.text(), _defaults(page))
    assert enabled == (False, "Apply or discard your changes before changing the filters or uploading.", [])
    actions.apply_button.click()
    wait_for(qtbot, lambda: not actions.apply_button.isVisible())
    wait_for(qtbot, lambda: _text(table, 0, STATUS_COLUMN) == "")
    assert _text(table, 0, CHOICE_COLUMN) == "Extract subtitle 1: eng, subrip"


def test_remove_goes_through_the_confirmation_dialog(qtbot: QtBot, window: MainWindow) -> None:
    """A ticked file is removed after the backend's question is confirmed in the dialog."""
    page = _opened(qtbot, window)
    table = _subtitles(page)
    tick = table.rows.index(SILENT_ROW, TICK_COLUMN)
    table.rows.setData(tick, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    remove = page.action_row.remove_button
    wait_for(qtbot, remove.isEnabled)
    remove.click()
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog))
    dialog = window.dialog
    assert isinstance(dialog, ConfirmationDialog)
    dialog.accept()
    wait_for(qtbot, lambda: _names(table) == [(FILM, EN), (FILM, HU)])
    assert not remove.isEnabled()


def test_the_filters_narrow_the_tables(qtbot: QtBot, window: MainWindow, leg: Leg) -> None:
    """A language tick and a folder narrow the views through the backend."""
    page = _opened(qtbot, window)
    controls = page.controls
    wait_for(qtbot, lambda: HU in controls.languages.boxes)
    controls.languages.boxes[HU].setChecked(True)
    wait_for(qtbot, lambda: _names(page.tabs.tables["tracks"]) == [(FILM, HU)])
    labels = [controls.folder.itemText(index) for index in range(controls.folder.count())]
    assert labels == ["All folders", "Audio", "Movies"]
    movies = labels.index("Movies")
    controls.folder.setCurrentIndex(movies)
    controls.folder.activated.emit(movies)
    wait_for(qtbot, lambda: page.model.loader.filters.folder == str(leg.root / "media" / "Movies"))
    wait_for(qtbot, lambda: _names(_subtitles(page)) == [(FILM, HU), (SILENT, HU)])


def test_a_hidden_page_sends_nothing(qtbot: QtBot, window: MainWindow, runner: RecordingRunner) -> None:
    """Back to the job hides the page: a change of its job sends no review until it is shown again."""
    page = _opened(qtbot, window)
    wait_for(qtbot, lambda: not window.busy())
    page.header.back_button.click()
    jobs = _jobs(window)
    wait_for(qtbot, lambda: jobs.currentWidget() is jobs.page_view)
    wait_for(qtbot, lambda: not window.busy())
    reviews = sum(request.action == "review_batch" for request in runner.requests)
    window.context.signals.batches_changed.emit((FIRST_BATCH_ID,))
    wait_for(qtbot, lambda: not window.busy())
    assert sum(request.action == "review_batch" for request in runner.requests) == reviews
    window.context.signals.open_review.emit(FIRST_BATCH_ID)
    wait_for(qtbot, lambda: sum(request.action == "review_batch" for request in runner.requests) == reviews + 1)


# Files of the large review (a row each in the embedded-subtitle table).
LARGE = 20_000
# Repaints of the page while an upload or a probe reports progress.
PROGRESS_REPAINTS = 50
# Most seconds the rows of the large review may take to build (generous: shared CI runners).
BUILD_LIMIT_S = 10


def _large_page(qtbot: QtBot, leg: Leg) -> ReviewPage:
    """Return the shown review page over a review of `LARGE` files."""
    area = review_area(leg)
    area.model.loader.loaded.review = large_review(LARGE)
    page = ReviewPage(area.model)
    qtbot.addWidget(page)
    page.show()
    return page


def _combos(page: ReviewPage) -> list[QComboBox]:
    return [combo for table in page.tabs.tables.values() for combo in table.findChildren(QComboBox)]


@dataclass
class _Counter:
    calls: int = 0

    def __call__(self, *_arguments: object) -> None:
        self.calls += 1


@dataclass
class _CountedBuild:
    """Counts the builds of the review index, then builds it."""

    build: Callable[[BatchReview, BatchDetail | None], ReviewIndex]
    calls: int = 0

    def __call__(self, review: BatchReview, detail: BatchDetail | None) -> ReviewIndex:
        self.calls += 1
        return self.build(review, detail)


def _progress(page: ReviewPage) -> _Counter:
    """Repaint the page as progress events do; return the counter of the subtitle table's resets."""
    resets = _Counter()
    rows = page.tabs.tables["subtitles"].rows
    rows.modelReset.connect(resets)
    for _ in range(PROGRESS_REPAINTS):
        page.model.changed()
        page.refresh()
    return resets


def test_a_large_review_costs_per_change(qtbot: QtBot, leg: Leg, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tens of thousands of rows: no widget per row, rows built once, progress repaints reset nothing, an edit repaints one row."""
    built = _CountedBuild(index_module.build_index)
    monkeypatch.setattr(index_module, "build_index", built)
    with qt_messages() as collected:
        page = _large_page(qtbot, leg)
        table = _subtitles(page)
        rows = table.rows
        tester = QAbstractItemModelTester(rows, QAbstractItemModelTester.FailureReportingMode.Warning)
        resets = _progress(page)
        spans = ChangedSpans()
        rows.dataChanged.connect(spans)
        page.model.editor.choose(rows.row(LARGE - 1).shape.options[1].choice)
        failures = [message for message in collected.messages if "FAIL" in message]
    assert (rows.rowCount(), built.calls, resets.calls, _combos(page)) == (LARGE, 1, 0, [])
    assert (spans.spans, _text(table, LARGE - 1, STATUS_COLUMN)) == ([(1, 0, 6)], "Changed; not applied yet")
    assert (tester.model() is rows, failures) == (True, [])


def test_a_large_review_is_indexed_fast() -> None:
    """The rows of two tables of tens of thousands of files are built well within a second per ten thousand rows."""
    review = large_review(LARGE)
    started = time.monotonic()
    index = index_module.build_index(review, None)
    elapsed = time.monotonic() - started
    sizes = [len(index.tabs[tab].shapes) for tab in ("subtitles", "tracks")]
    assert (sizes, elapsed < BUILD_LIMIT_S) == ([LARGE, LARGE], True), elapsed


def test_a_queued_review_leaves_the_page_usable(qtbot: QtBot, leg: Leg, tmp_path: Path) -> None:
    """With the long pool full, Check again waits in its queue: the page says so, and choices still work."""
    runner = HeldUploads(leg.backend)
    with released(runner):
        window = settled_window(qtbot, runner, tmp_path)
        page = _opened(qtbot, window)
        wait_for(qtbot, page.model.editable)
        for request in uploads(LONG_POOL_THREADS):
            window.context.executor.submit(request, _ignore)
        wait_for(qtbot, lambda: runner.held == LONG_POOL_THREADS)
        page.controls.check_button.click()
        wait_for(qtbot, lambda: page.model.loader.loaded.state == "queued")
        status = page.controls.status
        wait_for(qtbot, lambda: status.text().startswith("Waiting to check the files; you can keep choosing"))
        assert page.model.editable()
        _choose_in_cell(qtbot, page, EXTRACT_ENGLISH)
    wait_for(qtbot, lambda: page.model.loader.loaded.state == "shown")
    assert _text(_subtitles(page), 0, STATUS_COLUMN) == "Changed; not applied yet"


def _ignore(_response: object) -> None:
    """Answer of a call nobody waits for."""
