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
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from support.batches import FIRST_BATCH_ID
from support.cleanup import FILM_ID, keys_under
from support.cleanup_gui import cleanup_world
from support.cloud import CloudWorld
from support.gated import wait_for
from support.gui import window_over
from support.launch import FOREIGN_BATCH_ID
from transcribe_ui.batches.payloads import RetryJob
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
    jobs = window.pages.page(PAGE_JOBS)
    assert isinstance(jobs, JobsPage)
    model = jobs.page_view.model
    wait_for(qtbot, lambda: _finished(window) and model.batch_id not in {None, FIRST_BATCH_ID})
    wait_for(qtbot, lambda: model.detail is not None and model.detail.summary.batch_id == model.batch_id)
    assert (window.pages.current_page(), jobs.currentWidget()) == (PAGE_JOBS, jobs.page_view)


def test_delete_from_the_start_dialog_plans_again(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """Delete from cloud asks over the start dialog; once deleted, the dialog's plan drops the job."""
    window.context.hooks.run()
    page = window.pages.page(PAGE_CLOUD)
    assert isinstance(page, CloudPage)
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
