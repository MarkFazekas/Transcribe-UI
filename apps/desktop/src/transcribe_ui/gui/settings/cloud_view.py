"""The cloud setup section: the Runpod objects' state, Check, and Set up / Update (architecture §3.7)."""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.gui.settings.cloud_setup import CloudObjectRow, CloudSetupViewModel
from transcribe_ui.gui.settings.constants import CLOUD_COLUMNS
from transcribe_ui.gui.settings.widgets import show_text
from transcribe_ui.i18n.catalog import tr

# Shows an error in the generic error dialog.
type ShowError = Callable[[ErrorView], object]


@dataclass(frozen=True)
class CloudControls:
    """The section's state lines, table and buttons."""

    key_label: QLabel
    state_label: QLabel
    table: QTableWidget
    error_label: QLabel
    details_button: QPushButton
    check_button: QPushButton
    setup_button: QPushButton


def _table(parent: QWidget) -> QTableWidget:
    table = QTableWidget(0, CLOUD_COLUMNS, parent)
    headers = [tr("Object"), tr("Name"), tr("State")]
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _key_note() -> str:
    return tr(
        "The cloud setup needs the Runpod API key (Settings, Accounts). Without it, this computer uploads and "
        "downloads, and another computer that has the key starts the transcription.",
    )


def _controls(parent: QWidget) -> CloudControls:
    return CloudControls(
        key_label=wrapped_label(_key_note(), parent),
        state_label=wrapped_label("", parent),
        table=_table(parent),
        error_label=wrapped_label("", parent),
        details_button=QPushButton(tr("Details"), parent),
        check_button=QPushButton(tr("Check"), parent),
        setup_button=QPushButton(tr("Set up"), parent),
    )


def _buttons(controls: CloudControls) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(controls.details_button)
    row.addStretch()
    row.addWidget(controls.check_button)
    row.addWidget(controls.setup_button)
    return row


def _fill(table: QTableWidget, rows: tuple[CloudObjectRow, ...]) -> None:
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column, text in enumerate((row.kind, row.name, row.state)):
            table.setItem(row_index, column, QTableWidgetItem(text))
    table.resizeColumnsToContents()


def _state_text(model: CloudSetupViewModel) -> str:
    status = model.status
    if model.checking:
        return tr("Checking the cloud setup...")
    if model.message:
        return model.message
    if status is None:
        return ""
    if status.ready:
        return tr("The cloud setup is ready.")
    return tr("The cloud setup is incomplete or outdated; run Set up.")


def _setup_label(model: CloudSetupViewModel) -> str:
    """Return "Set up" before the objects exist, "Update" once they all do."""
    status = model.status
    states = [] if status is None else [found.state for found in status.cloud_objects]
    if status is None or "missing" in states:
        return tr("Set up")
    return tr("Update")


class CloudSetupView(QWidget):
    """The cloud setup, bound to a `CloudSetupViewModel`; also the wizard's second step."""

    def __init__(self, model: CloudSetupViewModel, show_error: ShowError, parent: QWidget | None = None) -> None:
        """Build the section and follow `model`; Details shows the error through `show_error`."""
        super().__init__(parent)
        self._model = model
        self._show_error = show_error
        self._rows: tuple[CloudObjectRow, ...] = ()
        self.controls = _controls(self)
        self._lay_out()
        self.controls.check_button.clicked.connect(self._check)
        self.controls.setup_button.clicked.connect(model.set_up)
        self.controls.details_button.clicked.connect(self._details)
        model.subscribe(self._repaint)
        self._repaint()

    def _lay_out(self) -> None:
        controls = self.controls
        layout = QVBoxLayout(self)
        intro = tr(
            "The cloud setup creates the Runpod objects the transcription needs: a volume for result backups, "
            "the R2 secrets and the worker template. Run it again after the R2 values change.",
        )
        layout.addWidget(wrapped_label(intro, self))
        layout.addWidget(controls.key_label)
        layout.addWidget(controls.state_label)
        layout.addWidget(controls.table, 1)
        layout.addWidget(controls.error_label)
        layout.addStretch()
        layout.addLayout(_buttons(controls))

    def _check(self) -> None:
        self._model.check()

    def _details(self) -> None:
        if self._model.error is not None:
            self._show_error(self._model.error)

    def _repaint(self) -> None:
        model = self._model
        controls = self.controls
        rows = model.rows()
        if rows != self._rows:
            self._rows = rows
            _fill(controls.table, rows)
        controls.key_label.setVisible(not model.key_set)
        controls.table.setVisible(bool(rows))
        show_text(controls.state_label, _state_text(model))
        error = model.error
        show_text(controls.error_label, "" if error is None else error.message)
        controls.details_button.setVisible(model.error is not None)
        idle = model.key_set and not (model.checking or model.running)
        controls.check_button.setEnabled(idle)
        controls.setup_button.setEnabled(idle)
        controls.setup_button.setText(_setup_label(model))
