"""Creating the job, or saving an edited job's selection (architecture §3.3).

Qt-free. `create_batch` (or `edit_batch` for a job reopened with "Edit selection") goes out one
at a time. When a selected file already has a subtitle in a target language, the backend asks
first (`existing_languages`); Confirm re-sends the same request with the confirmation's
`set_to`. An error opens the window's error dialog; a saved job emits `batches_changed` and
opens its job page. An answer about a job the page no longer shows (the user started a new job,
or edits another one) only emits `batches_changed`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.batches.classes import CreateBatchRequest, EditBatchRequest, ItemLanguages
from transcribe_ui.batches.responses import BatchDetail, BatchStage, CreateBatchResponse, EditBatchResponse
from transcribe_ui.discovery.classes import SelectionOperation
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
from transcribe_ui.gui.selection.constants import OPEN_JOB_SIGNAL, SAVE_KEY
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


class SaveCommands:
    """Sends `create_batch` or `edit_batch`, one at a time, through the backend's confirmation."""

    def __init__(
        self,
        executor: Executor,
        outlet: SelectionOutlet,
        on_saved: Callable[[], None],
        on_leave: Callable[[], None],
    ) -> None:
        """Send through `executor`; signals, questions and errors go to `outlet`.

        `on_saved` follows a save of what the page shows; `on_leave` follows leaving a job's edit.
        """
        self._channel = ExclusiveChannel(executor)
        self._outlet = outlet
        self._on_saved = on_saved
        self._on_leave = on_leave
        # The job being edited; None for a new job.
        self.editing: EditTarget | None = None
        # Counts the times the page left what it showed: a save sent before no longer counts.
        self._generation = 0

    def leave(self, *, selection_dropped: bool = True) -> None:
        """Stop editing a job: a load of it still on its way is dropped.

        When the page also drops its selection (`selection_dropped`), a save sent before and
        answered later no longer changes the page.
        """
        self.editing = None
        if selection_dropped:
            self._generation += 1
        self._on_leave()

    def busy(self) -> bool:
        """Return whether a save is in flight."""
        return self._channel.busy(SAVE_KEY)

    def send(self, request: SaveRequest) -> None:
        """Send the save (refused while one is in flight: a double click)."""
        self._send(request, self._generation)

    def _send(self, request: SaveRequest, generation: int) -> None:
        """Send the save for the page as it was at `generation` (a retry keeps the first send's)."""
        answered = partial(self._answered, request, generation)
        self._channel.send(SAVE_KEY, request, answered)

    def _current(self, request: SaveRequest, generation: int) -> bool:
        """Return whether the page still shows what `request` saves (the same edited job, or the same new job)."""
        edited = request.batch_id if isinstance(request, EditBatchRequest) else None
        shown = None if self.editing is None else self.editing.batch_id
        return generation == self._generation and edited == shown

    def _answered(self, request: SaveRequest, generation: int, response: Response) -> None:
        current = self._current(request, generation)
        if isinstance(response, NeedsConfirmation):
            if current:
                confirmed = partial(self._confirmed, request, response, generation)
                self._outlet.confirm(confirmation_view(response), confirmed)
        elif isinstance(response, ErrorResponse):
            retry = partial(self._send, request, generation)
            self._outlet.show_error(error_view(response), retry)
        elif isinstance(response, CreateBatchResponse | EditBatchResponse):
            self._saved(response.batch, current=current)

    def _confirmed(self, request: SaveRequest, asked: NeedsConfirmation, generation: int) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of %s does not fit the request", request.action)
            self._outlet.show_error(confirmation_mismatch(request), None)
            return
        if isinstance(confirmed, CreateBatchRequest | EditBatchRequest):
            self._send(confirmed, generation)

    def _saved(self, detail: BatchDetail, *, current: bool) -> None:
        """Tell the pages; when the page still shows this job, start a new one and open the job's page."""
        batch_id = detail.summary.batch_id
        if current:
            self.editing = None
            self._on_saved()
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        if current:
            self._outlet.emit(OPEN_JOB_SIGNAL, batch_id)
