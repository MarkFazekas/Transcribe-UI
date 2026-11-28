"""Puts the review page into the window: on the Jobs page, the handlers of `open_review` and `run_upload`."""

from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QStackedWidget

from transcribe_ui.gui.constants import PAGE_JOBS, STATUS_MESSAGE_MS
from transcribe_ui.gui.jobs.install import WindowOutlet
from transcribe_ui.gui.review.uploads import UploadTracker
from transcribe_ui.gui.review.view import ReviewPage
from transcribe_ui.gui.review.viewmodel import ReviewModel

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class ReviewWindowOutlet(WindowOutlet):
    """The review page's outlet in the window: its bus, its dialogs and its status bar."""

    def show_status(self, text: str) -> None:
        """Show a short message in the status bar."""
        self._window.statusBar().showMessage(text, STATUS_MESSAGE_MS)


def _open_review(window: "MainWindow", jobs: QStackedWidget, page: ReviewPage, batch_id: str) -> None:
    """Show a job's review on the Jobs page (the job first, so the page loads it once it shows)."""
    page.model.open_batch(batch_id)
    jobs.setCurrentWidget(page)
    window.pages.show_page(PAGE_JOBS)


def install_review(window: "MainWindow") -> ReviewPage:
    """Add the review page to the Jobs page and handle `open_review` and `run_upload`.

    `run_upload` (Upload or Resume upload on the job page) starts the upload in the activity
    panel without leaving the page it came from.
    """
    context = window.context
    outlet = ReviewWindowOutlet(window)
    uploads = UploadTracker(context.activity, outlet)
    model = ReviewModel(context.executor, outlet, uploads)
    jobs = window.pages.page(PAGE_JOBS)
    if not isinstance(jobs, QStackedWidget):
        message = "The review page needs the Jobs page installed first."
        raise TypeError(message)
    page = ReviewPage(model, jobs)
    jobs.addWidget(page)
    window.connect_handler("open_review", partial(_open_review, window, jobs, page))
    window.connect_handler("run_upload", uploads.start)
    context.signals.batches_changed.connect(model.on_batches_changed)
    return page
