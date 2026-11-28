"""The Qt model of a review table and the delegate of its choice column (architecture §3.1 "GUI" "Review and upload").

A table may hold tens of thousands of rows, so nothing is built per row: the model reads the
review index's rows (built once per review) and asks `display.row_state` for what a cell shows
only when the view paints it. A new review resets the model; an edit emits `dataChanged` for the
rows it changed only. The choice column has no widget per row: the delegate creates a combo box
when the user edits a cell (a click), offers that row's choices and hands the chosen
`ReviewChoice` itself to the view-model, so the mouse wheel never changes a choice it passes over.
"""

from functools import partial
from typing import TYPE_CHECKING, override

from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from transcribe_ui.batches.classes import ExtractChoice, TranscribeChoice
from transcribe_ui.gui.review.constants import (
    CHOICE_COLUMN,
    DETAILS_COLUMN,
    FILE_COLUMN,
    FOLDER_COLUMN,
    LANGUAGE_COLUMN,
    STATUS_COLUMN,
    TICK_COLUMN,
    ReviewTab,
)
from transcribe_ui.gui.review.display import ReviewRow, row_state
from transcribe_ui.gui.review.editing import EditChange
from transcribe_ui.gui.review.index import TableRows
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.review.options import ChoiceOption

# A model index as Qt passes it.
type AnyIndex = QModelIndex | QPersistentModelIndex

# The parent of every row (the table has no rows below rows).
TOP_LEVEL = QModelIndex()
# The roles a cell answers.
SHOWN_ROLES = frozenset((Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.CheckStateRole))


def _columns() -> list[str]:
    columns = [tr("Remove"), tr("File"), tr("Folder")]
    columns += [tr("Language"), tr("Choice")]
    columns += [tr("Status"), tr("Details")]
    return columns


def _choice_text(row: ReviewRow) -> str:
    if not row.shape.options:
        return ""
    if row.chosen is None:
        return tr("Choose…")
    return row.shape.options[row.chosen].label


def _cell_text(row: ReviewRow, column: int) -> str:
    shape = row.shape
    texts = {
        FILE_COLUMN: shape.file_name,
        FOLDER_COLUMN: shape.folder,
        LANGUAGE_COLUMN: shape.language or "",
        CHOICE_COLUMN: _choice_text(row),
        STATUS_COLUMN: row.status,
        DETAILS_COLUMN: shape.details,
    }
    return texts.get(column, "")


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """Return sorted rows as runs of consecutive rows (first, last)."""
    runs: list[tuple[int, int]] = []
    for row in sorted(rows):
        if runs and runs[-1][1] == row - 1:
            runs[-1] = (runs[-1][0], row)
        else:
            runs.append((row, row))
    return runs


def _checked(state: object) -> bool:
    """Return whether a check state Qt passes (an enum or its value) is Checked."""
    checked = Qt.CheckState.Checked
    return state in {checked, checked.value}


class ReviewTableModel(QAbstractTableModel):
    """One review table over the review index's rows."""

    def __init__(self, model: ReviewModel, tab: ReviewTab, parent: QObject | None = None) -> None:
        """Show the rows of `tab`; edits go to `model`'s editor."""
        super().__init__(parent)
        self._model = model
        self._tab: ReviewTab = tab
        # The rows shown (the latest review's after `show_rows`).
        self.shown_rows = TableRows()
        model.editor.edits.subscribe(self._edited)

    def show_rows(self) -> bool:
        """Show the latest review's rows; return whether they changed (a reset only then)."""
        rows = self._model.editor.views.current().tabs[self._tab]
        if rows is self.shown_rows:
            return False
        self.beginResetModel()
        self.shown_rows = rows
        self.endResetModel()
        return True

    def row(self, row: int) -> ReviewRow:
        """Return a row with what the user's edits show in it."""
        return row_state(self._model, self.shown_rows.shapes[row])

    @override
    def rowCount(self, /, parent: AnyIndex = TOP_LEVEL) -> int:
        """Return the number of rows (none below a row)."""
        return 0 if parent.isValid() else len(self.shown_rows.shapes)

    @override
    def columnCount(self, /, parent: AnyIndex = TOP_LEVEL) -> int:
        """Return the number of columns."""
        return 0 if parent.isValid() else len(_columns())

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        """Return the column titles."""
        columns = _columns()
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        return columns[section] if 0 <= section < len(columns) else None

    @override
    def data(self, index: AnyIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        """Return a cell's text, or the Remove tick's state (read from the view-model when asked)."""
        if not index.isValid() or role not in SHOWN_ROLES:
            return None
        row = row_state(self._model, self.shown_rows.shapes[index.row()])
        column = index.column()
        if column == TICK_COLUMN:
            ticked = Qt.CheckState.Checked if row.ticked else Qt.CheckState.Unchecked
            return ticked if role == Qt.ItemDataRole.CheckStateRole else None
        return _cell_text(row, column) if role == Qt.ItemDataRole.DisplayRole else None

    @override
    def flags(self, index: AnyIndex) -> Qt.ItemFlag:
        """Return the Remove tick as checkable and a choice as editable while choices can change."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        column = index.column()
        if column == TICK_COLUMN:
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        shown = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        options = self.shown_rows.shapes[index.row()].options
        if column == CHOICE_COLUMN and options and self._model.editable():
            return shown | Qt.ItemFlag.ItemIsEditable
        return shown

    @override
    def setData(self, index: AnyIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:
        """Tick a file for removal, or make a choice (`value`: the `ReviewChoice` itself)."""
        if not index.isValid():
            return False
        item_id = self.shown_rows.shapes[index.row()].item_id
        editor = self._model.editor
        if index.column() == TICK_COLUMN and role == Qt.ItemDataRole.CheckStateRole:
            editor.tick(item_id, ticked=_checked(value))
            return True
        if index.column() == CHOICE_COLUMN and isinstance(value, TranscribeChoice | ExtractChoice):
            shown = self.row(index.row())
            options = shown.shape.options
            chosen = shown.chosen
            unchanged = chosen is not None and options[chosen].choice == value
            # A commit of the choice already shown (the editor lost the focus) changes nothing.
            if unchanged or editor.choose(value):
                return True
            # The job became held while the choice was open: say so rather than lose it silently.
            self._model.outlet.show_status(
                tr("The job is busy; your choice was not kept. Choose again when it is free.")
            )
        return False

    def _edited(self, change: EditChange) -> None:
        """Repaint only the rows an edit changed."""
        rows = self.shown_rows
        by_key = rows.by_key
        changed = [by_key[key] for key in change.keys if key in by_key]
        for item_id in change.files:
            changed.extend(rows.by_item.get(item_id, ()))
        last = len(_columns()) - 1
        for first_row, last_row in _runs(changed):
            top_left = self.index(first_row, 0)
            self.dataChanged.emit(top_left, self.index(last_row, last))


class PickOnlyCombo(QComboBox):
    """The choice editor: a pick by click or keyboard only (the wheel scrolls the table instead)."""

    def __init__(self, parent: QWidget) -> None:
        """Take the focus by click or Tab only."""
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Ignore the wheel (its open popup scrolls by itself)."""
        event.ignore()


class ChoiceDelegate(QStyledItemDelegate):
    """Edits a choice cell with a combo box created only while the cell is edited."""

    @override
    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: AnyIndex) -> QWidget:
        """Return a combo box of the row's choices, each item holding its `ReviewChoice`."""
        model = index.model()
        options: tuple[ChoiceOption, ...] = ()
        if isinstance(model, ReviewTableModel):
            options = model.row(index.row()).shape.options
        combo = PickOnlyCombo(parent)
        combo.setPlaceholderText(tr("Choose…"))
        for choice in options:
            combo.addItem(choice.label, choice.choice)
        combo.activated.connect(partial(self._commit, combo))
        return combo

    @override
    def setEditorData(self, editor: QWidget, index: AnyIndex) -> None:
        """Show the row's choice in the combo box."""
        model = index.model()
        if isinstance(editor, QComboBox) and isinstance(model, ReviewTableModel):
            row = model.row(index.row())
            editor.setCurrentIndex(-1 if row.chosen is None else row.chosen)

    @override
    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: AnyIndex) -> None:
        """Hand the chosen `ReviewChoice` to the model."""
        if isinstance(editor, QComboBox) and editor.currentIndex() >= 0:
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)

    def _commit(self, combo: QComboBox, *_ignored: object) -> None:
        """Apply a choice as soon as it is picked and close the editor (the view deletes it later)."""
        self.commitData.emit(combo)
        self.closeEditor.emit(combo)
