"""The retry and cleanup handlers in the window (pytest-qt, offscreen; plan m6 M6.10).

The window runs over a seeded cloud world through `QtExecutor`: the job page's signals reach
the handlers through the bus; the cleanup question deletes only on a click of its button (Enter
cancels); Retry opens the retry job; Delete from cloud in the start dialog asks over that
dialog and the dialog plans again.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot

from support.batches import FIRST_BATCH_ID
from support.cleanup import FILM_ID, keys_under
from support.cleanup_gui import cleanup_world
from support.cloud import CloudWorld
from support.flow_window import started
from support.gated import wait_for
from support.gui import window_over
from support.launch import FOREIGN_BATCH_ID
from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.gui.cleanup.display import not_asked_text
from transcribe_ui.gui.cleanup.view import CleanupDialog
from transcribe_ui.gui.cloud.start_view import StartDialog
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import PAGE_CLOUD, PAGE_JOBS
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.shell import MainWindow


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a leg with the transcribed source batch and another computer's job."""
    with cleanup_world(tmp_path) as created:
        yield created


@pytest.fixture
def window(qtbot: QtBot, world: CloudWorld, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the world; drained afterwards."""
    created = window_over(qtbot, world.backend, tmp_path)
    yield created
    # pytest-qt has deleted the window by now, which closed the executor (late answers are
    # dropped); the calls still running finish here.
    wait_for(qtbot, lambda: not created.busy())


def _question(qtbot: QtBot) -> CleanupDialog:
    wait_for(qtbot, lambda: isinstance(QApplication.activeModalWidget(), CleanupDialog))
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, CleanupDialog)
    return dialog


def _jobs(window: MainWindow) -> JobsPage:
    jobs = window.pages.page(PAGE_JOBS)
    assert isinstance(jobs, JobsPage)
    return jobs


def _all_jobs(jobs: JobsPage) -> QPushButton:
    """Return the job page's "All jobs" button (it switches the Jobs page without a bus signal)."""
    return next(button for button in jobs.page_view.findChildren(QPushButton) if button.text() == "All jobs")


def _cloud(window: MainWindow) -> CloudPage:
    page = window.pages.page(PAGE_CLOUD)
    assert isinstance(page, CloudPage)
    return page


def _finished(window: MainWindow) -> bool:
    entries = window.context.activity.entries
    return not window.busy() and bool(entries) and entries[-1].state == "finished"


def test_the_question_deletes_only_on_its_button(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """Enter in the question cancels (nothing deleted); its Delete everything button deletes in the panel."""
    kept = keys_under(world.store)
    signals = window.context.signals
    signals.clean_up.emit(FIRST_BATCH_ID, "everything")
    dialog = _question(qtbot)
    assert (dialog.cancel_button.isDefault(), dialog.delete_button.autoDefault()) == (True, False)
    assert dialog.delete_button.text() == "Delete everything"
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    wait_for(qtbot, lambda: QApplication.activeModalWidget() is None and not window.busy())
    assert (keys_under(world.store), len(window.context.activity.entries)) == (kept, 0)
    signals.clean_up.emit(FIRST_BATCH_ID, "everything")
    _question(qtbot).delete_button.click()
    wait_for(qtbot, lambda: _finished(window))
    assert not keys_under(world.store)
    assert window.statusBar().currentMessage() != "Not available yet."


def test_retry_opens_the_retry_job(qtbot: QtBot, window: MainWindow) -> None:
    """Retry from the job page copies in the panel, then the Jobs page shows the retry job."""
    signals = window.context.signals
    signals.open_job.emit(FIRST_BATCH_ID)
    signals.retry_jobs.emit(FIRST_BATCH_ID, (RetryJob(item_id=FILM_ID, language="en"),))
    jobs = _jobs(window)
    model = jobs.page_view.model
    wait_for(qtbot, lambda: _finished(window) and model.batch_id not in {None, FIRST_BATCH_ID})
    wait_for(qtbot, lambda: model.detail is not None and model.detail.summary.batch_id == model.batch_id)
    assert (window.pages.current_page(), jobs.currentWidget()) == (PAGE_JOBS, jobs.page_view)


def test_delete_from_the_start_dialog_plans_again(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """Delete from cloud asks over the start dialog; once deleted, the dialog's plan drops the job."""
    started(qtbot, window)
    page = _cloud(window)
    page.run_panel.buttons.start_button.click()
    start = page.start_dialog
    wait_for(qtbot, lambda: FOREIGN_BATCH_ID in start.plan.delete_buttons)
    start.plan.delete_buttons[FOREIGN_BATCH_ID].click()
    dialog = _question(qtbot)
    assert isinstance(dialog.parent(), StartDialog)
    dialog.delete_button.click()
    wait_for(qtbot, lambda: _finished(window) and start.model.phase == "ready")
    wait_for(qtbot, lambda: FOREIGN_BATCH_ID not in start.plan.delete_buttons)
    assert not keys_under(world.store, FOREIGN_BATCH_ID)


def test_all_jobs_after_a_cleanup_asks_nothing(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """All jobs before the cleanup's question arrives: no question opens, a note says nothing was deleted."""
    signals = window.context.signals
    signals.open_job.emit(FIRST_BATCH_ID)
    kept = keys_under(world.store)
    signals.clean_up.emit(FIRST_BATCH_ID, "everything")
    _all_jobs(_jobs(window)).click()
    wait_for(qtbot, lambda: not window.busy())
    assert (QApplication.activeModalWidget(), window.statusBar().currentMessage()) == (
        None,
        not_asked_text(FIRST_BATCH_ID),
    )
    assert keys_under(world.store) == kept


def test_all_jobs_after_a_retry_stays_on_the_list(qtbot: QtBot, window: MainWindow) -> None:
    """All jobs before the retry answers: the list stays shown, a note says where the retry job is."""
    signals = window.context.signals
    signals.open_job.emit(FIRST_BATCH_ID)
    signals.retry_jobs.emit(FIRST_BATCH_ID, (RetryJob(item_id=FILM_ID, language="en"),))
    jobs = _jobs(window)
    _all_jobs(jobs).click()
    wait_for(qtbot, lambda: _finished(window))
    assert (jobs.currentWidget(), jobs.page_view.model.batch_id) == (jobs.list_view, None)
    assert window.statusBar().currentMessage().startswith("The retry job ")


def test_closing_the_start_dialog_drops_the_question(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """The start dialog closed before the question of another computer's job arrives: it does not open."""
    started(qtbot, window)
    page = _cloud(window)
    page.run_panel.buttons.start_button.click()
    start = page.start_dialog
    wait_for(qtbot, lambda: FOREIGN_BATCH_ID in start.plan.delete_buttons)
    window.context.signals.delete_unknown_batch.emit(FOREIGN_BATCH_ID)
    start.reject()
    wait_for(qtbot, lambda: not window.busy())
    assert (QApplication.activeModalWidget(), window.statusBar().currentMessage()) == (
        None,
        not_asked_text(FOREIGN_BATCH_ID),
    )
    assert keys_under(world.store, FOREIGN_BATCH_ID)


def test_opening_the_start_dialog_drops_the_question(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """The start dialog opened while a job page's cleanup check is in flight: no question, a note instead."""
    signals = window.context.signals
    signals.open_job.emit(FIRST_BATCH_ID)
    kept = keys_under(world.store)
    signals.clean_up.emit(FIRST_BATCH_ID, "everything")
    _cloud(window).open_start()
    wait_for(qtbot, lambda: not window.busy())
    assert isinstance(QApplication.activeModalWidget(), StartDialog)
    assert window.statusBar().currentMessage() == not_asked_text(FIRST_BATCH_ID)
    assert keys_under(world.store) == kept
