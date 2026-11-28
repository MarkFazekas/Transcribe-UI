"""Puts the retry and cloud cleanup handlers into the window: their action signals and the navigation they follow."""

from functools import partial
from typing import TYPE_CHECKING

from transcribe_ui.gui.cleanup.area import CleanupHandlers, build_handlers
from transcribe_ui.gui.cleanup.constants import NAVIGATION_SIGNALS
from transcribe_ui.gui.cleanup.panel import JobSteps
from transcribe_ui.gui.cleanup.retry import RetryModel
from transcribe_ui.gui.cleanup.view import CleanupWindowOutlet
from transcribe_ui.gui.cleanup.viewmodel import CleanupModel

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


def install_cleanup(window: "MainWindow") -> CleanupHandlers:
    """Handle `retry_jobs`, `clean_up` and `delete_unknown_batch`; note every move to another page or job.

    No page holds the handlers, and PySide keeps a bound method's object only weakly, so the
    connections are partials, which keep the handlers alive as long as the window's bus.
    """
    handlers = build_handlers(window.context.activity, CleanupWindowOutlet(window))
    window.connect_handler("retry_jobs", partial(RetryModel.retry, handlers.retry))
    window.connect_handler("clean_up", partial(CleanupModel.clean_up, handlers.cleanup))
    window.connect_handler("delete_unknown_batch", partial(CleanupModel.delete_unknown, handlers.cleanup))
    navigated = partial(JobSteps.navigated, handlers.steps)
    signals = window.context.signals
    for signal_name in NAVIGATION_SIGNALS:
        getattr(signals, signal_name).connect(navigated)
    window.pages.navigation.currentRowChanged.connect(navigated)
    return handlers
