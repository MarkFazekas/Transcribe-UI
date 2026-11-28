"""Widgets the jobs list and the job page share: read-only tables, a row of buttons, an error banner."""

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.gui.jobs.display import JobButton
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.jobs.constants import JobButtonKey

# Called with the button that was clicked.
type OnPress = Callable[[JobButton], None]


def read_only_cell(text: str) -> QTableWidgetItem:
    """Return a table cell the user can select but neither edit nor tick."""
    cell = QTableWidgetItem(text)
    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return cell


def fill_table(table: QTableWidget, rows: list[list[str]]) -> None:
    """Replace the table's rows with read-only cells."""
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            table.setItem(row_index, column_index, read_only_cell(text))


def row_table(columns: list[str], parent: QWidget) -> QTableWidget:
    """Return a table of whole-row single selection with these column titles."""
    table = QTableWidget(parent)
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    return table


class ButtonRow(QWidget):
    """A row of buttons rebuilt from display data."""

    def __init__(self, parent: QWidget) -> None:
        """Start empty."""
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.buttons: dict[JobButtonKey, QPushButton] = {}

    def show_buttons(self, buttons: tuple[JobButton, ...], on_press: OnPress) -> None:
        """Replace the buttons; a click calls `on_press` with its button."""
        for old in self.buttons.values():
            self._layout.removeWidget(old)
            old.deleteLater()
        self.buttons = {}
        for button in buttons:
            widget = QPushButton(button.label, self)
            widget.setEnabled(button.enabled)
            widget.clicked.connect(partial(on_press, button))
            self._layout.addWidget(widget)
            self.buttons[button.key] = widget


class ErrorBanner(QWidget):
    """The error of a load, with Retry; hidden while there is none."""

    def __init__(self, on_retry: Callable[[], object], parent: QWidget) -> None:
        """Hidden until an error is shown; Retry calls `on_retry`."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = wrapped_label("", self)
        layout.addWidget(self.message, 1)
        self._on_retry = on_retry
        self.retry_button = QPushButton(tr("Retry"), self)
        self.retry_button.clicked.connect(self._retry)
        layout.addWidget(self.retry_button)
        self.setVisible(False)

    def show_error(self, error: ErrorView | None) -> None:
        """Show the error's text, or hide the banner."""
        self.message.setText("" if error is None else error.message)
        self.setVisible(error is not None)

    def _retry(self) -> None:
        # Called without the button's `checked` argument, which `on_retry` would take for its own.
        self._on_retry()
