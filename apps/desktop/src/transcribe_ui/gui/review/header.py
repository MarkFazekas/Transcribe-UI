"""The top of the review page: the job and its step, the check's status, and the filters.

Binds to `ReviewModel` and decides nothing: the step is the job's stage, the status and the
filters' choices are display data, and a change of a filter calls the view-model.
"""

from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.jobs.widgets import ErrorBanner
from transcribe_ui.gui.review.constants import (
    LATER_STAGES,
    OPEN_CLOUD_SIGNAL,
    OPEN_JOB_SIGNAL,
    REVIEW_STEP,
    TRANSCRIPTION_STAGE,
)
from transcribe_ui.gui.review.status import buttons, review_status
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.gui.review.widgets import WheelSafeCombo, action_button
from transcribe_ui.gui.selection.choices import LanguageChoice
from transcribe_ui.gui.selection.widgets import LanguageChecks
from transcribe_ui.gui.steps.view import Navigate, StepHeader
from transcribe_ui.i18n.catalog import tr

if TYPE_CHECKING:
    from transcribe_ui.gui.review.folders import FolderChoice


class ReviewHeader(QWidget):
    """Back to the job, the step header (Next once the upload is done), the title and a load error."""

    def __init__(self, model: ReviewModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        self.back_button = action_button(tr("Back to the job"), self._open_job, self)
        self.steps = StepHeader(self)
        self.title = QLabel(self)
        self.banner = ErrorBanner(model.loader.reload, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        line.addWidget(self.back_button)
        line.addWidget(self.title, 1)
        layout.addLayout(line)
        layout.addWidget(self.steps)
        layout.addWidget(self.banner)

    def refresh(self) -> None:
        """Show the job's id, its stage (Next leads to the step after the upload) and the load error."""
        loader = self._model.loader
        detail = loader.loaded.detail
        stage = None if detail is None else detail.summary.stage
        title = tr("Review and upload: job {batch_id}")
        self.title.setText(title.format(batch_id=loader.batch_id or ""))
        self.steps.show_stage(stage, REVIEW_STEP)
        self.steps.set_navigation(None, self._next(stage))
        self.banner.show_error(loader.loaded.detail_error)

    def _next(self, stage: str | None) -> Navigate:
        if stage == TRANSCRIPTION_STAGE:
            return partial(self._model.outlet.emit, OPEN_CLOUD_SIGNAL)
        return self._open_job if stage in LATER_STAGES else None

    def _open_job(self) -> None:
        batch_id = self._model.loader.batch_id
        if batch_id is not None:
            self._model.outlet.emit(OPEN_JOB_SIGNAL, batch_id)


class ReviewControls(QWidget):
    """The check's status with Stop and Check again, its error, and the language and folder filters."""

    def __init__(self, model: ReviewModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        self.status = wrapped_label("", self)
        self.stop_button = action_button(tr("Stop checking"), model.loader.stop, self)
        self.check_button = action_button(tr("Check again"), self._check_again, self)
        self.banner = ErrorBanner(model.loader.load_review, self)
        self.languages = LanguageChecks(self._filter_languages, self)
        self.folder = WheelSafeCombo(self)
        self.folder.activated.connect(self._filter_folder)
        self._folders: tuple[FolderChoice, ...] = ()
        self._lay_out()

    def refresh(self) -> None:
        """Show the check's state and the filters of the job shown."""
        model = self._model
        loaded = model.loader.loaded
        checking = loaded.state in {"queued", "checking"}
        state = buttons(model)
        self.status.setText(review_status(model))
        self.stop_button.setVisible(checking)
        self.check_button.setVisible(not checking)
        self.check_button.setEnabled(state.check_enabled)
        self.banner.show_error(loaded.review_error)
        self._show_filters(enabled=state.filters_enabled)

    def _lay_out(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        line.addWidget(self.status, 1)
        line.addWidget(self.stop_button)
        line.addWidget(self.check_button)
        layout.addLayout(line)
        layout.addWidget(self.banner)
        layout.addLayout(self._filters_line())

    def _filters_line(self) -> QHBoxLayout:
        filters = QHBoxLayout()
        filters.addWidget(QLabel(tr("Languages:"), self))
        filters.addWidget(self.languages)
        filters.addWidget(QLabel(tr("Folder:"), self))
        filters.addWidget(self.folder)
        filters.addStretch()
        return filters

    def _show_filters(self, *, enabled: bool) -> None:
        loader = self._model.loader
        detail = loader.loaded.detail
        languages = () if detail is None else detail.summary.languages
        offered = tuple(LanguageChoice(code, code) for code in languages)
        self.languages.show_languages(offered, loader.filters.languages)
        hint = "" if enabled else tr("Apply or discard your changes before changing the filters.")
        for widget in (self.languages, self.folder):
            widget.setEnabled(enabled)
            widget.setToolTip(hint)
        choices = self._model.editor.views.folders()
        with QSignalBlocker(self.folder):
            if choices is not self._folders:
                self._folders = choices
                self.folder.clear()
                self.folder.addItems([choice.label for choice in choices])
            paths = [choice.path for choice in choices]
            folder = loader.filters.folder
            index = paths.index(folder) if folder in paths else 0
            self.folder.setCurrentIndex(index)

    def _check_again(self) -> None:
        # The button may still show enabled for up to one repaint after the job became held.
        if buttons(self._model).check_enabled:
            self._model.loader.load_review()

    def _filter_languages(self, languages: tuple[str, ...]) -> None:
        self._model.filter_by(languages, self._model.loader.filters.folder)

    def _filter_folder(self, index: int) -> None:
        if 0 <= index < len(self._folders):
            languages = self._model.loader.filters.languages
            self._model.filter_by(languages, self._folders[index].path)
