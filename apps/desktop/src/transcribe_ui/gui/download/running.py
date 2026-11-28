"""A batch's download in the activity panel: start, the overwrite confirmation, Cancel, Resume (§3.1 "GUI").

Qt-free. `run_download` (or the import of a custom plan) runs as an activity of the window, so
it shows in the activity panel and in the dialog alike, one at a time per batch. A
`needs_confirmation` answer (existing files would be replaced) is asked through the outlet and
confirmed by sending the same request again with each `set_to`; the backend decides what needs
it. A cancelled download is "Stopped; continue any time" with Resume, which sends the same
request again (the backend continues where it stopped).
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import NeedsConfirmation, Request, Response
from transcribe_ui.gui.activity import Activity, ActivityEntry
from transcribe_ui.gui.download.constants import ACTIVITY_KEY
from transcribe_ui.gui.feedback import (
    ConfirmationView,
    ErrorView,
    confirmation_mismatch,
    confirmation_view,
    confirmed_request,
)
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.placement.classes import DownloadOutcome, ImportCustomPlanResponse, RunDownloadResponse

logger = logging.getLogger(__name__)

# Told that the download's display data changed.
type Changed = Callable[[], None]
# What of an entry the dialog shows: its state, its last progress event and its error.
type EntryShown = tuple[object, ...]
# Gets the answer of a download that ran (succeeded, stopped or failed; not a confirmation question).
type Finished = Callable[[Response], None]


class DownloadOutlet(Protocol):
    """What the download dialog tells the window: a bus signal, a confirmation to ask, an error to show."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error; `retry` sends the request again (the `app_busy` fix)."""

    def show_dialog(self) -> None:
        """Show the download dialog (again, if it was closed), raised; it may take the focus, depending on the platform."""


def _entry_shown(entry: ActivityEntry) -> EntryShown:
    return (id(entry), entry.state, entry.last_event, entry.error)


class DownloadRun:
    """The batch's download: its activity entry and the outcome of the last download that finished."""

    def __init__(
        self,
        activity: Activity,
        outlet: DownloadOutlet,
        batch_id: str,
        on_change: Changed,
        on_finished: Finished,
    ) -> None:
        """Run as an activity keyed by the batch; tell `on_change` about changes, `on_finished` about answers."""
        self._activity = activity
        self._outlet = outlet
        self._batch_id = batch_id
        self._key = (ACTIVITY_KEY, batch_id)
        self._on_change = on_change
        self._on_finished = on_finished
        # The download's entry in the activity panel (the last one started), or None.
        self.entry: ActivityEntry | None = None
        # What the last download that finished did, or None.
        self.outcome: DownloadOutcome | None = None
        # The entry as last told to `on_change`, so other entries' progress tells nothing.
        self._shown: EntryShown | None = None
        activity.subscribe(self._activity_changed)

    def busy(self) -> bool:
        """Return whether the batch's download is queued or running."""
        return self._activity.busy(self._key)

    def start(self, request: Request) -> bool:
        """Start the download (refused while the batch's download is in flight); return whether it started."""
        title = tr("Download of job {batch_id}").format(batch_id=self._batch_id)
        entry = self._activity.start(request, title, self._done, self._key)
        if entry is None:
            return False
        # An executor that answers at once has run `_done` already; an entry it dismissed (a
        # confirmation asked) is not the download's any more.
        if entry in self._activity.entries:
            self.entry = entry
        self._on_change()
        return True

    def cancel(self) -> None:
        """Cancel the download: it stops after the file being placed; Resume continues."""
        if self.entry is not None:
            self._activity.cancel(self.entry)

    def resume(self) -> None:
        """Send the stopped or failed download again; it continues where it stopped."""
        if self.entry is not None:
            self._activity.resume(self.entry)

    def show_details(self) -> None:
        """Show the error of a failed download (its Retry resumes it)."""
        entry = self.entry
        if entry is not None and entry.error is not None:
            self._outlet.show_error(entry.error, partial(self._activity.resume, entry))

    def _done(self, response: Response) -> None:
        entry = self._answered_entry()
        if isinstance(response, NeedsConfirmation) and entry is not None:
            # Nothing ran: the question replaces the entry, and Confirm starts the confirmed request.
            self._activity.dismiss(entry)
            self.entry = None
            confirmed = partial(self._confirmed, entry.request, response)
            self._outlet.confirm(confirmation_view(response), confirmed)
            self._on_change()
            return
        if isinstance(response, RunDownloadResponse | ImportCustomPlanResponse):
            self.outcome = response.download
        self._on_finished(response)

    def _answered_entry(self) -> ActivityEntry | None:
        """Return the batch's entry in the panel (by its key: `start` may not have returned yet)."""
        listed = [entry for entry in self._activity.entries if entry.key == self._key]
        return listed[0] if listed else None

    def _confirmed(self, request: Request, asked: NeedsConfirmation) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(confirmation_mismatch(request), None)
            return
        self.start(confirmed)

    def _activity_changed(self) -> None:
        shown = None if self.entry is None else _entry_shown(self.entry)
        if shown != self._shown:
            self._shown = shown
            self._on_change()
