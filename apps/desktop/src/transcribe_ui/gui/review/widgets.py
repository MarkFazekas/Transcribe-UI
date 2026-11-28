"""Widgets of the review page: the review tables and the upload box (architecture §3.1 "GUI" "Review and upload").

They hold no rule: each shows display data and passes the user's input to its view-model. A
table is a view over `models.ReviewTableModel` (reset only by a new review, repainted per changed
row by an edit), with no widget per row.
"""

from collections.abc import Callable
from functools import partial
from typing import override

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.review.constants import CHOICE_COLUMN, PERCENT
from transcribe_ui.gui.review.index import TableRows
from transcribe_ui.gui.review.status import UploadView
from transcribe_ui.gui.review.table import ChoiceDelegate, ReviewTableModel
from transcribe_ui.i18n.catalog import tr

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


class WheelSafeCombo(QComboBox):
    """A combo box the mouse wheel changes only while it has the focus (scrolling past it changes nothing)."""

    def __init__(self, parent: QWidget) -> None:
        """Take the focus by click or Tab only."""
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Change the choice only while focused; otherwise let the page scroll."""
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _fixed_rows(header: QHeaderView) -> None:
    """Hide the row numbers and keep every row at one height, so no row is measured."""
    header.setVisible(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)


# A row's transcription (item id, language), or its file (item id, None) for a file that needs attention.
type RowKey = tuple[str, str | None]


def _row_of(rows: TableRows, kept: RowKey) -> int | None:
    """Return the row of a transcription, or the first row of a file, in `rows`."""
    item_id, language = kept
    if language is not None:
        return rows.by_key.get((item_id, language))
    return next(iter(rows.by_item.get(item_id, ())), None)


class ReviewTable(QTableView):
    """A review table: a row per transcription (its choice edited in place) or per file that needs attention.

    No widget is made per row: rows have a fixed height and the choice column's combo box exists
    only while a cell is edited (a click on it).
    """

    def __init__(self, model: ReviewTableModel, parent: QWidget) -> None:
        """Show `model`; a click on a choice starts editing it."""
        super().__init__(parent)
        self.setModel(model)
        self.rows = model
        self.setItemDelegateForColumn(CHOICE_COLUMN, ChoiceDelegate(self))
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        triggers = QTableView.EditTrigger.SelectedClicked | QTableView.EditTrigger.EditKeyPressed
        self.setEditTriggers(triggers)
        _fixed_rows(self.verticalHeader())
        self.clicked.connect(self._clicked)
        # The job whose first load sized the columns (later reviews keep the user's widths).
        self._sized_for: str | None = None

    def show_rows(self, batch_id: str | None, *, editable: bool) -> int:
        """Show the latest review's rows, keeping the current row and the scroll position; return their count.

        An open choice editor is closed once choices cannot change (its pick would not be kept).
        """
        if not editable:
            self._close_editor()
        kept = self._current_key()
        scroll = self.verticalScrollBar().value()
        if self.rows.show_rows():
            if batch_id != self._sized_for and self.rows.rowCount():
                self._sized_for = batch_id
                self.resizeColumnsToContents()
            self._restore(kept, scroll)
        return self.rows.rowCount()

    def _current_key(self) -> RowKey | None:
        current = self.currentIndex()
        shapes = self.rows.shown_rows.shapes
        if not current.isValid() or current.row() >= len(shapes):
            return None
        shape = shapes[current.row()]
        return shape.item_id, shape.language

    def _restore(self, kept: RowKey | None, scroll: int) -> None:
        """Select the row of the kept transcription (or file) again, and scroll back to where the table was."""
        found = None if kept is None else _row_of(self.rows.shown_rows, kept)
        if found is not None:
            self.setCurrentIndex(self.rows.index(found, 0))
        self.verticalScrollBar().setValue(scroll)

    def _close_editor(self) -> None:
        editor = self.indexWidget(self.currentIndex())
        if editor is not None:
            self.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def _clicked(self, index: QModelIndex) -> None:
        editable = bool(index.flags() & Qt.ItemFlag.ItemIsEditable)
        if index.column() == CHOICE_COLUMN and editable:
            self.edit(index)


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
        blocked = view.blocked if view.can_resume else ""
        lines = [line for line in (view.files, view.part, blocked) if line]
        self.progress.setText("\n".join(lines))
        self.progress.setVisible(bool(lines))
        self.progress_bar.setVisible(view.percent is not None)
        self.progress_bar.setValue(view.percent or 0)
        self._show_buttons(view)

    def _show_buttons(self, view: UploadView) -> None:
        self.cancel_button.setVisible(view.can_cancel)
        self.resume_button.setVisible(view.can_resume)
        self.resume_button.setEnabled(view.resume_enabled)
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
