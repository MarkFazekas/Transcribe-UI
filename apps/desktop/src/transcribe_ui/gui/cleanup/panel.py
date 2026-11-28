"""The jobs' cloud steps in the activity panel, shared by the retry and the cleanup handlers.

Qt-free. One cloud step per job at a time (a retry and a cleanup of the same job share the
key; a second one is refused with a note). The navigation count tells whether the user went to
another page or job since an action was asked for: an answer from before asks and opens nothing.
"""

from collections.abc import Callable
from typing import Protocol

from transcribe_ui.api.classes import ErrorResponse, Request, Response
from transcribe_ui.gui.activity import Activity, ActivityEntry
from transcribe_ui.gui.cleanup.constants import ACTIVITY_KEY
from transcribe_ui.gui.cleanup.display import CleanupQuestion, busy_text, explained_error
from transcribe_ui.gui.feedback import ErrorView

# Called with the response of a step (again after each Resume in the panel).
type StepDone = Callable[[Response], None]


class CleanupOutlet(Protocol):
    """What the handlers tell the window: bus signals, the cleanup question, errors and short notes."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def ask(self, question: CleanupQuestion, on_confirm: Callable[[], None]) -> None:
        """Ask the cleanup question; its accept button calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error; `retry` re-sends the request (the `app_busy` fix)."""

    def notify(self, text: str) -> None:
        """Show a short note in the status bar."""


def _key(batch_id: str) -> tuple[str, str]:
    return (ACTIVITY_KEY, batch_id)


class JobSteps:
    """Starts a job's cloud step in the activity panel and tracks where the user is."""

    def __init__(self, activity: Activity, outlet: CleanupOutlet) -> None:
        """Run the steps in `activity`; notes go to `outlet`."""
        self.outlet = outlet
        self._activity = activity
        # Counts the user's moves to another page or job.
        self.generation = 0

    def navigated(self, *_arguments: object) -> None:
        """Note that the user went to another page or job."""
        self.generation += 1

    def start(self, batch_id: str, request: Request, title: str, on_done: StepDone) -> None:
        """Start the job's step in the panel, or say that another step of the job still runs."""
        if self._activity.start(request, title, on_done, _key(batch_id)) is None:
            self.outlet.notify(busy_text(batch_id))

    def explain(self, batch_id: str, response: ErrorResponse) -> None:
        """Give the job's failed entry the error with its explanation (the panel's Details shows it)."""
        entry = self._entry(batch_id)
        if entry is not None and entry.error is not None:
            entry.error = explained_error(response)
            self._activity.changed()

    def dismiss(self, batch_id: str) -> None:
        """Take the job's answered entry off the panel."""
        entry = self._entry(batch_id)
        if entry is not None:
            self._activity.dismiss(entry)

    def _entry(self, batch_id: str) -> ActivityEntry | None:
        key = _key(batch_id)
        for entry in self._activity.entries:
            if entry.key == key:
                return entry
        return None
