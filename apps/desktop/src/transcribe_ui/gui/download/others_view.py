"""The Cloud page's section of other computers' jobs, and its outlet in the window (architecture §3.9).

Binds to `others.OtherJobsModel` and decides nothing: the rows are display data, "Look in the
cloud" and each job's Download call the view-model. The table and its buttons are rebuilt only
when a row changed, never inside a button's own click (answers arrive later, on the GUI thread).
"""

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.download.others import OtherJobsModel
from transcribe_ui.gui.download.others_display import CloudJobRow
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView
from transcribe_ui.gui.jobs.widgets import fill_table, row_table
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.shell import MainWindow


def _columns() -> list[str]:
    return [
        tr("Job"),
        tr("Files"),
        tr("Audio"),
        tr("Status"),
        "",
    ]


def _cells(row: CloudJobRow) -> list[str]:
    return [row.batch_id, row.files, row.audio, row.state, ""]


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
    """Other computers' jobs in the cloud (Look in the cloud), each with Download."""

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
        self.table = row_table(_columns(), self)
        layout.addWidget(self.table)
        self._shown: tuple[CloudJobRow, ...] | None = None
        self.download_buttons: dict[str, QPushButton] = {}
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the jobs, the list's state and the Download buttons."""
        model = self.model
        self.look_button.setEnabled(not model.loading)
        self.state.setText(self._state_text())
        self.table.setVisible(bool(model.rows))
        if model.rows != self._shown:
            self._shown = model.rows
            self._fill()
        for batch_id, button in self.download_buttons.items():
            button.setEnabled(not model.adopting(batch_id))

    def _top_row(self) -> QHBoxLayout:
        top = QHBoxLayout()
        self.look_button.clicked.connect(self._look)
        top.addWidget(self.look_button)
        top.addWidget(self.state, 1)
        return top

    def _state_text(self) -> str:
        model = self.model
        if model.loading:
            return tr("Looking in the cloud...")
        if model.error is not None:
            return model.error.message
        if not model.listed:
            return tr("Not looked yet.")
        return "" if model.rows else tr("The cloud holds no job of another computer.")

    def _fill(self) -> None:
        rows = self.model.rows
        fill_table(self.table, [_cells(row) for row in rows])
        self.download_buttons = {}
        last = self.table.columnCount() - 1
        for index, row in enumerate(rows):
            button = QPushButton(tr("Download..."), self.table)
            button.setAutoDefault(False)
            button.clicked.connect(partial(self._download, row.batch_id))
            self.table.setCellWidget(index, last, button)
            self.download_buttons[row.batch_id] = button
        self.table.resizeColumnsToContents()

    def _look(self) -> None:
        self.model.look()

    def _download(self, batch_id: str, *_qt_arguments: object) -> None:
        self.model.download(batch_id)
