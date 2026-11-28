"""Drives the main window as a user would, for the GUI's end-to-end flow (plan m6 M6.11).

Waits run the event loop (`qtbot.waitUntil`), never sleep; "settled" means also that every call
the window sent has answered. Navigation goes through the widgets: the navigation list, the Jobs
page's list and its Open button, the job page's buttons, the Cloud page's Start transcription.
"""

from collections.abc import Callable

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from transcribe_ui.gui.activity import ActivityEntry
from transcribe_ui.gui.cloud.start_view import StartDialog
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import PAGE_CLOUD, PAGE_JOBS, PAGES
from transcribe_ui.gui.jobs.constants import JobButtonKey
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.review.view import ReviewPage
from transcribe_ui.gui.shell import MainWindow

# How long the flow waits for the event loop at one point, in milliseconds.
WAIT_MS = 10_000
# The state of a finished entry of the activity panel.
FINISHED = "finished"
# States of an entry of the activity panel that ended (only `finished` lets the flow go on).
ENDED = frozenset((FINISHED, "failed", "stopped"))
# The job page's button that shows the Jobs page's list.
ALL_JOBS = "All jobs"


def _always() -> bool:
    return True


def wait(qtbot: QtBot, condition: Callable[[], object]) -> None:
    """Run the event loop until `condition` holds (fails after `WAIT_MS`)."""
    qtbot.waitUntil(lambda: bool(condition()), timeout=WAIT_MS)


def settle(qtbot: QtBot, window: MainWindow, condition: Callable[[], object] = _always) -> None:
    """Run the event loop until `condition` holds and every call of the window has answered."""
    wait(qtbot, lambda: condition() and not window.busy())


def started(qtbot: QtBot, window: MainWindow) -> None:
    """Run the start-up steps, as `run_gui` does once the window shows."""
    finished: list[bool] = []
    window.context.hooks.run(lambda: finished.append(True))
    settle(qtbot, window, lambda: finished)


def navigate(qtbot: QtBot, window: MainWindow, key: str) -> None:
    """Choose the page's row in the navigation list."""
    keys = [page_key for page_key, _needs_database in PAGES]
    window.pages.navigation.setCurrentRow(keys.index(key))
    wait(qtbot, lambda: window.pages.current_page() == key)


def entries(window: MainWindow) -> list[ActivityEntry]:
    """Return the activity panel's entries now (the panel replaces its list, so this one stays as it is)."""
    return list(window.context.activity.entries)


def _ended(window: MainWindow, before: list[ActivityEntry], action: str) -> ActivityEntry | None:
    """Return the newest entry of `action` that is not in `before`, once it ended (finished, stopped or failed)."""
    found = window.context.activity.entries
    new = [entry for entry in found if entry.request.action == action and not any(entry is old for old in before)]
    if new and new[-1].state in ENDED:
        return new[-1]
    return None


def finished_after(qtbot: QtBot, window: MainWindow, before: list[ActivityEntry], action: str) -> None:
    """Wait until a new entry of the activity panel (not in `before`) sent `action` and ended; it must have finished.

    A stopped or failed entry fails the flow at once, with its error.
    """
    settle(qtbot, window, lambda: _ended(window, before, action))
    entry = _ended(window, before, action)
    assert entry is not None
    assert entry.state == FINISHED, f"{action} ended {entry.state}: {entry.status}"


def jobs_page(window: MainWindow) -> JobsPage:
    """Return the Jobs page."""
    page = window.pages.page(PAGE_JOBS)
    assert isinstance(page, JobsPage)
    return page


def job_button(qtbot: QtBot, window: MainWindow, batch_id: str, key: JobButtonKey) -> QPushButton:
    """Return a button of the job page once it shows the job with that button."""
    view = jobs_page(window).page_view
    wait(qtbot, lambda: view.model.batch_id == batch_id and key in view.button_row.buttons)
    return view.button_row.buttons[key]


def _leave_review(jobs: JobsPage) -> None:
    shown = jobs.currentWidget()
    if isinstance(shown, ReviewPage):
        shown.header.back_button.click()


def open_job(qtbot: QtBot, window: MainWindow, batch_id: str) -> None:
    """Show the job's page from the Jobs page's list (leaving the review or another job's page first)."""
    navigate(qtbot, window, PAGE_JOBS)
    jobs = jobs_page(window)
    _leave_review(jobs)
    page = jobs.page_view
    if jobs.currentWidget() is page:
        if page.model.batch_id == batch_id:
            return
        next(button for button in page.findChildren(QPushButton) if button.text() == ALL_JOBS).click()
    listed = jobs.list_view
    wait(qtbot, lambda: batch_id in [row.batch_id for row in listed.model.rows])
    listed.table.selectRow([row.batch_id for row in listed.model.rows].index(batch_id))
    listed.open_button.click()
    wait(qtbot, lambda: page.model.batch_id == batch_id)


def cloud_page(window: MainWindow) -> CloudPage:
    """Return the Cloud page."""
    page = window.pages.page(PAGE_CLOUD)
    assert isinstance(page, CloudPage)
    return page


def planned_start(qtbot: QtBot, window: MainWindow) -> StartDialog:
    """The Cloud page's Start transcription; return the start dialog once its plan can start."""
    navigate(qtbot, window, PAGE_CLOUD)
    page = cloud_page(window)
    page.run_panel.buttons.start_button.click()
    dialog = page.start_dialog
    settle(qtbot, window, dialog.start_button.isEnabled)
    return dialog
