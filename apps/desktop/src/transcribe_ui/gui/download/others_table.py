"""The Qt model of the list of other computers' jobs (architecture §3.9 "Adoption").

The list has no row limit, so nothing is built per row: the model reads the display rows of
`others.OtherJobsModel` and a new list resets it. Rows are read-only and selectable; the
section's single Download acts on the selected row.
"""

from typing import override

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from transcribe_ui.gui.download.others_display import CloudJobRow
from transcribe_ui.i18n.catalog import tr

# A model index as Qt passes it.
type AnyIndex = QModelIndex | QPersistentModelIndex

# The parent of every row (the table has no rows below rows).
TOP_LEVEL = QModelIndex()


def _columns() -> list[str]:
    return [
        tr("Job"),
        tr("Files"),
        tr("Audio"),
        tr("Status"),
    ]


def _cells(row: CloudJobRow) -> tuple[str, ...]:
    return (row.batch_id, row.files, row.audio, row.state)


class OtherJobsTableModel(QAbstractTableModel):
    """The listed jobs of other computers, one row each."""

    def __init__(self, parent: QObject | None = None) -> None:
        """Start empty."""
        super().__init__(parent)
        self.rows: tuple[CloudJobRow, ...] = ()

    def show_rows(self, rows: tuple[CloudJobRow, ...]) -> bool:
        """Show `rows`; return whether they changed (a reset only then)."""
        if rows == self.rows:
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True

    def row_of(self, batch_id: str) -> int | None:
        """Return the row of a job, or None when it is not listed."""
        listed = [row.batch_id for row in self.rows]
        return listed.index(batch_id) if batch_id in listed else None

    @override
    def rowCount(self, /, parent: AnyIndex = TOP_LEVEL) -> int:
        """Return the number of rows (none below a row)."""
        return 0 if parent.isValid() else len(self.rows)

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
        """Return a cell's text."""
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        return _cells(self.rows[index.row()])[index.column()]
