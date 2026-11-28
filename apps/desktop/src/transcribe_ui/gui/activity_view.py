"""The activity panel: one row per long call with its progress, Cancel, Resume and Dismiss.

Binds to the view-model of `gui/activity.py`. Progress events arrive one by one and unchanged;
the panel repaints at most every `PROGRESS_REPAINT_MS` instead of once per event. Each entry
keeps its row: a repaint updates the labels and which buttons show in place, and adds or
removes rows only when the entries change, so a click during progress is never lost.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transcribe_ui.gui.activity import Activity, ActivityEntry
from transcribe_ui.gui.constants import PROGRESS_REPAINT_MS
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.i18n.catalog import tr

# Shows the error of a failed entry (the generic error dialog) with the re-send its Retry uses.
type ShowError = Callable[[ErrorView, Callable[[], object]], object]


def _show_entry_error(show_error: ShowError, activity: Activity, entry: ActivityEntry) -> None:
    if entry.error is not None:
        # Retry (`app_busy`) resumes the entry: re-sending its request is the retry.
        show_error(entry.error, partial(activity.resume, entry))


class ActivityRow(QWidget):
    """One entry: title, status, path and the buttons that apply now."""

    def __init__(self, activity: Activity, entry: ActivityEntry, show_error: ShowError, parent: QWidget) -> None:
        """Render `entry` of `activity`; `refresh` updates the row in place."""
        super().__init__(parent)
        self.entry = entry
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(QLabel(entry.title, self))
        self.status = QLabel(self)
        self._layout.addWidget(self.status)
        self._path = QLabel(self)
        self._layout.addWidget(self._path)
        self._layout.addStretch()
        cancel = partial(activity.cancel, entry)
        self.cancel_button = self._button(tr("Cancel"), cancel)
        resume = partial(activity.resume, entry)
        self.resume_button = self._button(tr("Resume"), resume)
        show_details = partial(_show_entry_error, show_error, activity, entry)
        self.details_button = self._button(tr("Details"), show_details)
        dismiss = partial(activity.dismiss, entry)
        self.dismiss_button = self._button(tr("Dismiss"), dismiss)
        self.refresh()

    def refresh(self) -> None:
        """Show the entry's current status, path and buttons (the widgets stay the same)."""
        entry = self.entry
        self.status.setText(entry.status)
        self._path.setText(entry.path)
        self.cancel_button.setVisible(entry.can_cancel)
        self.resume_button.setVisible(entry.can_resume)
        self.details_button.setVisible(entry.error is not None)
        self.dismiss_button.setVisible(not entry.can_cancel)

    def _button(self, text: str, action: Callable[[], object]) -> QPushButton:
        button = QPushButton(text, self)
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
        """Match the rows to the entries (keeping existing rows), then refresh each in place.

        Entries only leave the list or join it at the end, so kept rows stay in order and new
        rows are appended.
        """
        kept = {id(row.entry): row for row in self.rows}
        rows: list[ActivityRow] = []
        for entry in self._activity.entries:
            row = kept.pop(id(entry), None)
            if row is None:
                row = self._row(entry)
                self._layout.addWidget(row)
            rows.append(row)
        for gone in kept.values():
            self._layout.removeWidget(gone)
            gone.deleteLater()
        for row in rows:
            row.refresh()
        self.rows = rows
        self.setVisible(bool(rows))

    def _row(self, entry: ActivityEntry) -> ActivityRow:
        return ActivityRow(self._activity, entry, self._show_error, self)

    def _schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
