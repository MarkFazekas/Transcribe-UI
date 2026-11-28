"""Puts the Jobs page into the window: registration, navigation, `batches_changed`, the start-up load."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from transcribe_ui.gui.constants import PAGE_JOBS
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView
from transcribe_ui.gui.jobs.commands import JobCommands
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.jobs.viewmodel import JobPageModel, JobsListModel
from transcribe_ui.gui.signals import StartupSlot
from transcribe_ui.gui.texts import page_title

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class WindowOutlet:
    """The jobs pages' outlet in the window: its bus and its generic dialogs."""

    def __init__(self, window: "MainWindow") -> None:
        """Emit on `window`'s bus and open its dialogs."""
        self._window = window

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` with `arguments`."""
        getattr(self._window.context.signals, signal_name).emit(*arguments)

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Open the confirmation dialog."""
        self._window.show_confirmation(view, on_confirm)

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Open the error dialog."""
        self._window.show_error(view, retry)


def install_jobs(window: "MainWindow") -> JobsPage:
    """Register the Jobs page, connect its navigation and `batches_changed`, and add the start-up load."""
    context = window.context
    outlet = WindowOutlet(window)
    commands = JobCommands(context.executor, outlet)
    list_model = JobsListModel(context.executor, commands, outlet)
    page_model = JobPageModel(context.executor, commands, outlet)
    page = JobsPage(list_model, page_model, window)
    window.pages.add_page(PAGE_JOBS, page_title(PAGE_JOBS), page, needs_database=True)
    signals = context.signals
    signals.open_job.connect(page.open_job)
    signals.open_jobs.connect(page.show_list)
    signals.batches_changed.connect(list_model.on_batches_changed)
    signals.batches_changed.connect(page_model.on_batches_changed)
    context.hooks.register(StartupSlot.BATCHES, list_model.reload)
    return page
