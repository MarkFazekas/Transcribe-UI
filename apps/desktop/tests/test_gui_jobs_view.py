"""The Jobs page and the step header in the window (pytest-qt, offscreen; plan m6 M6.5).

The window runs over the seeded scenarios through `QtExecutor`: the start-up step fills the
list, a double click opens a job, its buttons reach their signal or "Not available yet", a
tick box disables Retry, and Delete goes through the confirmation dialog.
"""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
from pytestqt.qtbot import QtBot

from support.actions import COPYING_RETRY, RETRIED_SOURCE, SCENARIOS, scenario_batch_id
from support.gated import wait_for
from support.gui import window_over
from support.jobs import scenario_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports
from transcribe_ui.gui.constants import PAGE_JOBS
from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.gui.steps.view import StepHeader

# The scenarios the default list shows (one of them is hidden).
SHOWN = len(SCENARIOS) - 1


@pytest.fixture
def ports(tmp_path: Path) -> Iterator[Ports]:
    """Return ports over a database holding every scenario's batch (closed afterwards)."""
    created = scenario_ports(tmp_path)
    with closing(created.state):
        yield created


@pytest.fixture
def window(qtbot: QtBot, ports: Ports, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the seeded backend, its start-up steps run; drained afterwards."""
    created = window_over(qtbot, Backend(ports), tmp_path)
    created.context.hooks.run()
    yield created
    # pytest-qt has deleted the window by now, which closed the executor (late answers are
    # dropped); the calls still running finish here.
    wait_for(qtbot, lambda: not created.busy())


def _cell(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    cell = table.item(row, column)
    assert cell is not None
    return cell


def _jobs(window: MainWindow) -> JobsPage:
    page = window.pages.page(PAGE_JOBS)
    assert isinstance(page, JobsPage)
    return page


def _open(qtbot: QtBot, window: MainWindow, name: str) -> JobsPage:
    jobs = _jobs(window)
    window.context.signals.open_job.emit(scenario_batch_id(name))
    wait_for(qtbot, lambda: jobs.page_view.model.header is not None)
    return jobs


def test_the_start_up_step_fills_the_list(qtbot: QtBot, window: MainWindow) -> None:
    """The list loads at start-up; "Show hidden jobs" adds the hidden one; a double click opens a job."""
    jobs = _jobs(window)
    table = jobs.list_view.table
    wait_for(qtbot, lambda: table.rowCount() == SHOWN)
    jobs.list_view.show_hidden.setChecked(True)
    wait_for(qtbot, lambda: table.rowCount() == len(SCENARIOS))
    table.selectRow(0)
    assert jobs.list_view.open_button.isEnabled()
    batch_id = _cell(table, 0, 1).text()
    table.cellDoubleClicked.emit(0, 0)
    wait_for(qtbot, lambda: jobs.page_view.model.header is not None)
    assert jobs.currentWidget() is jobs.page_view
    assert window.pages.current_page() == PAGE_JOBS
    assert jobs.page_view.header.title.text().startswith(f"Job {batch_id}")


def _selected_text(jobs: JobsPage) -> str | None:
    table = jobs.list_view.table
    cells = [cell for cell in table.selectedItems() if cell.column() == 1]
    return cells[0].text() if cells else None


def test_the_selection_follows_its_job_across_reloads(qtbot: QtBot, window: MainWindow) -> None:
    """A reload that removes a row above keeps the same job selected; once the job is gone, none is."""
    jobs = _jobs(window)
    view = jobs.list_view
    wait_for(qtbot, lambda: view.table.rowCount() == SHOWN)
    kept = scenario_batch_id(COPYING_RETRY)
    rows = [row.batch_id for row in view.model.rows]
    view.table.selectRow(rows.index(kept))
    above = scenario_batch_id(RETRIED_SOURCE)
    assert rows.index(above) < rows.index(kept)
    view.model.press(above, "hide")
    wait_for(qtbot, lambda: view.table.rowCount() == SHOWN - 1)
    assert (view.selected(), _selected_text(jobs)) == (kept, kept)
    gone = scenario_batch_id("downloaded_no_speech")
    view.table.selectRow([row.batch_id for row in view.model.rows].index(gone))
    view.button_row.buttons["hide"].click()
    wait_for(qtbot, lambda: view.table.rowCount() == SHOWN - 2)
    assert (view.selected(), _selected_text(jobs), view.open_button.isEnabled()) == (None, None, False)
    assert not view.button_row.buttons


def test_an_unhandled_button_is_not_available(qtbot: QtBot, window: MainWindow) -> None:
    """Review emits its signal, which no part handles yet: the status bar says so."""
    jobs = _open(qtbot, window, "reviewing_open")
    buttons = jobs.page_view.button_row.buttons
    assert list(buttons) == ["delete", "review"]
    with qtbot.waitSignal(window.context.signals.open_review) as emitted:
        buttons["review"].click()
    assert emitted.args == [scenario_batch_id("reviewing_open")]
    assert window.statusBar().currentMessage() == "Not available yet."


def test_clearing_the_last_tick_disables_retry(qtbot: QtBot, window: MainWindow) -> None:
    """The failed job's tick box starts ticked; clearing it disables Retry without rebuilding the table."""
    jobs = _open(qtbot, window, "transcribed_flagged")
    view = jobs.page_view
    table = view.table
    tick = _cell(table, 1, 0)
    checkable = _cell(table, 0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert (tick.checkState(), checkable) == (Qt.CheckState.Checked, Qt.ItemFlag.NoItemFlags)
    retry = view.button_row.buttons["retry"]
    assert retry.isEnabled()
    tick.setCheckState(Qt.CheckState.Unchecked)
    retry = view.button_row.buttons["retry"]
    assert (retry.isEnabled(), _cell(table, 1, 0) is tick) == (False, True)
    assert [step.state for step in view.steps.steps] == ["done", "done", "done", "current"]


def test_an_equal_reload_keeps_the_table_and_the_ticks(qtbot: QtBot, window: MainWindow) -> None:
    """A reload that returns the same rows neither rebuilds the table nor brings a cleared tick back."""
    jobs = _open(qtbot, window, "transcribed_flagged")
    view = jobs.page_view
    tick = _cell(view.table, 1, 0)
    tick.setCheckState(Qt.CheckState.Unchecked)
    retry = view.button_row.buttons["retry"]
    loads: list[bool] = []
    view.model.subscribe(lambda: loads.append(True))
    view.model.reload()
    wait_for(qtbot, lambda: bool(loads))
    kept = (_cell(view.table, 1, 0) is tick, tick.checkState(), view.button_row.buttons["retry"] is retry)
    assert kept == (True, Qt.CheckState.Unchecked, True)
    assert not retry.isEnabled()


def test_an_equal_reload_keeps_the_list_buttons(qtbot: QtBot, window: MainWindow) -> None:
    """A reload that changes neither the selected job nor its buttons keeps the same button widgets."""
    view = _jobs(window).list_view
    wait_for(qtbot, lambda: view.table.rowCount() == SHOWN)
    view.table.selectRow([row.batch_id for row in view.model.rows].index(scenario_batch_id("cleaned")))
    hide = view.button_row.buttons["hide"]
    loads: list[bool] = []
    view.model.subscribe(lambda: loads.append(True))
    view.model.reload()
    wait_for(qtbot, lambda: bool(loads))
    assert view.button_row.buttons["hide"] is hide


def test_delete_asks_and_returns_to_the_list(qtbot: QtBot, window: MainWindow) -> None:
    """Delete opens the backend's confirmation; Confirm deletes the job and shows the list without it."""
    jobs = _open(qtbot, window, "composing")
    buttons = jobs.page_view.button_row.buttons
    buttons["delete"].click()
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog))
    dialog = window.dialog
    assert isinstance(dialog, ConfirmationDialog)
    dialog.confirm_button.click()
    table = jobs.list_view.table
    wait_for(qtbot, lambda: jobs.currentWidget() is jobs.list_view and table.rowCount() == SHOWN - 1)
    shown = {_cell(table, row, 1).text() for row in range(table.rowCount())}
    assert scenario_batch_id("composing") not in shown


def test_a_load_error_shows_in_the_page_with_retry(qtbot: QtBot, window: MainWindow) -> None:
    """A job that is gone shows its error in the page; Retry loads it again; so does the list's Retry."""
    jobs = _jobs(window)
    window.context.signals.open_job.emit("batch_20990101_000000")
    banner = jobs.page_view.banner
    wait_for(qtbot, lambda: banner.isVisibleTo(jobs.page_view))
    assert banner.message.text() == "The job batch_20990101_000000 does not exist."
    model = jobs.page_view.model
    model.error = None
    banner.retry_button.click()
    wait_for(qtbot, lambda: model.error is not None)
    list_model = jobs.list_view.model
    list_model.rows = ()
    jobs.list_view.banner.retry_button.click()
    wait_for(qtbot, lambda: len(list_model.rows) == SHOWN)


def test_the_step_header_navigation(qtbot: QtBot) -> None:
    """Back and Next show only when the page connects them, and call what it gave."""
    header = StepHeader()
    qtbot.addWidget(header)
    header.show()
    assert (header.back_button.isVisible(), header.next_button.isVisible()) == (False, False)
    pressed: list[str] = []
    header.set_navigation(lambda: pressed.append("back"), lambda: pressed.append("next"))
    header.back_button.click()
    header.next_button.click()
    assert pressed == ["back", "next"]
    header.show_stage("done")
    assert [label.text() for label in header.step_labels] == [
        "✓ 1. Files",
        "✓ 2. Review & upload",
        "✓ 3. Transcription",
        "✓ 4. Download",
    ]
    header.show_stage("review_upload")
    assert [label.font().bold() for label in header.step_labels] == [False, True, False, False]
