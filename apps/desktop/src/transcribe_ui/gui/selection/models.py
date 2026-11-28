"""The selection tree's lazy item model over the flat scan (architecture §3.2).

Rows are virtual: an index carries its node number (`ScanIndex`), and every answer (row
counts, parents, cells) is looked up when the view asks, so a 20 000-file scan opens at once
and scrolls without building a node per row. A new scan resets the model; a new preview, a
filter's matches or changed target languages only tell the view that the cells changed. A
click on a tick box goes to the view-model, which appends the operation; the tick boxes then
follow the preview's answer.
"""

from typing import TYPE_CHECKING, overload, override

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QGuiApplication, QPalette

from transcribe_ui.gui.selection.constants import CHECKED, COLUMN_COUNT, NAME_COLUMN, PARTIAL, CheckMark
from transcribe_ui.gui.selection.rows import cell_text, cell_tooltip, row_dimmed, row_mark, tick_row
from transcribe_ui.gui.selection.viewmodel import NewJobModel
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.selection.tree import ScanIndex

# An index the model is asked about.
type ModelIndex = QModelIndex | QPersistentModelIndex

# The invalid index: the parent of the top level (the default argument of Qt).
TOP_LEVEL = QModelIndex()
# The role of a cell's text.
DISPLAY_ROLE = Qt.ItemDataRole.DisplayRole
# The role of a cell's tick box.
CHECK_ROLE = Qt.ItemDataRole.CheckStateRole


def check_state(mark: CheckMark) -> Qt.CheckState:
    """Return Qt's tick box state of a row's mark."""
    if mark == CHECKED:
        return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked if mark == PARTIAL else Qt.CheckState.Unchecked


def column_titles() -> tuple[str, ...]:
    """Return the titles of the tree's columns."""
    titles = (tr("Name"), tr("Subtitles it has"), tr("Kind"))
    return (*titles, tr("Size"), tr("Transcribed into"))


def _cell_value(model: NewJobModel, scan_index: "ScanIndex", node: int, column: int, role: int) -> object:
    """Return what a cell shows in one of Qt's roles (None: nothing)."""
    if role == DISPLAY_ROLE:
        return cell_text(model, scan_index, node, column)
    if role == Qt.ItemDataRole.ToolTipRole:
        return cell_tooltip(scan_index, node, column)
    if role == CHECK_ROLE:
        ticked = column == NAME_COLUMN
        return check_state(row_mark(model, scan_index, node)) if ticked else None
    if role == Qt.ItemDataRole.ForegroundRole and row_dimmed(model, scan_index, node):
        return QGuiApplication.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    return None


class _TreeShape(QAbstractItemModel):
    """The rows and parents of the scan's tree (structure only)."""

    def __init__(self, parent: QObject | None = None) -> None:
        """Start without a scan."""
        super().__init__(parent)
        self.scan_index: ScanIndex | None = None

    @override
    def index(self, row: int, column: int, /, parent: ModelIndex = TOP_LEVEL) -> QModelIndex:
        """Return the index of a row under `parent`."""
        scan_index = self.scan_index
        if scan_index is None or not self.hasIndex(row, column, parent):
            return QModelIndex()
        holder = parent.internalId() if parent.isValid() else None
        return self.createIndex(row, column, scan_index.child(holder, row))

    @overload
    def parent(self, /) -> QObject | None: ...

    @overload
    def parent(self, child: ModelIndex, /) -> QModelIndex: ...

    @override
    def parent(self, child: ModelIndex | None = None, /) -> QObject | QModelIndex | None:
        """Return the folder row holding `child` (without `child`: the QObject parent)."""
        if child is None:
            return super().parent()
        scan_index = self.scan_index
        if scan_index is None or not child.isValid():
            return QModelIndex()
        holder = scan_index.parent(child.internalId())
        if holder is None:
            return QModelIndex()
        return self.createIndex(scan_index.row(holder), 0, holder)

    @override
    def rowCount(self, /, parent: ModelIndex = TOP_LEVEL) -> int:
        """Return the rows under `parent` (the top level holds the scan root)."""
        scan_index = self.scan_index
        if scan_index is None or parent.column() > 0:
            return 0
        return scan_index.child_count(parent.internalId() if parent.isValid() else None)

    @override
    def columnCount(self, /, parent: ModelIndex = TOP_LEVEL) -> int:
        """Return the columns: name, subtitles, kind, size, target languages."""
        return COLUMN_COUNT

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, /, role: int = DISPLAY_ROLE) -> object:
        """Return the column titles."""
        horizontal = orientation == Qt.Orientation.Horizontal
        if horizontal and role == DISPLAY_ROLE and 0 <= section < COLUMN_COUNT:
            return column_titles()[section]
        return None


class SelectionTreeModel(_TreeShape):
    """The tree of the scan on screen: names, subtitles, kinds, sizes, tick boxes from the selection."""

    def __init__(self, model: NewJobModel, parent: QObject | None = None) -> None:
        """Bind to the New job page's view-model."""
        super().__init__(parent)
        self._model = model
        self._scan_version = -1
        self._marks: tuple[int, int, int] = (-1, -1, -1)
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Reset for a new scan; tell the view about changed cells after a preview, a match or new languages."""
        model = self._model
        scan = model.scan
        marks = (model.ticks.version, model.filters.version, model.targets.version)
        if scan.version != self._scan_version:
            self.beginResetModel()
            self._scan_version = scan.version
            self._marks = marks
            self.scan_index = None if scan.scanned is None else scan.scanned.index
            self.endResetModel()
        elif marks != self._marks:
            self._marks = marks
            _cells_changed(self)

    @override
    def data(self, index: ModelIndex, /, role: int = DISPLAY_ROLE) -> object:
        """Return a cell's text, tick box, tooltip or dimmed colour."""
        scan_index = self.scan_index
        if scan_index is None or not index.isValid():
            return None
        return _cell_value(self._model, scan_index, index.internalId(), index.column(), role)

    @override
    def flags(self, index: ModelIndex, /) -> Qt.ItemFlag:
        """Return a cell's flags: every row can be selected; the name cell has the tick box."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        shown = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return shown | Qt.ItemFlag.ItemIsUserCheckable if index.column() == NAME_COLUMN else shown

    @override
    def setData(self, index: ModelIndex, value: object, /, role: int = Qt.ItemDataRole.EditRole) -> bool:
        """Tick or untick a row: the view-model appends the operation (the tick boxes follow its preview)."""
        scan_index = self.scan_index
        ticked = role == CHECK_ROLE and index.column() == NAME_COLUMN
        if scan_index is None or not index.isValid() or not ticked:
            return False
        tick_row(self._model, scan_index, index.internalId())
        return True


def _cells_changed(model: SelectionTreeModel) -> None:
    """Tell the view that every cell may have changed, one folder's rows at a time (Qt's ranges share a parent)."""
    scan_index = model.scan_index
    if scan_index is None or not scan_index.folder_count:
        return
    last = COLUMN_COUNT - 1
    root = model.index(0, 0)
    model.dataChanged.emit(root, root.siblingAtColumn(last))
    for folder, rows in enumerate(scan_index.child_folders):
        count = len(rows) + scan_index.file_counts[folder]
        if count:
            holder = model.createIndex(scan_index.row(folder), 0, folder)
            first = model.index(0, 0, holder)
            final = model.index(count - 1, last, holder)
            model.dataChanged.emit(first, final)
