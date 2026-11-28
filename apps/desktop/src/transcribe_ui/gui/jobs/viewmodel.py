"""View-models of the jobs list and the job page (architecture §3.1 "GUI").

Qt-free. The list sends `list_batches` (newest first, hidden jobs with "Show hidden"), the page
`get_batch`; both reload on `batches_changed`. Every button of a job comes from its allowed
actions (`BatchSummary.actions`). The buttons other parts handle only emit their bus signal
(edit selection, review, upload, download, retry with the ticked jobs, cleanup with its scope);
the shell answers "Not available yet" until that part connects its handler. Hide, show, delete
and continue to review go through `JobCommands`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.batches.classes import GetBatchRequest, ListBatchesRequest
from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.batches.responses import BatchDetail, BatchSummary, GetBatchResponse, ListBatchesResponse
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.jobs import constants
from transcribe_ui.gui.jobs.commands import JobCommands, JobOutlet
from transcribe_ui.gui.jobs.display import (
    JobButton,
    JobRow,
    TranscriptionRow,
    job_buttons,
    job_row,
    transcription_rows,
    with_retry_state,
)
from transcribe_ui.gui.viewmodel import ViewModel

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


@dataclass(frozen=True)
class Pressed:
    """A button pressed on a job page: which, for which job, and the jobs ticked for Retry."""

    key: constants.JobButtonKey
    batch_id: str
    ticked: tuple[RetryJob, ...]


def _carry_out(pressed: Pressed, outlet: JobOutlet, commands: JobCommands) -> None:
    """Emit the button's signal (with the cleanup scope, or the ticked jobs), or send the jobs pages' request."""
    key = pressed.key
    batch_id = pressed.batch_id
    signal_name = constants.SIGNAL_BUTTONS.get(key)
    scope = constants.CLEANUP_BUTTONS.get(key)
    if signal_name:
        outlet.emit(signal_name, batch_id)
    elif scope:
        outlet.emit(constants.CLEAN_UP_SIGNAL, batch_id, scope)
    elif key == "retry":
        outlet.emit(constants.RETRY_SIGNAL, batch_id, pressed.ticked)
    else:
        commands.press(key, batch_id)


class JobsListModel(ViewModel):
    """The jobs list: newest first, hidden jobs only with "Show hidden"; Open, Hide and Show per job."""

    def __init__(self, executor: Executor, commands: JobCommands, outlet: JobOutlet) -> None:
        """Load through `executor`; hide and show through `commands`; Open through `outlet`."""
        super().__init__(executor)
        self._channel = LatestChannel(executor)
        self._commands = commands
        self._outlet = outlet
        self.show_hidden = False
        self.summaries: tuple[BatchSummary, ...] = ()
        self.rows: tuple[JobRow, ...] = ()
        # The error of the last load, shown in the page with Retry; None after a load that succeeded.
        self.error: ErrorView | None = None
        # `proceed` of the start-up step waiting for a load.
        self._waiting: list[Proceed] = []

    def build_list_request(self) -> ListBatchesRequest:
        """Return the request of the list as it is set now."""
        return ListBatchesRequest(action="list_batches", include_hidden=self.show_hidden)

    def reload(self, proceed: Proceed | None = None) -> None:
        """Load the list (a load in flight is replaced); `proceed` (a start-up step's) is called once it answered."""
        if proceed is not None:
            self._waiting.append(proceed)
        self._channel.send(constants.LIST_KEY, self.build_list_request(), self._loaded)

    def include_hidden(self, *, shown: bool) -> None:
        """Show or leave out the hidden jobs, and reload."""
        self.show_hidden = shown
        self.reload()

    def on_batches_changed(self, _batch_ids: object) -> None:
        """Reload after any job changed."""
        self.reload()

    def buttons(self, batch_id: str) -> tuple[JobButton, ...]:
        """Return the list's buttons for a job (Hide or Show, from its allowed actions)."""
        for summary in self.summaries:
            if summary.batch_id == batch_id:
                return job_buttons(summary, constants.LIST_BUTTONS)
        return ()

    def press(self, batch_id: str, key: constants.JobButtonKey) -> None:
        """Hide or show a job."""
        self._commands.press(key, batch_id)

    def open_job(self, batch_id: str) -> None:
        """Open the job page of a job."""
        self._outlet.emit(constants.OPEN_JOB_SIGNAL, batch_id)

    def _loaded(self, response: Response) -> None:
        if isinstance(response, ListBatchesResponse):
            self.summaries = response.batches
            self.rows = tuple(job_row(summary) for summary in response.batches)
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()


class JobPageModel(ViewModel):
    """One job: its stage, labels, files and transcriptions, failed ones with their reason, and its buttons."""

    def __init__(self, executor: Executor, commands: JobCommands, outlet: JobOutlet) -> None:
        """Load through `executor`; the page's own requests through `commands`; signals through `outlet`."""
        super().__init__(executor)
        self._channel = LatestChannel(executor)
        self._commands = commands
        self._outlet = outlet
        self.batch_id: str | None = None
        self.detail: BatchDetail | None = None
        self.header: JobRow | None = None
        self.rows: tuple[TranscriptionRow, ...] = ()
        self.buttons: tuple[JobButton, ...] = ()
        self.error: ErrorView | None = None
        # Jobs the user took the tick off; every other failed or no-speech job is ticked for Retry.
        self._cleared: set[RetryJob] = set()

    def build_get_request(self) -> GetBatchRequest | None:
        """Return the request of the page's job, or None without a job."""
        if self.batch_id is None:
            return None
        return GetBatchRequest(action="get_batch", batch_id=self.batch_id)

    def open_batch(self, batch_id: str) -> None:
        """Show a job (another job's ticks start fresh) and load it."""
        if batch_id != self.batch_id:
            self.batch_id = batch_id
            self.detail = None
            self.header = None
            self.rows = ()
            self.buttons = ()
            self.error = None
            self._cleared = set()
            self.changed()
        self.reload()

    def close(self) -> None:
        """Leave the job (back to the list): later changes no longer reload it."""
        self.batch_id = None

    def reload(self) -> None:
        """Load the page's job again."""
        request = self.build_get_request()
        if request is not None:
            loaded = partial(self._loaded, request.batch_id)
            self._channel.send(constants.PAGE_KEY, request, loaded)

    def on_batches_changed(self, batch_ids: object) -> None:
        """Reload when the page's job changed (an empty tuple: any job)."""
        any_batch = isinstance(batch_ids, tuple) and not batch_ids
        named = isinstance(batch_ids, tuple) and self.batch_id in batch_ids
        if self.batch_id is not None and (any_batch or named):
            self.reload()

    def ticked(self) -> tuple[RetryJob, ...]:
        """Return the jobs ticked for Retry, in the page's order."""
        offered = [row.retry for row in self.rows if row.retry is not None]
        return tuple(job for job in offered if job not in self._cleared)

    def set_ticked(self, job: RetryJob, *, ticked: bool) -> None:
        """Tick or untick a failed or no-speech job for Retry."""
        if ticked:
            self._cleared.discard(job)
        else:
            self._cleared.add(job)
        self.buttons = with_retry_state(self.buttons, ticked=bool(self.ticked()))
        self.changed()

    def press(self, key: constants.JobButtonKey) -> None:
        """Do what a shown, enabled button does: emit its signal, or send the request the jobs pages handle."""
        batch_id = self.batch_id
        usable = {button.key for button in self.buttons if button.enabled}
        if batch_id is not None and key in usable:
            pressed = Pressed(key, batch_id, self.ticked())
            _carry_out(pressed, self._outlet, self._commands)

    def _loaded(self, batch_id: str, response: Response) -> None:
        if batch_id != self.batch_id:
            return
        if isinstance(response, GetBatchResponse):
            detail = response.batch
            self.detail = detail
            self.header = job_row(detail.summary)
            self.rows = transcription_rows(detail)
            buttons = job_buttons(detail.summary)
            self.buttons = with_retry_state(buttons, ticked=bool(self.ticked()))
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self.changed()
