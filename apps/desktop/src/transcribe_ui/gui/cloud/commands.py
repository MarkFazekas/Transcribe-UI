"""The Cloud area's own actions: one at a time per key, confirmed by the backend's confirmation.

Qt-free. Stop and Cancel of a run (`cancel_worker`) and Terminate or Cancel of a cloud machine
(`terminate_pod`, `cancel_queued_pod`) each ask the backend first: the `needs_confirmation`
answer is shown by the window's confirmation dialog, and Confirm re-sends the same request with
each confirmation's `set_to`. An error opens the error dialog (Retry re-sends); a success calls
the action's follow-up. A second press while the key's request is in flight sends nothing.
"""

import logging
from collections.abc import Callable, Hashable
from functools import partial

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.api.constants import ERROR_CANCELLED, ERROR_INTERNAL
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import ErrorView, confirmation_view, confirmed_request, error_view

logger = logging.getLogger(__name__)

# Called with the response of a request that succeeded.
type Succeeded = Callable[[Response], None]


def internal_error(request: Request) -> ErrorView:
    """Return the `internal_error` shown when a confirmation does not fit its request (a backend bug)."""
    body = ErrorBody(code=ERROR_INTERNAL, message="The confirmation does not fit the request.")
    return error_view(ErrorResponse(action=request.action, error=body))


class CloudCommands:
    """Sends the Cloud area's actions, asking the backend's confirmation first."""

    def __init__(self, executor: Executor, outlet: CloudOutlet, on_answer: Callable[[], None]) -> None:
        """Send through `executor`; confirmations and errors go to `outlet`; `on_answer` after every answer."""
        self._channel = ExclusiveChannel(executor)
        self._outlet = outlet
        self._on_answer = on_answer

    def busy(self, key: Hashable) -> bool:
        """Return whether the key's request is in flight."""
        return self._channel.busy(key)

    def send(self, key: Hashable, request: Request, on_success: Succeeded) -> bool:
        """Send a request (refused while the key's previous one is in flight); return whether it was sent."""
        answered = partial(self._answered, key, request, on_success)
        return self._channel.send(key, request, answered) is not None

    def _answered(self, key: Hashable, request: Request, on_success: Succeeded, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, key, request, response, on_success)
            self._outlet.confirm(confirmation_view(response), confirmed)
        elif not isinstance(response, ErrorResponse):
            on_success(response)
        elif response.error.code != ERROR_CANCELLED:
            # A `cancelled` answer means the window is closing: nothing to show.
            resend = partial(self.send, key, request, on_success)
            self._outlet.show_error(error_view(response), resend)
        self._on_answer()

    def _confirmed(self, key: Hashable, request: Request, asked: NeedsConfirmation, on_success: Succeeded) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(internal_error(request), None)
            return
        self.send(key, confirmed, on_success)
