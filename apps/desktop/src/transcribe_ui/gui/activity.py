"""The activity panel's view-model: the window's long calls with progress, Cancel and Resume (§3.1 "GUI").

Qt-free. Areas start their long requests here instead of on the executor: each call shows its
current stage, `completed`/`total` and path; Cancel sets the call's cancel flag; the `cancelled`
error is shown as a neutral "Stopped; continue any time" with Resume, which re-sends the same
request (re-sending resumes, §3.1). One call per key runs at a time (`ExclusiveChannel`).
"""

from collections.abc import Hashable
from dataclasses import dataclass, field
from functools import partial

from transcribe_ui.api.classes import ErrorResponse, ProgressEvent, Request, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.constants import ActivityState
from transcribe_ui.gui.executor import CallHandle, DoneCallback, ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.texts import activity_state_text
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.texts import progress_text


def _ignore(_response: Response) -> None:
    """Default `on_done` of an activity nobody waits for."""


@dataclass
class ActivityEntry:
    """One long call in the panel."""

    key: Hashable
    title: str
    request: Request
    # Called with each response of the call (again after a Resume).
    on_done: DoneCallback
    state: ActivityState = "queued"
    last_event: ProgressEvent | None = None
    error: ErrorView | None = None
    call: CallHandle | None = field(default=None, repr=False)

    @property
    def status(self) -> str:
        """Return the entry's status line."""
        if self.state == "failed" and self.error is not None:
            return self.error.message
        if self.state == "running" and self.last_event is not None:
            return progress_text(self.last_event)
        return activity_state_text(self.state)

    @property
    def path(self) -> str:
        """Return the path of the latest progress event, or ""."""
        event = self.last_event
        if event is None or event.path is None:
            return ""
        return str(event.path)

    @property
    def can_cancel(self) -> bool:
        """Return whether Cancel applies (queued or running)."""
        return self.state in {"queued", "running"}

    @property
    def can_resume(self) -> bool:
        """Return whether Resume applies (stopped or failed: re-sending resumes)."""
        return self.state in {"stopped", "failed"}


class Activity(ViewModel):
    """The window's long calls."""

    def __init__(self, executor: Executor) -> None:
        """Send through this executor."""
        super().__init__(executor)
        self._channel = ExclusiveChannel(executor)
        self.entries: list[ActivityEntry] = []

    def busy(self, key: Hashable) -> bool:
        """Return whether the key's call is queued or running."""
        return self._channel.busy(key)

    def start(
        self,
        request: Request,
        title: str,
        on_done: DoneCallback = _ignore,
        key: Hashable | None = None,
    ) -> ActivityEntry | None:
        """Start a long request (key: the request's JSON unless given); None while the key's call runs."""
        entry_key = request.model_dump_json() if key is None else key
        if self._channel.busy(entry_key):
            return None
        self.entries = [entry for entry in self.entries if entry.key != entry_key]
        entry = ActivityEntry(entry_key, title, request, on_done)
        self.entries.append(entry)
        self._send(entry)
        return entry

    def cancel(self, entry: ActivityEntry) -> None:
        """Cancel the entry's call (a queued one never runs; a running one stops between steps)."""
        if entry.call is not None and entry.can_cancel:
            entry.call.cancel()

    def resume(self, entry: ActivityEntry) -> bool:
        """Re-send a stopped or failed entry's request; return whether it was sent."""
        if not entry.can_resume or self._channel.busy(entry.key):
            return False
        entry.state = "queued"
        entry.error = None
        entry.last_event = None
        self._send(entry)
        return True

    def dismiss(self, entry: ActivityEntry) -> None:
        """Remove a completed entry from the panel."""
        if not entry.can_cancel and entry in self.entries:
            self.entries.remove(entry)
            self.changed()

    def _send(self, entry: ActivityEntry) -> None:
        entry.call = self._channel.send(
            entry.key,
            entry.request,
            partial(self._complete, entry),
            partial(self._progressed, entry),
            partial(self._started, entry),
        )
        self.changed()

    def _started(self, entry: ActivityEntry) -> None:
        entry.state = "running"
        self.changed()

    def _progressed(self, entry: ActivityEntry, event: ProgressEvent) -> None:
        entry.last_event = event
        self.changed()

    def _complete(self, entry: ActivityEntry, response: Response) -> None:
        if isinstance(response, ErrorResponse):
            cancelled = response.error.code == ERROR_CANCELLED
            entry.state = "stopped" if cancelled else "failed"
            entry.error = None if cancelled else error_view(response)
        else:
            entry.state = "finished"
        self.changed()
        entry.on_done(response)
