"""The generic Qt dialogs: errors, confirmations, and the close dialogs (architecture §3.1 "GUI").

They render the display data of `gui/feedback.py` and decide nothing: the error dialog offers
the fix the table names, the confirmation dialog only accepts or rejects (the caller re-sends
the confirmed request). Dialogs are opened with `open()` (window-modal, no nested event loop).
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import override

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.constants import DETAIL_KEY_HEADER, DETAIL_VALUE_HEADER, SEE_LOG_TEXT
from transcribe_ui.gui.feedback import ConfirmationView, Details, ErrorView, Fix

# Carries out the fix of an error (open a page, retry the request).
type FixHandler = Callable[[Fix], None]
# What a button does.
type Action = Callable[[], None]
# Rows of a table, each a tuple of cell texts.
type Rows = tuple[tuple[str, ...], ...]


def wrapped_label(text: str, parent: QWidget) -> QLabel:
    """Return a label that wraps its text."""
    label = QLabel(text, parent)
    label.setWordWrap(True)
    return label


def read_only_table(columns: tuple[str, ...], rows: Rows, parent: QWidget) -> QTableWidget:
    """Return a table of texts the user cannot edit."""
    table = QTableWidget(parent)
    table.setColumnCount(len(columns))
    table.setRowCount(len(rows))
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            table.setItem(row_index, column_index, QTableWidgetItem(text))
    table.resizeColumnsToContents()
    return table


def add_details(layout: QVBoxLayout, details: Details, parent: QWidget) -> None:
    """Add the rows of the details (as one key/value table) and each detail table to the layout."""
    if details.rows:
        layout.addWidget(read_only_table((DETAIL_KEY_HEADER, DETAIL_VALUE_HEADER), details.rows, parent))
    for detail_table in details.tables:
        layout.addWidget(QLabel(detail_table.title, parent))
        layout.addWidget(read_only_table(detail_table.columns, detail_table.rows, parent))


def open_folder(folder: Path) -> None:
    """Open a folder in the file manager of the system."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def accept_buttons(dialog: QDialog, accept_text: str) -> tuple[QDialogButtonBox, QPushButton]:
    """Return an accept button labelled `accept_text` next to Cancel, wired to the dialog."""
    buttons = QDialogButtonBox(dialog)
    accept_button = buttons.addButton(accept_text, QDialogButtonBox.ButtonRole.AcceptRole)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons, accept_button


class ErrorDialog(QDialog):
    """The message, a collapsed "Details" section (code, details, "Open log folder"), and the fix."""

    def __init__(self, view: ErrorView, log_dir: Path, on_fix: FixHandler, parent: QWidget | None = None) -> None:
        """Show `view`; "Open log folder" opens `log_dir`; the fix button calls `on_fix`."""
        super().__init__(parent)
        self.setWindowTitle("Something went wrong")
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped_label(view.message, self))
        if view.see_log:
            layout.addWidget(QLabel(SEE_LOG_TEXT, self))
        self.details_button = QPushButton("Details", self)
        self.details_button.setCheckable(True)
        layout.addWidget(self.details_button)
        self.details = self._details(view, log_dir)
        layout.addWidget(self.details)
        self.fix_button: QPushButton | None = None
        layout.addWidget(self._buttons(view.fix, on_fix))

    def _details(self, view: ErrorView, log_dir: Path) -> QWidget:
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.addWidget(QLabel(f"Code: {view.code}", section))
        add_details(section_layout, view.details, section)
        log_button = QPushButton("Open log folder", section)
        log_button.clicked.connect(partial(open_folder, log_dir))
        section_layout.addWidget(log_button)
        section.setVisible(False)
        self.details_button.toggled.connect(section.setVisible)
        return section

    def _buttons(self, fix: Fix | None, on_fix: FixHandler) -> QDialogButtonBox:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        if fix is not None:
            self.fix_button = buttons.addButton(fix.label, QDialogButtonBox.ButtonRole.AcceptRole)
            self.fix_button.clicked.connect(partial(self._fix, on_fix, fix))
        return buttons

    def _fix(self, on_fix: FixHandler, fix: Fix) -> None:
        self.accept()
        on_fix(fix)


class ConfirmationDialog(QDialog):
    """The message and details of every confirmation; Confirm accepts, Cancel rejects."""

    def __init__(self, view: ConfirmationView, parent: QWidget | None = None) -> None:
        """Show `view`."""
        super().__init__(parent)
        self.setWindowTitle("Please confirm")
        layout = QVBoxLayout(self)
        for confirmation in view.confirmations:
            layout.addWidget(wrapped_label(confirmation.message, self))
            add_details(layout, confirmation.details, self)
        buttons, confirm_button = accept_buttons(self, "Confirm")
        self.confirm_button = confirm_button
        layout.addWidget(buttons)


class CloseQuestion(QDialog):
    """Q30: "Stop and close?", with the cloud note while a run is not final."""

    def __init__(self, question: str, cloud_note: str | None, parent: QWidget | None = None) -> None:
        """Ask `question`; add `cloud_note` when given."""
        super().__init__(parent)
        self.setWindowTitle("Close Transcribe-UI")
        layout = QVBoxLayout(self)
        layout.addWidget(wrapped_label(question, self))
        if cloud_note is not None:
            layout.addWidget(wrapped_label(cloud_note, self))
        buttons, close_button = accept_buttons(self, "Stop and close")
        self.close_button = close_button
        layout.addWidget(buttons)


class FinishingDialog(QDialog):
    """Shown while running calls finish their current step; "Quit now" ends the process at once.

    Esc and the title bar's close do nothing (`reject` is a no-op), so "Quit now" stays
    reachable until the calls have finished and the window closes.
    """

    def __init__(self, text: str, on_quit_now: Action, parent: QWidget | None = None) -> None:
        """Show `text`; "Quit now" calls `on_quit_now`."""
        super().__init__(parent)
        self.setWindowTitle("Closing")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(text, self))
        self.quit_now_button = QPushButton("Quit now", self)
        self.quit_now_button.clicked.connect(on_quit_now)
        layout.addWidget(self.quit_now_button)

    @override
    def reject(self) -> None:
        """Ignore Esc and the close button: the dialog ends only when the window closes."""
