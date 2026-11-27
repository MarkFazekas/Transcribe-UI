"""The writer-lock notice's view-model (architecture §3.1 "GUI", §3.8 "Writer lock").

Qt-free. The GUI's backend takes the writer lock with its first database request and keeps it
until the window closes. A start-up probe (`list_batches`, read-only) takes it early; while
another process holds it (`app_busy`), the window shows a blocking notice with Retry instead of
the pages that need the database. Every response is observed, so an `app_busy` from any page
shows the notice too.
"""

from collections.abc import Callable
from functools import partial
from typing import Literal

from transcribe_ui.api.classes import ErrorResponse, Request, Response
from transcribe_ui.batches.classes import ListBatchesRequest
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.state.constants import ERROR_APP_BUSY

# Whether this window holds the writer lock: not known yet, held, or held by another process.
type LockState = Literal["unknown", "held", "busy"]


class WriterLockGate(ViewModel):
    """Follows whether the database is usable from this window."""

    def __init__(self, executor: Executor) -> None:
        """Send the probe through this executor."""
        super().__init__(executor)
        self.state: LockState = "unknown"
        # The message of the `app_busy` error while busy.
        self.message = ""
        self._probing = False
        self._on_acquired: list[Callable[[], None]] = []
        # `proceed` of the start-up steps waiting for the probe in flight.
        self._waiting: list[Callable[[], None]] = []

    def when_acquired(self, listener: Callable[[], None]) -> None:
        """Call `listener` each time a Retry got the lock (pages then reload)."""
        self._on_acquired.append(listener)

    def probe(self, proceed: Callable[[], None] | None = None) -> None:
        """Send the probe; `proceed` (of a start-up step) is called when it answered."""
        if proceed is not None:
            self._waiting.append(proceed)
        if self._probing:
            return
        self._probing = True
        request = ListBatchesRequest(action="list_batches")
        self.executor.submit(request, partial(self._probed, request))

    def retry(self) -> None:
        """Try again to take the lock."""
        self.probe()

    def note(self, _request: Request, response: Response) -> None:
        """Observe every response: `app_busy` from any request means another process holds the lock."""
        if isinstance(response, ErrorResponse) and response.error.code == ERROR_APP_BUSY:
            self._set("busy", response.error.message)

    def _probed(self, request: Request, response: Response) -> None:
        self._probing = False
        was_busy = self.state == "busy"
        if isinstance(response, ErrorResponse):
            self.note(request, response)
        else:
            self._set("held", "")
        if was_busy and self.state == "held":
            for listener in list(self._on_acquired):
                listener()
        waiting = self._waiting
        self._waiting = []
        for step_done in waiting:
            step_done()

    def _set(self, state: LockState, message: str) -> None:
        if (state, message) != (self.state, self.message):
            self.state = state
            self.message = message
            self.changed()
