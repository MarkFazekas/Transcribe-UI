"""The Cloud area's own actions: one at a time per key, confirmed by the backend's confirmation.

Qt-free. Stop and Cancel of a run (`cancel_worker`) and Terminate or Cancel of a cloud machine
(`terminate_pod`, `cancel_queued_pod`) each ask the backend first: the `needs_confirmation`
answer is shown by the window's confirmation dialog, and Confirm re-sends the same request with
each confirmation's `set_to`. An error opens the error dialog (Retry re-sends). A success and an
error both call the action's follow-up (an error may have changed something before it failed);
a declined confirmation and a `cancelled` answer (the window closing) call nothing. A second press while the key's request is in flight sends nothing.
"""

import logging
from collections.abc import Callable, Hashable
from functools import partial

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import confirmation_mismatch, confirmation_view, confirmed_request, error_view

logger = logging.getLogger(__name__)

# Called with the final answer of a request: its success, or its error.
type Finished = Callable[[Response], None]


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

    def send(self, key: Hashable, request: Request, on_finished: Finished) -> bool:
        """Send a request (refused while the key's previous one is in flight); return whether it was sent."""
        answered = partial(self._answered, key, request, on_finished)
        return self._channel.send(key, request, answered) is not None

    def _answered(self, key: Hashable, request: Request, on_finished: Finished, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, key, request, response, on_finished)
            # Each question names what it stops (and why it is safe).
            self._outlet.confirm(confirmation_view(response, with_details=False), confirmed)
        elif not isinstance(response, ErrorResponse):
            on_finished(response)
        elif response.error.code != ERROR_CANCELLED:
            # A `cancelled` answer means the window is closing: nothing to show.
            resend = partial(self.send, key, request, on_finished)
            self._outlet.show_error(error_view(response), resend)
            on_finished(response)
        self._on_answer()

    def _confirmed(self, key: Hashable, request: Request, asked: NeedsConfirmation, on_finished: Finished) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(confirmation_mismatch(request), None)
            return
        self.send(key, confirmed, on_finished)
