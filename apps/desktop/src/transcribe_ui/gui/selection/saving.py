"""Creating the job, or saving an edited job's selection (architecture §3.3).

Qt-free. `create_batch` (or `edit_batch` for a job reopened with "Edit selection") goes out one
at a time. When a selected file already has a subtitle in a target language, the backend asks
first (`existing_languages`); Confirm re-sends the same request with the confirmation's
`set_to`. An error opens the window's error dialog; a saved job emits `batches_changed` and
opens its job page.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.batches.classes import CreateBatchRequest, EditBatchRequest, ItemLanguages
from transcribe_ui.batches.responses import BatchDetail, BatchStage, CreateBatchResponse, EditBatchResponse
from transcribe_ui.discovery.classes import SelectionOperation
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, confirmation_view, confirmed_request, error_view
from transcribe_ui.gui.selection.constants import BATCHES_CHANGED_SIGNAL, OPEN_JOB_SIGNAL, SAVE_KEY
from transcribe_ui.gui.selection.tree import Scanned

logger = logging.getLogger(__name__)

# A request that saves a job's selection.
type SaveRequest = CreateBatchRequest | EditBatchRequest


class SelectionOutlet(Protocol):
    """What the New job page tells the window: a bus signal, a confirmation to ask, an error to show."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error of an action; `retry` re-sends it (the `app_busy` fix)."""


@dataclass(frozen=True)
class EditTarget:
    """The job whose selection the page edits."""

    batch_id: str
    stage: BatchStage


@dataclass(frozen=True)
class Draft:
    """What a save sends: the scan on screen, the operations and the target languages."""

    scanned: Scanned
    operations: tuple[SelectionOperation, ...]
    languages: tuple[str, ...]
    item_languages: tuple[ItemLanguages, ...]


def save_request(draft: Draft, editing: EditTarget | None) -> SaveRequest:
    """Return `create_batch` for a new job, else `edit_batch` of the edited one.

    Raises:
        ValidationError: Without an operation or a language (the page offers no save then).
    """
    shared = {
        "scan": draft.scanned.spec,
        "scan_id": draft.scanned.index.tree.scan_id,
        "operations": draft.operations,
        "languages": draft.languages,
        "item_languages": draft.item_languages,
    }
    if editing is None:
        return CreateBatchRequest.model_validate({"action": "create_batch", **shared})
    return EditBatchRequest.model_validate({"action": "edit_batch", "batch_id": editing.batch_id, **shared})


def _internal_error(request: Request) -> ErrorView:
    body = ErrorBody(code=ERROR_INTERNAL, message="The confirmation does not fit the request.")
    return error_view(ErrorResponse(action=request.action, error=body))


class SaveCommands:
    """Sends `create_batch` or `edit_batch`, one at a time, through the backend's confirmation."""

    def __init__(self, executor: Executor, outlet: SelectionOutlet, on_saved: Callable[[], None]) -> None:
        """Send through `executor`; signals, questions and errors go to `outlet`; `on_saved` follows a save."""
        self._channel = ExclusiveChannel(executor)
        self._outlet = outlet
        self._on_saved = on_saved
        # The job being edited; None for a new job.
        self.editing: EditTarget | None = None

    def busy(self) -> bool:
        """Return whether a save is in flight."""
        return self._channel.busy(SAVE_KEY)

    def send(self, request: SaveRequest) -> None:
        """Send the save (refused while one is in flight: a double click)."""
        self._channel.send(SAVE_KEY, request, partial(self._answered, request))

    def _answered(self, request: SaveRequest, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, request, response)
            self._outlet.confirm(confirmation_view(response), confirmed)
        elif isinstance(response, ErrorResponse):
            self._outlet.show_error(error_view(response), partial(self.send, request))
        elif isinstance(response, CreateBatchResponse | EditBatchResponse):
            self._saved(response.batch)

    def _confirmed(self, request: SaveRequest, asked: NeedsConfirmation) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(_internal_error(request), None)
            return
        if isinstance(confirmed, CreateBatchRequest | EditBatchRequest):
            self.send(confirmed)

    def _saved(self, detail: BatchDetail) -> None:
        batch_id = detail.summary.batch_id
        self.editing = None
        self._on_saved()
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        self._outlet.emit(OPEN_JOB_SIGNAL, batch_id)
