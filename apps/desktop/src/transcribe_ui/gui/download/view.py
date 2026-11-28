"""The download dialog: step 4 of a job (architecture §3.1 "GUI" "Download").

Non-modal, one per job: the window stays usable, and the download goes on in the activity
panel when the dialog is closed. It binds to the view-model and decides nothing: what it shows
is `state.dialog_data`, repainted at most every `PROGRESS_REPAINT_MS`.
"""

from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QWidget

from transcribe_ui.gui.constants import PROGRESS_REPAINT_MS
from transcribe_ui.gui.download.chooser import FileChooser
from transcribe_ui.gui.download.constants import CUSTOM, DIALOG_MARGINS, DOWNLOAD_STEP
from transcribe_ui.gui.download.panels import CustomPanel, OptionsPanel
from transcribe_ui.gui.download.results import PlanPanel, RunPanel
from transcribe_ui.gui.download.state import dialog_data
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.download.widgets import stacked
from transcribe_ui.gui.steps.view import StepHeader
from transcribe_ui.i18n.catalog import tr


class DownloadDialog(QDialog):
    """One job's download: the step header, the choices, the plan, Download, and the download's progress."""

    def __init__(self, model: DownloadModel, chooser: FileChooser, parent: QWidget | None = None) -> None:
        """Bind to `model`; the file dialogs come from `chooser`."""
        super().__init__(parent)
        self.model = model
        self.setWindowTitle(tr("Download subtitles of job {batch_id}").format(batch_id=model.batch_id))
        self._steps = StepHeader(self)
        self.options = OptionsPanel(model, chooser, self)
        self.custom = CustomPanel(model, chooser, self)
        self.plan_panel = PlanPanel(model, self)
        self.run_panel = RunPanel(model, self)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.download_button = buttons.addButton(tr("Download"), QDialogButtonBox.ButtonRole.ActionRole)
        self.download_button.clicked.connect(model.download)
        buttons.rejected.connect(self.reject)
        widgets = (
            self._steps,
            self.options,
            self.custom,
            self.plan_panel,
            self.run_panel,
            buttons,
        )
        layout = stacked(self, widgets, self.plan_panel)
        layout.setContentsMargins(*DIALOG_MARGINS)
        # No default button: Enter in the output folder only ends the typing.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._timer = self._repaint_timer()
        # The model outlives a dialog deleted with its window: stop telling it about changes.
        self.destroyed.connect(model.subscribe(self._schedule))
        self.refresh()

    def refresh(self) -> None:
        """Show the model's state now."""
        model = self.model
        shown = dialog_data(model)
        self._steps.setVisible(shown.stage is not None)
        self._steps.show_stage(shown.stage, DOWNLOAD_STEP)
        self.options.show_form(model.form, shown.levels)
        self.custom.setVisible(model.form.strategy == CUSTOM)
        self.custom.show_files(shown)
        self.plan_panel.show_plan(shown.plan)
        self.run_panel.show_run(shown.run)
        self.download_button.setEnabled(shown.can_download)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Show the model's state now (a hidden dialog does not repaint)."""
        self.refresh()
        super().showEvent(event)

    def _repaint_timer(self) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(PROGRESS_REPAINT_MS)
        timer.timeout.connect(self.refresh)
        return timer

    def _schedule(self) -> None:
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()
