"""The Cloud page's other sections: the cloud machines list, the run history and the check banners.

Bind to the view-models and decide nothing: rows, texts and which machine has a stop button are
display data; a click calls a view-model or the page's callback.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.cloud.display import HistoryRow, history_row
from transcribe_ui.gui.cloud.machines import MachinesModel
from transcribe_ui.gui.cloud.plan_display import MachineRow
from transcribe_ui.gui.cloud.run_panel import open_file
from transcribe_ui.gui.cloud.runs import RunList
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import ErrorView, Fix
from transcribe_ui.gui.jobs.widgets import fill_table, row_table
from transcribe_ui.i18n.catalog import tr

# What a button does.
type Action = Callable[[], object]
# Carries out an error's fix.
type OnFix = Callable[[Fix], object]


def _machine_columns() -> list[str]:
    return [
        tr("Kind"),
        tr("Name"),
        tr("Status (Runpod)"),
        tr("Run"),
        tr("Role"),
        "",
    ]


def _machine_cells(row: MachineRow) -> list[str]:
    return [row.kind, row.name, row.status, row.run_id, row.role, ""]


def _history_columns() -> list[str]:
    return [
        tr("Started"),
        tr("Run"),
        tr("Status"),
        tr("Outcome"),
        tr("Elapsed"),
        tr("Cost"),
        tr("Test mode"),
        "",
    ]


def _history_cells(row: HistoryRow) -> list[str]:
    return [
        row.created,
        row.run_id,
        row.stage_text,
        row.end_reason,
        row.elapsed,
        row.cost,
        row.test_mode,
        "",
    ]


class MachinesSection(QGroupBox):
    """The app's live cloud machines (Check), each straggler or overdue one with Terminate or Cancel."""

    def __init__(self, model: MachinesModel, parent: QWidget | None = None) -> None:
        """Bind to `model`."""
        super().__init__(tr("Cloud machines"), parent)
        self.model = model
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.check_button = QPushButton(tr("Check"), self)
        self.check_button.clicked.connect(self._check)
        top.addWidget(self.check_button)
        self.state = wrapped_label("", self)
        top.addWidget(self.state, 1)
        layout.addLayout(top)
        self.table = row_table(_machine_columns(), self)
        layout.addWidget(self.table)
        self._shown: tuple[MachineRow, ...] | None = None
        self.stop_buttons: dict[str, QPushButton] = {}
        model.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the machines, the check's state and the stop buttons."""
        model = self.model
        self.check_button.setEnabled(not model.checking)
        self.state.setText(self._state_text())
        if model.rows != self._shown:
            self._shown = model.rows
            self._fill()
        for row in model.rows:
            button = self.stop_buttons.get(row.entry.entry_id)
            if button is not None:
                button.setEnabled(not model.stopping(row))

    def _state_text(self) -> str:
        model = self.model
        if model.checking:
            return tr("Checking the cloud machines...")
        if model.error is not None:
            return model.error.message
        states = (
            (not model.checked, tr("Not checked yet.")),
            (not model.rows, tr("No cloud machine of the app is running.")),
            (model.attention, tr("A machine is left over or past its time limit: stop it, so it costs no more.")),
        )
        return next((text for applies, text in states if applies), "")

    def _fill(self) -> None:
        rows = self.model.rows
        fill_table(self.table, [_machine_cells(row) for row in rows])
        self.stop_buttons = {}
        last = self.table.columnCount() - 1
        for index, row in enumerate(rows):
            if row.action_label is not None:
                button = QPushButton(row.action_label, self.table)
                button.clicked.connect(partial(self.model.stop, row))
                self.table.setCellWidget(index, last, button)
                self.stop_buttons[row.entry.entry_id] = button
        self.table.resizeColumnsToContents()

    def _check(self) -> None:
        self.model.check()


class HistorySection(QGroupBox):
    """The latest runs: when, outcome, elapsed time and cost (to the end, else the last check), the worker log."""

    def __init__(self, runs: RunList, parent: QWidget | None = None) -> None:
        """Bind to `runs`."""
        super().__init__(tr("Recent runs"), parent)
        self.runs = runs
        layout = QVBoxLayout(self)
        self.table = row_table(_history_columns(), self)
        layout.addWidget(self.table)
        self.rows: tuple[HistoryRow, ...] = ()
        runs.subscribe(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """Show the runs (the table is rebuilt only when a row changed)."""
        rows = tuple(history_row(progress) for progress in self.runs.runs)
        if rows == self.rows:
            return
        self.rows = rows
        fill_table(self.table, [_history_cells(row) for row in rows])
        last = self.table.columnCount() - 1
        for index, row in enumerate(rows):
            if row.worker_log is not None:
                button = QPushButton(tr("Worker log"), self.table)
                button.clicked.connect(partial(open_file, row.worker_log))
                self.table.setCellWidget(index, last, button)
        self.table.resizeColumnsToContents()


class CheckBanner(QWidget):
    """A failed check of the cloud (start-up or poll) with its fix and Retry; hidden while there is none."""

    def __init__(self, on_retry: Action, on_fix: OnFix, parent: QWidget | None = None) -> None:
        """Hidden until an error is shown; Retry calls `on_retry`, the fix `on_fix`."""
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = wrapped_label("", self)
        layout.addWidget(self.message, 1)
        self.fix_button = QPushButton(self)
        self.fix_button.clicked.connect(self._fix)
        layout.addWidget(self.fix_button)
        self.retry_button = QPushButton(tr("Retry"), self)
        self.retry_button.clicked.connect(self._retry)
        layout.addWidget(self.retry_button)
        self._on_retry = on_retry
        self._on_fix = on_fix
        self._error: ErrorView | None = None
        self.setVisible(False)

    def show_error(self, error: ErrorView | None) -> None:
        """Show the error's text and fix, or hide the banner."""
        self._error = error
        self.setVisible(error is not None)
        if error is None:
            return
        self.message.setText(error.message)
        fix = error.fix
        self.fix_button.setVisible(fix is not None and fix.kind != "retry")
        if fix is not None:
            self.fix_button.setText(fix.label)

    def _fix(self) -> None:
        fix = None if self._error is None else self._error.fix
        if fix is not None:
            self._on_fix(fix)

    def _retry(self) -> None:
        self._on_retry()
        fix = None if self._error is None else self._error.fix
        if fix is not None and fix.kind == "retry":
            # `app_busy`: the writer lock is probed too, so its notice learns that the other process let go.
            self._on_fix(fix)
