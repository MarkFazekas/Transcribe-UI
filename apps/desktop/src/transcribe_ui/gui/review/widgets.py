"""Widgets of the review page: the review tables and the upload box (architecture §3.1 "GUI" "Review and upload").

They hold no rule: each shows display data and passes the user's input to a callback. A table
is rebuilt only when a new review changes its rows' shape; a choice or a tick updates its cells
in place (signals blocked), so a combo box or a tick box is never replaced under the click that
changed it.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.jobs.widgets import read_only_cell, row_table
from transcribe_ui.gui.review.constants import (
    CHOICE_COLUMN,
    DETAILS_COLUMN,
    FILE_COLUMN,
    FOLDER_COLUMN,
    LANGUAGE_COLUMN,
    PERCENT,
    STATUS_COLUMN,
    TICK_COLUMN,
)
from transcribe_ui.gui.review.display import ReviewRow, RowShape
from transcribe_ui.gui.review.status import UploadView
from transcribe_ui.i18n.catalog import tr

# Told a transcription's chosen option: item id, language, the option's position.
type OnChoice = Callable[[str, str, int], None]
# Told a file's Remove tick: item id, ticked.
type OnTick = Callable[[str, bool], None]
# What the upload box's Cancel, Resume and Details do.
type UploadActions = tuple[Callable[[], object], Callable[[], object], Callable[[], object]]


def _call(on_click: Callable[[], object], *_ignored: object) -> None:
    """Call `on_click` without the arguments of the signal (`clicked` passes `checked`)."""
    on_click()


def action_button(text: str, on_click: Callable[[], object], parent: QWidget) -> QPushButton:
    """Return a button that never reacts to a stray Enter (no default, no auto-default)."""
    button = QPushButton(text, parent)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.clicked.connect(partial(_call, on_click))
    return button


def _columns() -> list[str]:
    columns = [tr("Remove"), tr("File"), tr("Folder")]
    columns += [tr("Language"), tr("Choice")]
    columns += [tr("Status"), tr("Details")]
    return columns


def _check_state(*, ticked: bool) -> Qt.CheckState:
    return Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked


def _tick_cell(*, ticked: bool) -> QTableWidgetItem:
    cell = QTableWidgetItem()
    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
    cell.setCheckState(_check_state(ticked=ticked))
    return cell


def _shown_index(chosen: int | None) -> int:
    """Return the combo box index of a choice (-1 shows the placeholder: no choice)."""
    return -1 if chosen is None else chosen


class ReviewTable(QWidget):
    """A review table: a row per transcription (with its choice) or per file that needs attention."""

    def __init__(self, on_choice: OnChoice, on_tick: OnTick, parent: QWidget) -> None:
        """Start empty; a choice calls `on_choice`, a Remove tick `on_tick`."""
        super().__init__(parent)
        self._on_choice = on_choice
        self._on_tick = on_tick
        self.table: QTableWidget = row_table(_columns(), self)
        self.table.itemChanged.connect(self._ticked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        self.rows: tuple[ReviewRow, ...] = ()
        self._shapes: tuple[RowShape, ...] | None = None

    def show_rows(self, rows: tuple[ReviewRow, ...]) -> None:
        """Show the rows: rebuilt when their shape changed, else updated in place."""
        if rows == self.rows and self._shapes is not None:
            return
        shapes = tuple(row.shape for row in rows)
        with QSignalBlocker(self.table):
            if shapes == self._shapes:
                for index, row in enumerate(rows):
                    self._update(index, row)
            else:
                self._rebuild(rows)
        self.rows = rows
        self._shapes = shapes

    def combo(self, row_index: int) -> QComboBox | None:
        """Return the combo box of a row (None for a row without a choice)."""
        widget = self.table.cellWidget(row_index, CHOICE_COLUMN)
        return widget if isinstance(widget, QComboBox) else None

    def _rebuild(self, rows: tuple[ReviewRow, ...]) -> None:
        table = self.table
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._fill(index, row)
        table.resizeColumnsToContents()

    def _fill(self, index: int, row: ReviewRow) -> None:
        shape = row.shape
        cells = {
            FILE_COLUMN: shape.file_name,
            FOLDER_COLUMN: shape.folder,
            LANGUAGE_COLUMN: shape.language or "",
            STATUS_COLUMN: row.status,
            DETAILS_COLUMN: shape.details,
        }
        self.table.setItem(index, TICK_COLUMN, _tick_cell(ticked=row.ticked))
        for column, text in cells.items():
            self.table.setItem(index, column, read_only_cell(text))
        if shape.options:
            self.table.setCellWidget(index, CHOICE_COLUMN, self._combo(index, row))

    def _combo(self, index: int, row: ReviewRow) -> QComboBox:
        combo = QComboBox(self.table)
        combo.addItems(list(row.shape.options))
        combo.setPlaceholderText(tr("Choose…"))
        combo.setCurrentIndex(_shown_index(row.chosen))
        combo.activated.connect(partial(self._chosen, index))
        return combo

    def _update(self, index: int, row: ReviewRow) -> None:
        tick = self.table.item(index, TICK_COLUMN)
        if tick is not None:
            tick.setCheckState(_check_state(ticked=row.ticked))
        status = self.table.item(index, STATUS_COLUMN)
        if status is not None:
            status.setText(row.status)
        combo = self.combo(index)
        if combo is not None:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(_shown_index(row.chosen))

    def _chosen(self, index: int, option: int) -> None:
        shape = self.rows[index].shape
        if shape.language is not None:
            self._on_choice(shape.item_id, shape.language, option)

    def _ticked(self, cell: QTableWidgetItem) -> None:
        row_index = cell.row()
        shown = 0 <= row_index < len(self.rows)
        if cell.column() != TICK_COLUMN or not shown:
            return
        ticked = cell.checkState() == Qt.CheckState.Checked
        self._on_tick(self.rows[row_index].shape.item_id, ticked)


class UploadBox(QWidget):
    """The job's upload: its state, files done and the current object's bytes, Cancel, Resume and Details."""

    def __init__(self, actions: UploadActions, parent: QWidget) -> None:
        """Start hidden; `actions` are Cancel, Resume and Details."""
        super().__init__(parent)
        cancel, resume, details = actions
        self.state = wrapped_label("", self)
        self.progress = QLabel(self)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, PERCENT)
        self.cancel_button = action_button(tr("Cancel upload"), cancel, self)
        self.resume_button = action_button(tr("Resume upload"), resume, self)
        self.details_button = action_button(tr("Details"), details, self)
        self._lay_out()
        self.setVisible(False)

    def show_upload(self, view: UploadView | None) -> None:
        """Show the upload (None: hide the box)."""
        self.setVisible(view is not None)
        if view is None:
            return
        self.state.setText(tr("Upload: {state}").format(state=view.state))
        lines = [line for line in (view.files, view.part) if line]
        self.progress.setText("\n".join(lines))
        self.progress.setVisible(bool(lines))
        self.progress_bar.setVisible(view.percent is not None)
        self.progress_bar.setValue(view.percent or 0)
        self._show_buttons(view)

    def _show_buttons(self, view: UploadView) -> None:
        self.cancel_button.setVisible(view.can_cancel)
        self.resume_button.setVisible(view.can_resume)
        self.details_button.setVisible(view.error is not None)

    def _lay_out(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        texts = QVBoxLayout()
        for text in (self.state, self.progress, self.progress_bar):
            texts.addWidget(text)
        layout.addLayout(texts, 1)
        for button in (self.cancel_button, self.resume_button, self.details_button):
            layout.addWidget(button)
