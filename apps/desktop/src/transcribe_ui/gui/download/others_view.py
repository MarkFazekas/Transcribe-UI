"""The Cloud page's section of other computers' jobs, and its outlet in the window (architecture §3.9).

Binds to `others.OtherJobsModel` and decides nothing: the rows are display data, "Look in the
cloud" and Download call the view-model. The table is a view over a model (`others_table.py`),
so nothing is built per row: one Download acts on the selected job, and a double-click on a
row does the same. A new list keeps the selected job selected while it is still listed.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.download.others import OtherJobsModel
from transcribe_ui.gui.download.others_display import CloudJobRow
from transcribe_ui.gui.download.others_table import OtherJobsTableModel
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


class OtherJobsTable(QTableView):
    """The listed jobs: read-only, one row selected at a time; a new list keeps the selected job."""

    def __init__(self, parent: QWidget) -> None:
        """Start empty."""
        super().__init__(parent)
        self.rows = OtherJobsTableModel(self)
        self.setModel(self.rows)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def selected(self) -> str | None:
        """Return the selected job's batch id, or None."""
        chosen = self.selectionModel().selectedRows()
        if not chosen:
            return None
        return self.rows.rows[chosen[0].row()].batch_id

    def show_rows(self, rows: tuple[CloudJobRow, ...]) -> None:
        """Show `rows`, keeping the selected job selected while it is still listed."""
        kept = self.selected()
        # A reset clears the selection.
        if not self.rows.show_rows(rows):
            return
        row = None if kept is None else self.rows.row_of(kept)
        if row is not None:
            self.selectRow(row)
        self.resizeColumnsToContents()


class OthersWindowOutlet:
    """The section's outlet in the window: its bus and its generic dialogs."""

    def __init__(self, window: "MainWindow") -> None:
        """Emit on `window`'s bus and open its dialogs."""
        self._window = window

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` with `arguments`."""
        getattr(self._window.context.signals, signal_name).emit(*arguments)

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Open the confirmation dialog."""
        self._window.show_confirmation(view, on_confirm)

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Open the error dialog."""
        self._window.show_error(view, retry)


class OtherJobsSection(QGroupBox):
    """Other computers' jobs in the cloud (Look in the cloud), and Download for the selected one."""

    def __init__(self, model: OtherJobsModel, parent: QWidget | None = None) -> None:
        """Bind to `model`."""
        super().__init__(tr("Jobs of other computers"), parent)
        self.model = model
        layout = QVBoxLayout(self)
        intro = tr(
            "Jobs uploaded to the same cloud storage from another computer. Download adds one to this "
            "computer's jobs and downloads its subtitles here.",
        )
        layout.addWidget(wrapped_label(intro, self))
        self.look_button = QPushButton(tr("Look in the cloud"), self)
        self.state = wrapped_label("", self)
        layout.addLayout(self._top_row())
        self.table = OtherJobsTable(self)
        layout.addWidget(self.table)
        self.download_button = QPushButton(tr("Download..."), self)
        layout.addLayout(self._bottom_row())
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the jobs, the list's state and whether Download applies to the selected job."""
        model = self.model
        self.look_button.setEnabled(not model.loading)
        self.state.setText(self._state_text())
        listed = bool(model.rows)
        self.table.setVisible(listed)
        self.download_button.setVisible(listed)
        self.table.show_rows(model.rows)
        self._show_download()

    def _top_row(self) -> QHBoxLayout:
        top = QHBoxLayout()
        self.look_button.clicked.connect(self._look)
        top.addWidget(self.look_button)
        top.addWidget(self.state, 1)
        return top

    def _bottom_row(self) -> QHBoxLayout:
        # Adding a job to this computer's jobs is never the default of a key press.
        self.download_button.setAutoDefault(False)
        self.download_button.clicked.connect(self._download_selected)
        # A double-click selects the row first, so it downloads that one.
        self.table.doubleClicked.connect(self._download_selected)
        self.table.selectionModel().selectionChanged.connect(self._show_download)
        bottom = QHBoxLayout()
        bottom.addWidget(self.download_button)
        bottom.addStretch()
        return bottom

    def _state_text(self) -> str:
        model = self.model
        if model.loading:
            return tr("Looking in the cloud...")
        if model.error is not None:
            return model.error.message
        if not model.listed:
            return tr("Not checked yet.")
        return "" if model.rows else tr("The cloud holds no job of another computer.")

    def _show_download(self, *_qt_arguments: object) -> None:
        selected = self.table.selected()
        adopting = selected is not None and self.model.adopting(selected)
        self.download_button.setEnabled(selected is not None and not adopting)
        hint = ""
        if selected is None:
            hint = tr("Select a job in the list first.")
        elif adopting:
            hint = tr("This job is being added to this computer's jobs.")
        self.download_button.setToolTip(hint)

    def _look(self) -> None:
        self.model.look()

    def _download_selected(self, *_qt_arguments: object) -> None:
        selected = self.table.selected()
        if selected is not None and not self.model.adopting(selected):
            self.model.download(selected)
