"""The Cloud area's outlet in the window: the bus, the generic dialogs, the fixes and the task-bar alert."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from transcribe_ui.gui.constants import SECTION_ACCOUNTS, SECTION_CLOUD_SETUP
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, Fix

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class CloudWindowOutlet:
    """The Cloud area's outlet in the window: its bus, its generic dialogs, its fixes and the task-bar alert."""

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

    def fix(self, fix: Fix) -> None:
        """Open the page the fix names, or (`app_busy`) probe the writer lock again."""
        context = self._window.context
        signals = context.signals
        if fix.kind == "accounts":
            signals.open_settings.emit(SECTION_ACCOUNTS)
        elif fix.kind == "cloud_setup":
            signals.open_settings.emit(SECTION_CLOUD_SETUP)
        elif fix.kind == "open_batch":
            signals.open_job.emit(fix.argument)
        else:
            context.lock.retry()

    def alert(self) -> None:
        """Flash the window in the task bar (a run ended), unless it is the active window."""
        QApplication.alert(self._window)

    def follow_link(self, target: str, argument: str) -> None:
        """Open what a status bar link names: the Cloud page, its machines list, or a job."""
        signals = self._window.context.signals
        if target == "job":
            signals.open_job.emit(argument)
        elif target == "machines":
            signals.open_cloud_machines.emit()
        else:
            signals.open_cloud.emit()
