"""The review page: step 2 of a job, review and upload (architecture §3.1 "GUI" "Review and upload").

Binds the widgets to `ReviewModel` and decides nothing: rows, texts and button states are display
data, and a click calls the view-model. The page repaints at most every `PROGRESS_REPAINT_MS`,
and only while it is visible (a hidden page repaints once when it is shown again); it tells the
view-model when it is shown or hidden, so a hidden page sends nothing.
"""

from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from transcribe_ui.gui.constants import PROGRESS_REPAINT_MS
from transcribe_ui.gui.dialogs import wrapped_label
from transcribe_ui.gui.review.constants import TABS, ReviewTab
from transcribe_ui.gui.review.display import tab_title
from transcribe_ui.gui.review.header import ReviewControls, ReviewHeader
from transcribe_ui.gui.review.models import ReviewTableModel
from transcribe_ui.gui.review.status import Buttons, buttons, upload_view
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.gui.review.widgets import ReviewTable, UploadBox, action_button
from transcribe_ui.i18n.catalog import tr


class ReviewTabs(QTabWidget):
    """The embedded-subtitle view (with "Transcribe all shown" and "Extract all shown"), the track view and the files that need attention."""

    def __init__(self, model: ReviewModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        editor = model.editor
        self.tables: dict[ReviewTab, ReviewTable] = {}
        for tab in TABS:
            self.tables[tab] = ReviewTable(ReviewTableModel(model, tab, self), self)
        self.transcribe_button = action_button(tr("Transcribe all shown"), editor.transcribe_shown, self)
        self.extract_button = action_button(tr("Extract all shown"), editor.extract_shown, self)
        self.addTab(self._subtitles_page(), "")
        self.addTab(self.tables["tracks"], "")
        self.addTab(self.tables["attention"], "")

    def refresh(self) -> None:
        """Show each table's rows and count."""
        for index, tab in enumerate(TABS):
            count = self.tables[tab].show_rows()
            self.setTabText(index, tab_title(tab, count))
        enabled = buttons(self._model).choose_enabled
        self.transcribe_button.setEnabled(enabled)
        self.extract_button.setEnabled(enabled)

    def _subtitles_page(self) -> QWidget:
        """Return the embedded-subtitle view with its buttons that choose for every row shown."""
        page = QWidget(self)
        layout = QVBoxLayout(page)
        line = QHBoxLayout()
        line.addWidget(self.transcribe_button)
        line.addWidget(self.extract_button)
        line.addStretch()
        layout.addLayout(line)
        layout.addWidget(self.tables["subtitles"])
        return page


class ActionRow(QWidget):
    """Apply, Discard, Remove from job and Upload, with the hint of what blocks them."""

    def __init__(self, model: ReviewModel, parent: QWidget) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self._model = model
        self.apply_button = action_button(tr("Apply choices"), model.commands.apply, self)
        self.discard_button = action_button(tr("Discard changes"), model.editor.discard, self)
        self.remove_button = action_button(tr("Remove ticked files from the job"), model.commands.remove_ticked, self)
        self.remove_button.setToolTip(tr("Tick the files to remove in the tables above."))
        self.upload_button = action_button(tr("Upload"), model.upload, self)
        self.hint = wrapped_label("", self)
        self._lay_out()

    def refresh(self) -> None:
        """Show the buttons that apply now, enabled while their precondition holds, and the hint."""
        state = buttons(self._model)
        self.apply_button.setVisible(state.apply_shown)
        self.apply_button.setEnabled(state.apply_enabled)
        self.discard_button.setVisible(state.apply_shown)
        self.remove_button.setVisible(state.remove_shown)
        self.remove_button.setEnabled(state.remove_enabled)
        self._show_upload(state)
        self.hint.setText(state.hint)

    def _show_upload(self, state: Buttons) -> None:
        self.upload_button.setVisible(state.upload_shown)
        self.upload_button.setEnabled(state.upload_enabled)
        self.upload_button.setText(state.upload_label or tr("Upload"))

    def _lay_out(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.apply_button, self.discard_button, self.remove_button, self.upload_button):
            layout.addWidget(button)
        layout.addWidget(self.hint, 1)


class ReviewPage(QWidget):
    """The review and upload step of a job: header, check and filters, the views, the actions, the upload."""

    def __init__(self, model: ReviewModel, parent: QWidget | None = None) -> None:
        """Bind to `model`."""
        super().__init__(parent)
        self.model = model
        self.header = ReviewHeader(model, self)
        self.controls = ReviewControls(model, self)
        self.tabs = ReviewTabs(model, self)
        self.action_row = ActionRow(model, self)
        upload_actions = (self._cancel_upload, model.upload, self._upload_details)
        self.upload_box = UploadBox(upload_actions, self)
        layout = QVBoxLayout(self)
        for widget in (self.header, self.controls, self.tabs, self.action_row, self.upload_box):
            layout.addWidget(widget, 1 if widget is self.tabs else 0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PROGRESS_REPAINT_MS)
        self._timer.timeout.connect(self.refresh)
        model.subscribe(self._schedule)
        self.refresh()

    def refresh(self) -> None:
        """Show the model's state (a hidden page waits until it is shown)."""
        if not self.isVisible():
            return
        self.header.refresh()
        self.controls.refresh()
        self.tabs.refresh()
        self.action_row.refresh()
        self.upload_box.show_upload(upload_view(self.model))

    @override
    def showEvent(self, event: QShowEvent) -> None:
        """Load the job if it changed while hidden, and repaint."""
        super().showEvent(event)
        if not event.spontaneous():
            self.model.visibility_changed(shown=True)
            self.refresh()

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        """Stop the review while hidden."""
        super().hideEvent(event)
        if not event.spontaneous():
            self.model.visibility_changed(shown=False)

    def _schedule(self) -> None:
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def _cancel_upload(self) -> None:
        batch_id = self.model.loader.batch_id
        if batch_id is not None:
            self.model.uploads.cancel(batch_id)

    def _upload_details(self) -> None:
        view = upload_view(self.model)
        if view is not None and view.error is not None:
            self.model.outlet.show_error(view.error, self.model.upload)
