"""The activity panel: one row per long call with its progress, Cancel, Resume and Dismiss.

Binds to the view-model of `gui/activity.py`. Progress events arrive one by one and unchanged;
the panel repaints at most every `PROGRESS_REPAINT_MS` instead of once per event.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.activity import Activity, ActivityEntry
from transcribe_ui.gui.constants import PROGRESS_REPAINT_MS
from transcribe_ui.gui.feedback import ErrorView

# Shows the error of a failed entry (the generic error dialog).
type ShowError = Callable[[ErrorView], object]


def _show_entry_error(show_error: ShowError, entry: ActivityEntry) -> None:
    if entry.error is not None:
        show_error(entry.error)


class ActivityRow(QWidget):
    """One entry: title, status, path and the buttons that apply."""

    def __init__(self, activity: Activity, entry: ActivityEntry, show_error: ShowError, parent: QWidget) -> None:
        """Render `entry` of `activity`."""
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(entry.status, self)
        title = QLabel(entry.title, self)
        path = QLabel(entry.path, self)
        for label in (title, self.status, path):
            self._layout.addWidget(label)
        self._layout.addStretch()
        cancel = partial(activity.cancel, entry)
        self.cancel_button = self._button("Cancel", cancel, visible=entry.can_cancel)
        resume = partial(activity.resume, entry)
        self.resume_button = self._button("Resume", resume, visible=entry.can_resume)
        details = partial(_show_entry_error, show_error, entry)
        self.details_button = self._button("Details", details, visible=entry.error is not None)
        dismiss = partial(activity.dismiss, entry)
        self.dismiss_button = self._button("Dismiss", dismiss, visible=not entry.can_cancel)

    def _button(self, text: str, action: Callable[[], object], *, visible: bool) -> QPushButton:
        button = QPushButton(text, self)
        button.setVisible(visible)
        button.clicked.connect(action)
        self._layout.addWidget(button)
        return button


class ActivityPanel(QWidget):
    """The window-level panel of long calls; hidden while it has none."""

    def __init__(self, activity: Activity, show_error: ShowError, parent: QWidget | None = None) -> None:
        """Bind to `activity`; Details of a failed entry opens `show_error`."""
        super().__init__(parent)
        self._activity = activity
        self._show_error = show_error
        self._layout = QVBoxLayout(self)
        self.rows: list[ActivityRow] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PROGRESS_REPAINT_MS)
        self._timer.timeout.connect(self.repaint_rows)
        activity.subscribe(self._schedule)
        self.repaint_rows()

    def repaint_rows(self) -> None:
        """Rebuild the rows from the view-model."""
        for old_row in self.rows:
            self._layout.removeWidget(old_row)
            old_row.deleteLater()
        self.rows = [self._row(entry) for entry in self._activity.entries]
        for row in self.rows:
            self._layout.addWidget(row)
        self.setVisible(bool(self.rows))

    def _row(self, entry: ActivityEntry) -> ActivityRow:
        return ActivityRow(self._activity, entry, self._show_error, self)

    def _schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
