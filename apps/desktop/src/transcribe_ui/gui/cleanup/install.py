"""Puts the retry and cloud cleanup handlers into the window: their action signals and the moves they count.

A move is anything that takes the user elsewhere: a navigation signal (`gui/signals.py`
`NAVIGATION_SIGNALS`), another page in the navigation list, the job page or the jobs list
("All jobs" switches the Jobs page's stack without a signal), and the start dialog opened or
closed. An answer from before a move asks nothing and opens nothing.
"""

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QStackedWidget

from transcribe_ui.gui.cleanup.area import CleanupHandlers, build_handlers
from transcribe_ui.gui.cleanup.view import CleanupWindowOutlet
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import PAGE_CLOUD, PAGE_JOBS
from transcribe_ui.gui.signals import NAVIGATION_SIGNALS

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class CleanupHub(QObject):
    """Holds the handlers for the window's lifetime (a child of the window) and receives their signals.

    The bus and the widgets connect to its methods; PySide drops those connections with the hub,
    and nothing outside the window holds it, so it never keeps a closed window alive.
    """

    def __init__(self, handlers: CleanupHandlers, parent: QObject) -> None:
        """Own `handlers`."""
        super().__init__(parent)
        self.handlers = handlers

    def retry(self, batch_id: str, jobs: object) -> None:
        """Handle `retry_jobs`."""
        self.handlers.retry.retry(batch_id, jobs)

    def clean_up(self, batch_id: str, scope: str) -> None:
        """Handle `clean_up`."""
        self.handlers.cleanup.clean_up(batch_id, scope)

    def delete_unknown(self, batch_id: str) -> None:
        """Handle `delete_unknown_batch`."""
        self.handlers.cleanup.delete_unknown(batch_id)

    def navigated(self, *_arguments: object) -> None:
        """Count a move."""
        self.handlers.steps.navigated()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Count the start dialog's opening as a move (it has no signal for it)."""
        if event.type() == QEvent.Type.Show:
            self.navigated()
        return False


def install_cleanup(window: "MainWindow") -> CleanupHandlers:
    """Handle `retry_jobs`, `clean_up` and `delete_unknown_batch`; count every move to another page, job or step."""
    hub = CleanupHub(build_handlers(window.context.activity, CleanupWindowOutlet(window)), window)
    window.connect_handler("retry_jobs", hub.retry)
    window.connect_handler("clean_up", hub.clean_up)
    window.connect_handler("delete_unknown_batch", hub.delete_unknown)
    signals = window.context.signals
    for signal_name in NAVIGATION_SIGNALS:
        getattr(signals, signal_name).connect(hub.navigated)
    pages = window.pages
    pages.navigation.currentRowChanged.connect(hub.navigated)
    jobs = pages.page(PAGE_JOBS)
    if isinstance(jobs, QStackedWidget):
        jobs.currentChanged.connect(hub.navigated)
    cloud = pages.page(PAGE_CLOUD)
    if isinstance(cloud, CloudPage):
        cloud.start_dialog.finished.connect(hub.navigated)
        cloud.start_dialog.installEventFilter(hub)
    return hub.handlers
