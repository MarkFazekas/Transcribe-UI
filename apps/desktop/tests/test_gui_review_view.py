"""The review page in the window (pytest-qt, offscreen; plan m6 M6.8).

The window runs over a batch in review through `QtExecutor`: Review on the job page opens the
page on the Jobs page, a combo box's choice waits for Apply (its widget kept in place), Remove
goes through the backend's confirmation dialog, the filters narrow the tables, and a hidden page
sends nothing when its job changes.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QTableWidgetItem
from pytestqt.qtbot import QtBot

from support.batches import FILM, FIRST_BATCH_ID, SILENT
from support.gated import wait_for
from support.jobs import RecordingRunner
from support.parity import Leg
from support.review import review_leg
from support.selection_window import settled_window
from transcribe_ui.gui.constants import PAGE_JOBS
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.review.constants import STATUS_COLUMN, TICK_COLUMN
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
    table = _subtitles(page).table
    wait_for(qtbot, lambda: table.rowCount() == SUBTITLE_ROWS)
    return page


def _cell(table: ReviewTable, row: int, column: int) -> QTableWidgetItem:
    cell = table.table.item(row, column)
    assert cell is not None
    return cell


def _names(table: ReviewTable) -> list[tuple[str, str | None]]:
    return [(row.shape.file_name, row.shape.language) for row in table.rows]


def test_review_opens_the_page_on_the_jobs_page(qtbot: QtBot, window: MainWindow) -> None:
    """`open_review` shows the page on the Jobs page at step 2, with the job's files checked."""
    page = _opened(qtbot, window)
    assert window.pages.current_page() == PAGE_JOBS
    assert _jobs(window).currentWidget() is page
    assert _names(_subtitles(page)) == [(FILM, EN), (FILM, HU), (SILENT, HU)]
    assert [step.state for step in page.header.steps.steps] == ["done", "current", "upcoming", "upcoming"]
    wait_for(qtbot, page.action_row.upload_button.isEnabled)
    assert not window.statusBar().currentMessage()


def test_a_choice_waits_for_apply(qtbot: QtBot, window: MainWindow) -> None:
    """Choosing an option marks the row in place; Apply stores it; no button reacts to a stray Enter."""
    page = _opened(qtbot, window)
    table = _subtitles(page)
    combo = table.combo(0)
    assert combo is not None
    combo.setCurrentIndex(EXTRACT_ENGLISH)
    combo.activated.emit(EXTRACT_ENGLISH)
    wait_for(qtbot, lambda: _cell(table, 0, STATUS_COLUMN).text() == "Changed; not applied yet")
    actions = page.action_row
    assert table.combo(0) is combo
    enabled = (actions.apply_button.isEnabled(), actions.upload_button.isEnabled(), actions.hint.text())
    assert enabled == (True, False, "Apply or discard your changes before the upload.")
    buttons = page.findChildren(QPushButton)
    defaults = [button.text() for button in buttons if button.autoDefault() or button.isDefault()]
    assert not defaults
    actions.apply_button.click()
    wait_for(qtbot, lambda: not actions.apply_button.isVisible())
    wait_for(qtbot, lambda: _cell(table, 0, STATUS_COLUMN).text() == "")
    assert combo.currentIndex() == EXTRACT_ENGLISH


def test_remove_goes_through_the_confirmation_dialog(qtbot: QtBot, window: MainWindow) -> None:
    """A ticked file is removed after the backend's question is confirmed in the dialog."""
    page = _opened(qtbot, window)
    table = _subtitles(page)
    _cell(table, SILENT_ROW, TICK_COLUMN).setCheckState(Qt.CheckState.Checked)
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
