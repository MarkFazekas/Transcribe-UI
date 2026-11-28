"""The requests the jobs pages send themselves, and the outlet through which they reach the window.

Qt-free. Hide, show, delete and continue to review are sent one at a time per job (a double
click sends once). A confirmation comes from the backend (`delete_batch` asks) and is confirmed
by re-sending the request with each `set_to`; an error opens the window's error dialog; a change
emits `batches_changed`, so every page showing the job reloads.
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.batches.classes import DeleteBatchRequest, SetBatchHiddenRequest, StartReviewRequest
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import (
    ConfirmationView,
    ErrorView,
    confirmation_mismatch,
    confirmation_view,
    confirmed_request,
    error_view,
)
from transcribe_ui.gui.jobs import constants

logger = logging.getLogger(__name__)

# Called with the response of a request that succeeded.
type Succeeded = Callable[[Response], None]


class JobOutlet(Protocol):
    """What the jobs pages tell the window: a bus signal, a confirmation to ask, an error to show."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error of an action; `retry` re-sends it (the `app_busy` fix)."""


class JobCommands:
    """The requests the jobs pages send themselves, one at a time per job: hide, show, delete, continue to review."""

    def __init__(self, executor: Executor, outlet: JobOutlet) -> None:
        """Send through `executor`; confirmations, errors and the follow-up signals go to `outlet`."""
        self._channel = ExclusiveChannel(executor)
        self._outlet = outlet

    def press(self, key: constants.JobButtonKey, batch_id: str) -> None:
        """Send the request of a button the jobs pages handle (hide, show, delete, continue to review)."""
        if key in {"hide", "show"}:
            hidden = SetBatchHiddenRequest(action="set_batch_hidden", batch_id=batch_id, hidden=key == "hide")
            self.send(batch_id, hidden, partial(self._changed, batch_id))
        elif key == "delete":
            deleted = DeleteBatchRequest(action="delete_batch", batch_id=batch_id)
            self.send(batch_id, deleted, partial(self._deleted, batch_id))
        elif key == "continue_review":
            started = StartReviewRequest(action="start_review", batch_id=batch_id)
            self.send(batch_id, started, partial(self._review_started, batch_id))

    def send(self, batch_id: str, request: Request, on_success: Succeeded) -> None:
        """Send a request about a job (refused while the job's previous one is in flight: a double click)."""
        answered = partial(self._answered, batch_id, request, on_success)
        self._channel.send(batch_id, request, answered)

    def _answered(self, batch_id: str, request: Request, on_success: Succeeded, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, batch_id, request, response, on_success)
            self._outlet.confirm(confirmation_view(response), confirmed)
        elif isinstance(response, ErrorResponse):
            resend = partial(self.send, batch_id, request, on_success)
            self._outlet.show_error(error_view(response), resend)
        else:
            on_success(response)

    def _confirmed(self, batch_id: str, request: Request, asked: NeedsConfirmation, on_success: Succeeded) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(confirmation_mismatch(request), None)
            return
        self.send(batch_id, confirmed, on_success)

    def _changed(self, batch_id: str, _response: Response) -> None:
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))

    def _deleted(self, batch_id: str, _response: Response) -> None:
        # Back to the list first, so the closed job page does not reload the deleted job.
        self._outlet.emit(constants.OPEN_JOBS_SIGNAL)
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))

    def _review_started(self, batch_id: str, _response: Response) -> None:
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        self._outlet.emit(constants.OPEN_REVIEW_SIGNAL, batch_id)
