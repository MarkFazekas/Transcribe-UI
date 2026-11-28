"""The start dialog (step 3 of a job): the plan, the wait for a machine, test mode, Start (§3.1 "GUI").

Binds to `StartModel` and decides nothing: the plan, its jobs (another computer's with "Delete
from cloud"), the waits offered and whether Start applies are display data; an error shows in
the dialog with its fix and Retry.
"""

from collections.abc import Callable
from functools import partial
from typing import Final, override

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transcribe_ui.batches.responses import BatchStage
from transcribe_ui.gui.cloud.plan_display import PlanBatchRow, PlanView
from transcribe_ui.gui.cloud.sections import CheckBanner, OnFix
from transcribe_ui.gui.cloud.start import StartModel
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.feedback import Fix
from transcribe_ui.gui.jobs.widgets import fill_table, row_table
from transcribe_ui.gui.steps.view import StepHeader
from transcribe_ui.i18n.catalog import tr

# The stage the step header of the dialog shows (step 3).
TRANSCRIPTION_STAGE: Final[BatchStage] = "transcription"

# Asks to delete another computer's job from the cloud (its batch id).
type DeleteUnknown = Callable[[str], object]


def _batch_columns() -> list[str]:
    return [
        tr("Job"),
        tr("From"),
        tr("Files"),
        tr("Transcriptions"),
        tr("Audio"),
        "",
    ]


def _batch_cells(row: PlanBatchRow) -> list[str]:
    return [row.batch_id, row.origin, row.files, row.transcriptions, row.audio, ""]


def _phase_text(phase: str) -> str:
    texts = {
        "planning": tr("Checking what is waiting for transcription..."),
        "launching": tr("Starting the cloud machine... You can close this window; the Cloud page shows the run."),
    }
    return texts.get(phase, "")


def _summary(view: PlanView) -> str:
    if view.empty:
        return tr("Nothing is waiting for transcription: upload a job first.")
    return view.summary


def _cost(view: PlanView) -> str:
    shown = [view.cost, view.time_limit]
    if view.price_note:
        shown.insert(1, view.price_note)
    return " ".join(shown)


class PlanSection(QWidget):
    """The plan: what is waiting, the cost and the time limit, one row per job, the objects skipped."""

    def __init__(self, on_delete_unknown: DeleteUnknown, parent: QWidget) -> None:
        """Start empty; "Delete from cloud" on another computer's job calls `on_delete_unknown`."""
        super().__init__(parent)
        self._on_delete_unknown = on_delete_unknown
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = wrapped_label("", self)
        self._cost = wrapped_label("", self)
        self.table = row_table(_batch_columns(), self)
        self._skipped = wrapped_label("", self)
        for widget in (self.summary, self._cost, self.table, self._skipped):
            layout.addWidget(widget)
        self.delete_buttons: dict[str, QPushButton] = {}

    def show_plan(self, view: PlanView | None) -> None:
        """Show a plan (nothing while none is known)."""
        self.setVisible(view is not None)
        if view is None:
            return
        self.summary.setText(_summary(view))
        self._cost.setText(_cost(view))
        self._fill(view.batches)
        skipped = (tr("The cloud machine skips these objects:"), *view.unreadable)
        self._skipped.setText("\n".join(skipped))
        self._skipped.setVisible(bool(view.unreadable))

    def _fill(self, rows: tuple[PlanBatchRow, ...]) -> None:
        fill_table(self.table, [_batch_cells(row) for row in rows])
        self.delete_buttons = {}
        last = self.table.columnCount() - 1
        for index, row in enumerate(rows):
            if not row.known:
                button = QPushButton(tr("Delete from cloud"), self.table)
                button.clicked.connect(partial(self._on_delete_unknown, row.batch_id))
                self.table.setCellWidget(index, last, button)
                self.delete_buttons[row.batch_id] = button
        self.table.resizeColumnsToContents()


class StartOptions(QWidget):
    """How long Runpod may wait for a free machine, and test mode under "Advanced"."""

    def __init__(self, model: StartModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.wait = QComboBox(self)
        self.wait.activated.connect(self._wait_chosen)
        form.addRow(tr("Wait for a free cloud machine at most"), self.wait)
        self.advanced = QPushButton(tr("Advanced"), self)
        self.advanced.setCheckable(True)
        form.addRow(self.advanced)
        self.test_mode = self._test_mode_box()
        form.addRow(self.test_mode)
        self._choices: tuple[tuple[int, str], ...] = ()

    def show_form(self) -> None:
        """Show the waits offered, the chosen one, and test mode."""
        model = self._model
        form = model.form
        choices = form.wait_choices()
        if choices != self._choices:
            self._choices = choices
            self.wait.clear()
            for seconds, label in choices:
                self.wait.addItem(label, seconds)
        index = self.wait.findData(form.wait_s)
        if index >= 0:
            self.wait.setCurrentIndex(index)
        self.test_mode.setChecked(form.test_mode)
        self.test_mode.setEnabled(model.phase != "launching")

    def _test_mode_box(self) -> QCheckBox:
        box = QCheckBox(tr("Test mode: a 2-hour limit, and the machine stays on until then for SSH"), self)
        box.clicked.connect(self._test_mode_clicked)
        box.setVisible(False)
        self.advanced.toggled.connect(box.setVisible)
        return box

    def _wait_chosen(self, index: int) -> None:
        seconds = self.wait.itemData(index)
        if isinstance(seconds, int):
            self._model.form.choose_wait(seconds)

    def _test_mode_clicked(self, checked: bool) -> None:  # noqa: FBT001 -- a Qt slot gets the state positionally
        self._model.use_test_mode(enabled=checked)


class StartDialog(QDialog):
    """Start transcription: what the cloud machine would do, for how much, and how long it may wait."""

    def __init__(
        self,
        model: StartModel,
        on_delete_unknown: DeleteUnknown,
        on_fix: OnFix,
        parent: QWidget | None = None,
    ) -> None:
        """Bind to `model`; "Delete from cloud" calls `on_delete_unknown`, an error's fix `on_fix`."""
        super().__init__(parent)
        self.model = model
        self._on_fix = on_fix
        self._phase = wrapped_label("", self)
        self.changed_notice = wrapped_label(
            tr("The work changed since you saw it. Check the new plan, then press Start again."),
            self,
        )
        self.plan = PlanSection(on_delete_unknown, self)
        self.options = StartOptions(model, self)
        self.error = CheckBanner(model.retry, self._fix, self)
        self.start_button = QPushButton(tr("Start"), self)
        self._build()

    def refresh(self) -> None:
        """Show the phase, the plan, the choices, the error and Start; close once the run started."""
        model = self.model
        if model.phase == "launched":
            self.accept()
            return
        self._phase.setText(_phase_text(model.phase))
        self._phase.setVisible(bool(self._phase.text()))
        self.changed_notice.setVisible(model.form.plan_changed)
        self.plan.show_plan(model.form.view)
        self.options.show_form()
        self.error.show_error(model.error)
        self.start_button.setEnabled(model.can_start)

    @override
    def done(self, outcome: int) -> None:
        """Close the dialog (Close, Esc, or the run started); a launch in flight still reaches the Cloud page."""
        if self.model.phase != "launched":
            self.model.close()
        super().done(outcome)

    def _build(self) -> None:
        self.setWindowTitle(tr("Start transcription"))
        layout = QVBoxLayout(self)
        steps = StepHeader(self)
        steps.show_stage(TRANSCRIPTION_STAGE)
        intro = wrapped_label(
            tr("One cloud machine transcribes every job that is uploaded and waiting, then stops itself."),
            self,
        )
        notes = (self._phase, self.changed_notice)
        for widget in (steps, intro, *notes, self.plan, self.options, self.error):
            layout.addWidget(widget)
        layout.addWidget(self._buttons())
        self.model.subscribe(self.refresh)
        self.refresh()

    def _buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.addButton(self.start_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.start_button.clicked.connect(self.model.start)
        self.start_button.setDefault(True)
        buttons.rejected.connect(self.reject)
        return buttons

    def _fix(self, fix: Fix) -> None:
        # A fix that opens a page closes the dialog first (it would cover the page); Retry keeps it.
        if fix.kind != "retry":
            self.reject()
        self._on_fix(fix)
