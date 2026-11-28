"""The cleanup question and the handlers' outlet in the window (architecture §3.1 "GUI").

The question renders `display.CleanupQuestion` and decides nothing: its accept button names
what is deleted, and Cancel is the default button with the focus, so a stray Enter deletes
nothing. It opens over the dialog the user works in (the start dialog for another computer's
job), else over the window, and is deleted once closed.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.cleanup.display import CleanupQuestion
from transcribe_ui.gui.constants import STATUS_MESSAGE_MS
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import ErrorView

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow

# Style of the heading and of a warning line.
EMPHASIS_STYLE = "font-weight: bold;"


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
    """The handlers' outlet in the window: its bus, the cleanup question, its error dialog and its status bar."""

    def __init__(self, window: "MainWindow") -> None:
        """Emit on `window`'s bus and open dialogs over it."""
        self._window = window
        # The question shown last (tests reach its buttons); deleted once closed.
        self.dialog: CleanupDialog | None = None

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` with `arguments`."""
        getattr(self._window.context.signals, signal_name).emit(*arguments)

    def ask(self, question: CleanupQuestion, on_confirm: Callable[[], None]) -> None:
        """Open the cleanup question over the dialog in use (the start dialog), else over the window."""
        parent = QApplication.activeModalWidget() or self._window
        dialog = CleanupDialog(question, parent)
        # Deleted once closed (after the click that closed it has returned).
        dialog.finished.connect(dialog.deleteLater)
        dialog.accepted.connect(on_confirm)
        self.dialog = dialog
        dialog.ask()

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Open the window's error dialog over the dialog in use (the start dialog), else over the window."""
        self._window.show_error(view, retry, QApplication.activeModalWidget())

    def notify(self, text: str) -> None:
        """Show a short note in the window's status bar."""
        self._window.statusBar().showMessage(text, STATUS_MESSAGE_MS)
