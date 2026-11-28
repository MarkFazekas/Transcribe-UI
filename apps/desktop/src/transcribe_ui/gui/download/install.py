"""Puts the download dialog into the window: the handler of `open_download` and the reload on `batches_changed`."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

from transcribe_ui.gui.dialogs import ConfirmationDialog
from transcribe_ui.gui.download.chooser import FileChooser, QtFileChooser
from transcribe_ui.gui.download.constants import OPEN_DOWNLOAD_SIGNAL
from transcribe_ui.gui.download.view import DownloadDialog
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from transcribe_ui.gui.shell import MainWindow


class DialogOutlet:
    """A download dialog's outlet in the window: the bus, and confirmations over the dialog."""

    def __init__(self, window: "MainWindow") -> None:
        """Emit on `window`'s bus; errors open its error dialog."""
        self._window = window
        # The dialog the confirmations open over (set once it is built).
        self.parent: QWidget | None = None
        # The confirmation asked last (kept for tests).
        self.dialog: ConfirmationDialog | None = None

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` with `arguments`."""
        getattr(self._window.context.signals, signal_name).emit(*arguments)

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask over the download dialog (shown again if it was closed); Confirm calls `on_confirm`."""
        parent = self.parent or self._window
        parent.show()
        parent.raise_()
        dialog = ConfirmationDialog(view, parent)
        dialog.accepted.connect(on_confirm)
        self.dialog = dialog
        dialog.open()

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Open the window's error dialog (its fix buttons open pages, its Retry calls `retry`)."""
        self._window.show_error(view, retry)


class DownloadArea(QObject):
    """The download dialogs of the window, one per job, kept so a dialog reopens with the user's choices."""

    def __init__(self, window: "MainWindow", chooser: FileChooser) -> None:
        """Open dialogs over `window`; their file dialogs come from `chooser`."""
        super().__init__(window)
        self._window = window
        # Asks for folders and files (tests answer it).
        self.chooser = chooser
        self.dialogs: dict[str, DownloadDialog] = {}

    def open_download(self, batch_id: str) -> DownloadDialog:
        """Show the job's download dialog (built on first use) and load its plan."""
        dialog = self.dialogs.get(batch_id)
        if dialog is None:
            dialog = self._build(batch_id)
            self.dialogs[batch_id] = dialog
        dialog.model.on_open()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def on_batches_changed(self, batch_ids: object) -> None:
        """Reload the open dialogs of the jobs that changed."""
        for dialog in self.dialogs.values():
            if dialog.isVisible():
                dialog.model.on_batches_changed(batch_ids)

    def _build(self, batch_id: str) -> DownloadDialog:
        context = self._window.context
        outlet = DialogOutlet(self._window)
        model = DownloadModel(context.executor, context.activity, outlet, batch_id)
        dialog = DownloadDialog(model, self.chooser, self._window)
        outlet.parent = dialog
        return dialog


def install_download(window: "MainWindow", chooser: FileChooser | None = None) -> DownloadArea:
    """Handle `open_download` with the download dialog and reload open dialogs on `batches_changed`."""
    area = DownloadArea(window, QtFileChooser() if chooser is None else chooser)
    window.connect_handler(OPEN_DOWNLOAD_SIGNAL, area.open_download)
    window.context.signals.batches_changed.connect(area.on_batches_changed)
    return area
