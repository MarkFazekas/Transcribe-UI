"""Apply and Remove from job: the review page's changing requests (architecture §3.1 "GUI" "Review and upload").

Qt-free. They go through the page's `SerialSender`, after the call before them. A confirmation
comes from the backend (`exclude_items` asks) and is confirmed by re-sending the request with each
`set_to`; an error opens the window's error dialog, whose retry re-sends the same request with the
same follow-up, so its answer is judged against the page as it was when first sent. Every answer
tells the other pages that the job changed; an answer about a job the page no longer shows does
nothing else. Applied choices leave the draft; the views come from the answer when it is the
page's current, unfiltered view, else they are loaded again (`resolve_review` answers unfiltered).
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.batches.classes import ExcludeItemsRequest, ResolveReviewRequest, ReviewFilter
from transcribe_ui.batches.responses import ExcludeItemsResponse, ResolveReviewResponse
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, confirmation_view, confirmed_request, error_view
from transcribe_ui.gui.review.editing import ChoiceEditor
from transcribe_ui.gui.review.loading import ReviewLoader
from transcribe_ui.gui.review.sender import SerialSender

logger = logging.getLogger(__name__)

# Called with the response of a request that succeeded.
type Succeeded = Callable[[Response], None]


class ReviewOutlet(Protocol):
    """What the review page tells the window: a bus signal, a question, an error, a status message."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error of an action; `retry` re-sends it (the `app_busy` fix)."""

    def show_status(self, text: str) -> None:
        """Show a short message in the window's status bar."""


def _internal_error(request: Request) -> ErrorView:
    body = ErrorBody(code=ERROR_INTERNAL, message="The confirmation does not fit the request.")
    return error_view(ErrorResponse(action=request.action, error=body))


class ReviewCommands:
    """Sends Apply (`resolve_review`) and Remove from job (`exclude_items`) after the page's other calls."""

    def __init__(
        self,
        sender: SerialSender,
        loader: ReviewLoader,
        editor: ChoiceEditor,
        outlet: ReviewOutlet,
        tell: Callable[[str], None],
    ) -> None:
        """Send through `sender`; questions and errors go to `outlet`; `tell` says a job changed."""
        self._sender = sender
        self._loader = loader
        self._editor = editor
        self._outlet = outlet
        self._tell = tell

    def apply(self) -> None:
        """Store the choices made (`resolve_review`)."""
        batch_id = self._loader.batch_id
        choices = self._editor.draft.pending()
        if batch_id is None or not choices:
            return
        request = ResolveReviewRequest(action="resolve_review", batch_id=batch_id, choices=choices)
        applied = partial(self._applied, batch_id, self._loader.generation, request)
        self._send(request, applied)

    def remove_ticked(self) -> None:
        """Remove the ticked files from the job (`exclude_items`, after the backend's confirmation)."""
        batch_id = self._loader.batch_id
        ticked = self._editor.ticked()
        if batch_id is not None and ticked:
            request = ExcludeItemsRequest(action="exclude_items", batch_id=batch_id, item_ids=ticked)
            self._send(request, partial(self._removed, batch_id, ticked))

    def _send(self, request: Request, on_success: Succeeded) -> None:
        """Send a changing request; `on_success` gets its answer once it succeeded (after a confirmation)."""
        self._sender.send_action(request, partial(self._answered, request, on_success))
        self._editor.notify()

    def _answered(self, request: Request, on_success: Succeeded, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, request, response, on_success)
            self._outlet.confirm(confirmation_view(response), confirmed)
        elif isinstance(response, ErrorResponse):
            retry = partial(self._send, request, on_success)
            self._outlet.show_error(error_view(response), retry)
        else:
            on_success(response)
        self._editor.notify()

    def _confirmed(self, request: Request, asked: NeedsConfirmation, on_success: Succeeded) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(_internal_error(request), None)
            return
        self._send(confirmed, on_success)

    def _applied(self, batch_id: str, generation: int, request: ResolveReviewRequest, response: Response) -> None:
        self._tell(batch_id)
        loader = self._loader
        if batch_id != loader.batch_id or not isinstance(response, ResolveReviewResponse):
            return
        self._editor.draft.forget(request.choices)
        current = generation == loader.generation and not self._sender.reviewing()
        if current and loader.filters == ReviewFilter():
            loader.loaded.review = response.review
            loader.load_detail()
        else:
            loader.reload()

    def _removed(self, batch_id: str, ticked: tuple[str, ...], response: Response) -> None:
        self._tell(batch_id)
        if batch_id == self._loader.batch_id and isinstance(response, ExcludeItemsResponse):
            self._editor.forget_items(ticked)
            self._loader.loaded.detail = response.batch
            self._loader.load_review()
