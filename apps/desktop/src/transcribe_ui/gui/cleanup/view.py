"""The cleanup question and the handlers' outlet in the window (architecture §3.1 "GUI").

The question renders `display.CleanupQuestion` and decides nothing: its accept button names
what is deleted, and Cancel is the default button with the focus, so a stray Enter deletes
nothing. It opens over the dialog the user works in (the start dialog for another computer's
job), else over the window, and is deleted once closed.
"""

import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.cleanup.constants import EMPHASIS_STYLE
from transcribe_ui.gui.cleanup.display import CleanupQuestion
from transcribe_ui.gui.constants import STATUS_MESSAGE_MS
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import ErrorView

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class CleanupDialog(QDialog):
    """What is deleted, the backend's confirmations (warnings in bold), the delete button and Cancel."""

    def __init__(self, question: CleanupQuestion, parent: QWidget) -> None:
        """Show `question`; accepting means delete."""
        super().__init__(parent)
        self.setWindowTitle(question.title)
        layout = QVBoxLayout(self)
        heading = wrapped_label(question.heading, self)
        heading.setStyleSheet(EMPHASIS_STYLE)
        layout.addWidget(heading)
        for line in question.lines:
            label = wrapped_label(line.text, self)
            if line.warning:
                label.setStyleSheet(EMPHASIS_STYLE)
            layout.addWidget(label)
        self.delete_button = QPushButton(question.accept_label, self)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self._bind(buttons)
        layout.addWidget(buttons)

    def ask(self) -> None:
        """Open window-modal with Cancel focused."""
        self.open()
        self.cancel_button.setFocus()

    def _bind(self, buttons: QDialogButtonBox) -> None:
        # Deleting needs a click on its button: Enter never triggers it, also not when it has the focus.
        self.delete_button.setAutoDefault(False)
        buttons.addButton(self.delete_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)


class CleanupWindowOutlet:
    """The handlers' outlet in the window: its bus, the cleanup question, its error dialog and its status bar.

    It holds the window weakly, so the handlers never keep a closed window (and its context)
    alive; once the window is gone it does nothing.
    """

    def __init__(self, window: "MainWindow") -> None:
        """Emit on `window`'s bus and open dialogs over it."""
        self._window = weakref.ref(window)

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` with `arguments`."""
        window = self._window()
        if window is not None:
            getattr(window.context.signals, signal_name).emit(*arguments)

    def ask(self, question: CleanupQuestion, on_confirm: Callable[[], None]) -> None:
        """Open the cleanup question over the dialog in use (the start dialog), else over the window."""
        window = self._window()
        if window is None:
            return
        dialog = CleanupDialog(question, QApplication.activeModalWidget() or window)
        # Deleted once closed (after the click that closed it has returned).
        dialog.finished.connect(dialog.deleteLater)
        dialog.accepted.connect(on_confirm)
        dialog.ask()

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Open the window's error dialog over the dialog in use (the start dialog), else over the window."""
        window = self._window()
        if window is not None:
            window.show_error(view, retry, QApplication.activeModalWidget())

    def notify(self, text: str) -> None:
        """Show a short note in the window's status bar."""
        window = self._window()
        if window is not None:
            window.statusBar().showMessage(text, STATUS_MESSAGE_MS)
