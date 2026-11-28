"""The plan and the download parts of the download dialog: the plan table, the download's status and failures."""

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.download.display import FailureRow, PlanRow
from transcribe_ui.gui.download.state import PlanData, RunData
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.download.widgets import show_text, stacked
from transcribe_ui.gui.jobs.widgets import ErrorBanner, fill_table, row_table
from transcribe_ui.i18n.catalog import tr


def _plan_columns() -> list[str]:
    return [
        tr("File"),
        tr("Language"),
        tr("Subtitle file"),
        tr("Folder"),
        tr("What happens"),
        tr("Note"),
    ]


def _plan_cells(row: PlanRow) -> list[str]:
    return [
        row.file_name,
        row.language,
        row.target_name,
        row.target_folder,
        row.outcome_text,
        row.note,
    ]


def _failure_cells(row: FailureRow) -> list[str]:
    return [row.problem, row.target]


def _errors_text(errors: tuple[str, ...]) -> str:
    if not errors:
        return ""
    return "\n".join((tr("Nothing can be downloaded until these are fixed:"), *errors))


class PlanPanel(QWidget):
    """What is missing, the error of a load (with Retry), the plan's errors, its summary and its table."""

    def __init__(self, model: DownloadModel, parent: QWidget) -> None:
        """Bind to `model`; Retry loads again."""
        super().__init__(parent)
        self.hint = wrapped_label("", self)
        self.banner = ErrorBanner(model.reload, self)
        self.errors = wrapped_label("", self)
        self._loading = QLabel(self)
        self.summary = wrapped_label("", self)
        self.table = row_table(_plan_columns(), self)
        widgets = (
            self.hint,
            self.banner,
            self.errors,
            self._loading,
            self.summary,
            self.table,
        )
        stacked(self, widgets, self.table)
        # The rows the table shows, so a repaint that changes none of them keeps the table.
        self._rows: tuple[PlanRow, ...] | None = None

    def show_plan(self, shown: PlanData) -> None:
        """Show the plan part of the dialog."""
        show_text(self.hint, shown.hint)
        self.banner.show_error(shown.error)
        show_text(self.errors, _errors_text(shown.errors))
        show_text(self._loading, shown.loading)
        show_text(self.summary, shown.summary)
        self.table.setVisible(shown.hint is None)
        if shown.rows != self._rows:
            self._rows = shown.rows
            fill_table(self.table, [_plan_cells(row) for row in shown.rows])
            self.table.resizeColumnsToContents()


class RunPanel(QWidget):
    """The download's status with Cancel, Resume and Details, and what the last download did."""

    def __init__(self, model: DownloadModel, parent: QWidget) -> None:
        """Bind to `model`'s download."""
        super().__init__(parent)
        run = model.run
        self.status = QLabel(self)
        self.cancel_button = self._button(tr("Cancel"), run.cancel)
        self.resume_button = self._button(tr("Resume"), run.resume)
        self.details_button = self._button(tr("Details"), run.show_details)
        self.outcome = wrapped_label("", self)
        self.failures = row_table([tr("Could not be placed"), tr("Subtitle file")], self)
        layout = stacked(self, (self.outcome, self.failures))
        layout.insertLayout(0, self._status_row())
        self._failures: tuple[FailureRow, ...] | None = None

    def show_run(self, shown: RunData) -> None:
        """Show the download's status, its buttons, and the last download's outcome and failures."""
        self.status.setText(shown.status)
        self.cancel_button.setVisible(shown.can_cancel)
        self.resume_button.setVisible(shown.can_resume)
        self.details_button.setVisible(shown.has_details)
        show_text(self.outcome, shown.outcome)
        self.failures.setVisible(bool(shown.failures))
        if shown.failures != self._failures:
            self._failures = shown.failures
            fill_table(self.failures, [_failure_cells(row) for row in shown.failures])
            self.failures.resizeColumnsToContents()

    def _status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        for button in (self.cancel_button, self.resume_button, self.details_button):
            row.addWidget(button)
        return row

    def _button(self, text: str, action: Callable[[], object]) -> QPushButton:
        button = QPushButton(text, self)
        button.clicked.connect(action)
        return button
