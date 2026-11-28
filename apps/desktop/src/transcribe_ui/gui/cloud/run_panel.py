"""The Cloud page's current run: its state, progress and countdown, Start, Stop or Cancel, Refresh now.

Binds to the view-models and decides nothing: the shown values are the display data of
`gui/cloud/display.py`, the countdown is repainted each second from the backend's
`estimated_finish_at`, and a click calls a view-model or the page's callback.
"""

from collections.abc import Callable
from functools import partial
from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from transcribe_ui.gui.cloud.area import CloudModels
from transcribe_ui.gui.cloud.constants import COUNTDOWN_TICK_MS, PROGRESS_SCALE
from transcribe_ui.gui.cloud.display import RunView, countdown_text, run_view
from transcribe_ui.gui.cloud.key import needs_key_hint
from transcribe_ui.gui.cloud.ports import Clock
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.i18n.catalog import tr

# Opens a job's page (its batch id).
type OpenJob = Callable[[str], object]
# What a button does.
type Action = Callable[[], object]


def open_file(path: Path) -> None:
    """Open a file (a kept worker log) with the system's viewer."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _call(action: Action, *_qt_arguments: object) -> None:
    action()


def push_button(text: str, on_click: Action, parent: QWidget) -> QPushButton:
    """Return a button that calls `on_click` (without Qt's `checked` argument)."""
    button = QPushButton(text, parent)
    button.clicked.connect(partial(_call, on_click))
    return button


def _field_titles() -> dict[str, str]:
    return {
        "run": tr("Run"),
        "created": tr("Started"),
        "stage": tr("Status"),
        "pod": tr("Cloud machine (Runpod)"),
        "worker": tr("Worker"),
        "file": tr("File"),
        "job": tr("Job"),
        "done": tr("Transcriptions done"),
        "remaining": tr("Remaining"),
        "elapsed": tr("Elapsed"),
        "cost": tr("Cost so far"),
        "deadline": tr("Stops at the latest"),
        "updated": tr("Last check"),
        "end": tr("Outcome"),
        "worker_error": tr("Worker error"),
        "stop_error": tr("Stop problem"),
    }


def _job_link(job: str | None) -> str:
    if job is None:
        return ""
    shown = escape(job)
    return f'<a href="{shown}">{shown}</a>'


def _texts(view: RunView) -> dict[str, str]:
    run = view.run_id
    if view.test_mode:
        run = tr("{run} (test mode)").format(run=run)
    ongoing = not view.final
    return {
        "run": run,
        "created": view.created,
        "stage": view.stage_text,
        "pod": view.pod_status,
        "worker": view.worker_state,
        "file": view.current_file,
        "job": _job_link(view.current_job),
        "done": view.done,
        "elapsed": view.elapsed,
        "cost": view.cost,
        "deadline": view.deadline if ongoing else "",
        "updated": view.updated if ongoing else "",
        "end": view.end_reason,
        "worker_error": view.worker_error,
        "stop_error": view.stop_error,
    }


class RunButtons(QWidget):
    """Start transcription, Stop or Cancel (Q34), Refresh now."""

    def __init__(self, models: CloudModels, on_start: Action, parent: QWidget) -> None:
        """Start transcription calls `on_start`; Stop and Refresh now call the view-models."""
        super().__init__(parent)
        self._models = models
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.start_button = push_button(tr("Start transcription..."), on_start, self)
        self.stop_button = push_button(tr("Stop"), models.actions.stop, self)
        self.refresh_button = push_button(tr("Refresh now"), models.poll.refresh_now, self)
        for button in (self.start_button, self.stop_button, self.refresh_button):
            layout.addWidget(button)
        layout.addStretch()

    def show_run(self, view: RunView | None) -> None:
        """Enable and label the buttons for the shown run (Start and Stop need the Runpod key; shown with the reason)."""
        models = self._models
        active = view is not None and not view.final
        missing = models.key.missing
        no_key = needs_key_hint() if missing else ""
        self.start_button.setEnabled(not active and not missing)
        busy = tr("A transcription is running; it ends by itself, or stop it first.") if active else ""
        self.start_button.setToolTip(no_key or busy)
        label = None if view is None else view.stop_label
        self.stop_button.setVisible(label is not None)
        self.stop_button.setText(label or "")
        self.stop_button.setEnabled(not models.actions.stopping and not missing)
        self.stop_button.setToolTip(no_key)
        self.refresh_button.setEnabled(not models.poll.checking)


class RunFields(QWidget):
    """The run's values, one row each (rows without a value are hidden), the progress bar, the worker log."""

    def __init__(self, on_open_job: OpenJob, parent: QWidget) -> None:
        """Start empty; a click on the job's link calls `on_open_job`."""
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self.labels: dict[str, QLabel] = {}
        for key, title in _field_titles().items():
            self.labels[key] = wrapped_label("", self)
            self._form.addRow(title, self.labels[key])
        self.labels["job"].linkActivated.connect(on_open_job)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, PROGRESS_SCALE)
        self._form.addRow(tr("Progress"), self.progress)
        self.log_button = push_button(tr("Open the worker log"), self._open_log, self)
        self._form.addRow(self.log_button)
        self._view: RunView | None = None

    def show_run(self, view: RunView | None) -> None:
        """Show the run's values (every row hidden without a run)."""
        self._view = view
        texts = {} if view is None else _texts(view)
        for key, label in self.labels.items():
            if key != "remaining":
                self._show(label, texts.get(key, ""))
        progress = None if view is None or view.final else view.progress
        self._form.setRowVisible(self.progress, progress is not None)
        if view is not None and progress is not None:
            self.progress.setValue(progress)
            self.progress.setFormat(view.progress_text)
        log = None if view is None else view.worker_log
        self._form.setRowVisible(self.log_button, log is not None)

    def show_countdown(self, text: str) -> None:
        """Show the remaining time (hidden when unknown)."""
        self._show(self.labels["remaining"], text)

    def _show(self, label: QLabel, text: str) -> None:
        label.setText(text)
        self._form.setRowVisible(label, bool(text))

    def _open_log(self) -> None:
        view = self._view
        if view is not None and view.worker_log is not None:
            open_file(view.worker_log)


class RunPanel(QGroupBox):
    """The current run (or the last one with its outcome), the state of the checks, and the buttons."""

    def __init__(
        self,
        models: CloudModels,
        clock: Clock,
        on_start: Action,
        on_open_job: OpenJob,
        parent: QWidget | None = None,
    ) -> None:
        """Bind to the view-models; Start transcription calls `on_start`, a job's link `on_open_job`."""
        super().__init__(tr("Transcription in the cloud"), parent)
        self._models = models
        self._clock = clock
        self.buttons = RunButtons(models, on_start, self)
        self.note = wrapped_label("", self)
        self._empty = wrapped_label(
            tr("No transcription has run yet. Start transcription sends every uploaded job to a cloud machine."),
            self,
        )
        self.fields = RunFields(on_open_job, self)
        self.countdown = QTimer(self)
        self._view: RunView | None = None
        self._build()

    def refresh(self) -> None:
        """Show the current run, the checks' state and the buttons."""
        runs = self._models.runs
        current = runs.current
        self._view = None if current is None else run_view(current)
        self._empty.setVisible(current is None and runs.error is None)
        error = runs.error
        note = self._models.poll.note()
        self.note.setText(note if error is None else error.message)
        self.fields.show_run(self._view)
        self.buttons.show_run(self._view)
        self.show_countdown()

    def show_countdown(self) -> None:
        """Repaint the remaining time; the timer runs only while there is one to count down."""
        finish = None if self._view is None else self._view.finish_at
        self.fields.show_countdown(countdown_text(finish, self._clock()))
        if finish is None:
            self.countdown.stop()
        elif not self.countdown.isActive():
            self.countdown.start()

    def _build(self) -> None:
        column = QFormLayout(self)
        for widget in (self.buttons, self.note, self._empty, self.fields):
            column.addRow(widget)
        self.countdown.setInterval(COUNTDOWN_TICK_MS)
        self.countdown.timeout.connect(self.show_countdown)
        models = self._models
        for model in (models.runs, models.poll, models.actions, models.key):
            model.subscribe(self.refresh)
        self.refresh()
