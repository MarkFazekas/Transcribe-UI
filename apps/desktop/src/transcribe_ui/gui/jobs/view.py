"""The Jobs page: the jobs list and the job page (architecture §3.1 "GUI").

Binds the widgets to the view-models of `gui/jobs/viewmodel.py` and decides nothing: rows,
labels, texts and buttons are display data, and a click calls the view-model.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.jobs.constants import CELL_SEPARATOR
from transcribe_ui.gui.jobs.display import JobButton, JobRow, TranscriptionRow
from transcribe_ui.gui.jobs.viewmodel import JobPageModel, JobsListModel
from transcribe_ui.gui.jobs.widgets import ButtonRow, ErrorBanner, fill_table, row_table
from transcribe_ui.gui.steps.view import StepHeader
from transcribe_ui.i18n.catalog import tr

# Column of the job page's table that holds the Retry tick box.
RETRY_COLUMN = 0


def _list_columns() -> list[str]:
    return [
        tr("Created"),
        tr("Job"),
        tr("Step"),
        tr("Status"),
        tr("Files"),
        tr("Transcriptions"),
        tr("Languages"),
        tr("Notes"),
    ]


def _list_cells(row: JobRow) -> list[str]:
    return [
        row.created,
        row.batch_id,
        row.stage_text,
        row.status,
        row.files,
        row.transcriptions,
        row.languages,
        CELL_SEPARATOR.join(row.labels),
    ]


class JobsListView(QWidget):
    """The jobs list: "Show hidden jobs", the table (a double click opens a job), Open, Hide and Show."""

    def __init__(self, model: JobsListModel, parent: QWidget | None = None) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        self.show_hidden = self._hidden_box()
        layout.addWidget(self.show_hidden)
        self.banner = ErrorBanner(model.reload, self)
        layout.addWidget(self.banner)
        self.table = row_table(_list_columns(), self)
        # A double click first selects its row, so it opens the selected job.
        self.table.cellDoubleClicked.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        buttons = self._actions(layout)
        self.open_button = buttons[0]
        self.button_row = buttons[1]
        # The rows the table shows, and the selected job, kept by id across reloads.
        self._rows: tuple[JobRow, ...] = ()
        self._selected: str | None = None
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the model's rows and error; the selected job stays selected (by id) while it is listed."""
        self.banner.show_error(self.model.error)
        rows = self.model.rows
        if rows != self._rows:
            self._rows = rows
            batch_ids = [row.batch_id for row in rows]
            if self._selected not in batch_ids:
                self._selected = None
            with QSignalBlocker(self.table):
                fill_table(self.table, [_list_cells(row) for row in rows])
                self.table.clearSelection()
                if self._selected is not None:
                    self.table.selectRow(batch_ids.index(self._selected))
            self.table.resizeColumnsToContents()
        self._show_buttons()

    def selected(self) -> str | None:
        """Return the selected job's id, or None."""
        return self._selected

    def _hidden_box(self) -> QCheckBox:
        box = QCheckBox(tr("Show hidden jobs"), self)
        box.toggled.connect(self._toggle_hidden)
        return box

    def _actions(self, layout: QVBoxLayout) -> tuple[QPushButton, ButtonRow]:
        actions = QHBoxLayout()
        open_button = QPushButton(tr("Open"), self)
        open_button.clicked.connect(self._open_selected)
        actions.addWidget(open_button)
        button_row = ButtonRow(self)
        actions.addWidget(button_row)
        actions.addStretch()
        layout.addLayout(actions)
        return open_button, button_row

    def _toggle_hidden(self, shown: bool) -> None:  # noqa: FBT001 -- a Qt slot gets the state positionally
        self.model.include_hidden(shown=shown)

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        shown = 0 <= row < len(self._rows)
        chosen = bool(self.table.selectedItems()) and shown
        self._selected = self._rows[row].batch_id if chosen else None
        self._show_buttons()

    def _open_selected(self) -> None:
        batch_id = self.selected()
        if batch_id is not None:
            self.model.open_job(batch_id)

    def _show_buttons(self) -> None:
        batch_id = self.selected()
        self.open_button.setEnabled(batch_id is not None)
        buttons = () if batch_id is None else self.model.buttons(batch_id)
        self.button_row.show_buttons(buttons, partial(self._press, batch_id))

    def _press(self, batch_id: str | None, button: JobButton) -> None:
        if batch_id is not None:
            self.model.press(batch_id, button.key)


class JobHeaderView(QWidget):
    """The job's title, its status and counts, and its labels."""

    def __init__(self, parent: QWidget) -> None:
        """Start empty."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(self)
        layout.addWidget(self.title)
        self.summary = wrapped_label("", self)
        layout.addWidget(self.summary)
        self.labels = wrapped_label("", self)
        layout.addWidget(self.labels)

    def show_job(self, header: JobRow | None, batch_id: str | None) -> None:
        """Show a loaded job, or only the id of one still loading."""
        if header is None:
            self.title.setText(batch_id or "")
            self.summary.setText("")
            self.labels.setText("")
            return
        title = tr("Job {batch_id}, created {created}")
        named = {"batch_id": header.batch_id, "created": header.created}
        self.title.setText(title.format_map(named))
        summary = tr("Status: {status}. Files: {files}. Transcriptions: {transcriptions}. Languages: {languages}.")
        fields = {
            "status": header.status,
            "files": header.files,
            "transcriptions": header.transcriptions,
            "languages": header.languages,
        }
        self.summary.setText(summary.format_map(fields))
        self.labels.setText(CELL_SEPARATOR.join(header.labels))


def _page_columns() -> list[str]:
    return [
        tr("Retry"),
        tr("File"),
        tr("Language"),
        tr("Kind"),
        tr("Status"),
        tr("Reason"),
        tr("File status"),
        tr("Problem"),
        tr("Path"),
    ]


def _transcription_cells(row: TranscriptionRow) -> list[str]:
    return [
        "",
        row.file_name,
        row.language,
        row.kind,
        row.status,
        row.reason,
        row.file_status,
        row.problem,
        row.path,
    ]


class JobPageView(QWidget):
    """One job: All jobs, the step header, the job's header, its transcriptions (Retry tick boxes) and buttons."""

    def __init__(
        self,
        model: JobPageModel,
        on_back: Callable[[], object],
        parent: QWidget | None = None,
    ) -> None:
        """Bind to `model`; "All jobs" calls `on_back`."""
        super().__init__(parent)
        self.model = model
        layout = QVBoxLayout(self)
        back = QPushButton(tr("All jobs"), self)
        back.clicked.connect(on_back)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.steps = StepHeader(self)
        self.header = JobHeaderView(self)
        self.banner = ErrorBanner(model.reload, self)
        self.table = row_table(_page_columns(), self)
        self.table.itemChanged.connect(self._tick_changed)
        self.button_row = ButtonRow(self)
        for widget in (self.steps, self.header, self.banner, self.table, self.button_row):
            layout.addWidget(widget, 1 if widget is self.table else 0)
        # The rows the table shows: a tick changes no row, so the table is not rebuilt under the click.
        self._shown: tuple[TranscriptionRow, ...] | None = None
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the model's job, transcriptions, error and buttons."""
        model = self.model
        header = model.header
        self.steps.show_stage(None if header is None else header.stage)
        self.header.show_job(header, model.batch_id)
        self.banner.show_error(model.error)
        if model.rows != self._shown:
            self._shown = model.rows
            with QSignalBlocker(self.table):
                self._fill_rows()
            self.table.resizeColumnsToContents()
        self.button_row.show_buttons(model.buttons, self._press)

    def _fill_rows(self) -> None:
        rows = self.model.rows
        fill_table(self.table, [_transcription_cells(row) for row in rows])
        ticked = set(self.model.ticked())
        for row_index, row in enumerate(rows):
            if row.flagged:
                self._mark_flagged(row_index)
            if row.retry is not None:
                self._add_tick(row_index, ticked=row.retry in ticked)

    def _mark_flagged(self, row_index: int) -> None:
        for column in range(self.table.columnCount()):
            cell = self.table.item(row_index, column)
            if cell is not None:
                font = cell.font()
                font.setBold(True)
                cell.setFont(font)

    def _add_tick(self, row_index: int, *, ticked: bool) -> None:
        tick = QTableWidgetItem()
        tick.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        tick.setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
        self.table.setItem(row_index, RETRY_COLUMN, tick)

    def _tick_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() != RETRY_COLUMN:
            return
        job = self.model.rows[cell.row()].retry
        if job is not None:
            checked = cell.checkState() == Qt.CheckState.Checked
            self.model.set_ticked(job, ticked=checked)

    def _press(self, button: JobButton) -> None:
        self.model.press(button.key)


class JobsPage(QStackedWidget):
    """The Jobs page: the list, or one job's page."""

    def __init__(self, list_model: JobsListModel, page_model: JobPageModel, parent: QWidget | None = None) -> None:
        """Start on the list."""
        super().__init__(parent)
        self.list_view = JobsListView(list_model, self)
        self.page_view = JobPageView(page_model, self.show_list, self)
        self.addWidget(self.list_view)
        self.addWidget(self.page_view)

    def open_job(self, batch_id: str) -> None:
        """Show a job's page and load it."""
        self.page_view.model.open_batch(batch_id)
        self.setCurrentWidget(self.page_view)

    def show_list(self) -> None:
        """Show the list (the job page leaves its job)."""
        self.page_view.model.close()
        self.setCurrentWidget(self.list_view)
