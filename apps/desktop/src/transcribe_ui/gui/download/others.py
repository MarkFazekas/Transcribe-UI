"""Jobs other computers uploaded to the same cloud storage, and their download here (architecture §3.9).

Qt-free. "Look in the cloud" sends `list_cloud_batches` (R2 only; latest wins) and lists the jobs
this computer does not know (`known` false), each with its files, audio length and state; the
list loads only when asked (the only automatic requests are the checks of the cloud). Download
sends `adopt_cloud_batch` (one at a time per job): the backend's confirmation shows in the
window's confirmation dialog, and Confirm re-sends the request with each `set_to`. The adopted
job is then one of this computer's jobs: the jobs pages reload (`batches_changed`), its download
dialog opens (`open_download`; `mirror` is pre-selected there, since next to the media is
impossible for it), the cloud is checked, so its results are read and the poll follows it, and
the list loads again; until a list succeeds, the adopted job's row counts as adopting, so its
Download stays disabled. An error opens the error dialog (Retry re-sends) and also loads the
list again, since the job may have changed in the cloud meanwhile.
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import Protocol

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.batches.classes import AdoptCloudBatchRequest, ListCloudBatchesRequest
from transcribe_ui.batches.cloud_responses import AdoptCloudBatchResponse, ListCloudBatchesResponse
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.download.constants import CLOUD_JOBS_KEY, OPEN_DOWNLOAD_SIGNAL
from transcribe_ui.gui.download.others_display import CloudJobRow, cloud_job_row
from transcribe_ui.gui.executor import ExclusiveChannel, Executor, LatestChannel
from transcribe_ui.gui.feedback import (
    ConfirmationItem,
    ConfirmationView,
    Details,
    ErrorView,
    confirmation_mismatch,
    confirmation_view,
    confirmed_request,
    error_view,
)
from transcribe_ui.gui.viewmodel import ViewModel

logger = logging.getLogger(__name__)


class OthersOutlet(Protocol):
    """What the list tells the window: a bus signal, a confirmation to ask, an error to show."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error; `retry` sends the request again."""


def _question(response: NeedsConfirmation) -> ConfirmationView:
    """Return the confirmation without its details table: the question names the job and its file count."""
    no_details = Details((), ())
    shown = confirmation_view(response).confirmations
    asked = [ConfirmationItem(question.message, no_details) for question in shown]
    return ConfirmationView(tuple(asked))


class OtherJobsModel(ViewModel):
    """The jobs of other computers in the cloud, and Download, which adds one to this computer's jobs."""

    def __init__(self, executor: Executor, outlet: OthersOutlet, check_cloud: Callable[[], None]) -> None:
        """Send through `executor`; tell the window through `outlet`; `check_cloud` reads an adopted job's results."""
        super().__init__(executor)
        self._lists = LatestChannel(executor)
        self._adoptions = ExclusiveChannel(executor)
        self._outlet = outlet
        self._check_cloud = check_cloud
        self.rows: tuple[CloudJobRow, ...] = ()
        # Whether a list answered (before that the section says it has not looked yet).
        self.listed = False
        # Whether a list is on its way.
        self.loading = False
        # The error of the last list (None after one that succeeded).
        self.error: ErrorView | None = None
        # The jobs adopted since the last list that succeeded (their rows are still shown).
        self._adopted_ids: set[str] = set()

    def adopting(self, batch_id: str) -> bool:
        """Return whether the Download of a job is in flight, or it was adopted and no list succeeded since."""
        return self._adoptions.busy(batch_id) or batch_id in self._adopted_ids

    def look(self) -> None:
        """List the jobs in the cloud (a list on its way is replaced)."""
        # Before the send: an executor may answer inside it.
        self.loading = True
        self.changed()
        self._lists.send(CLOUD_JOBS_KEY, ListCloudBatchesRequest(action="list_cloud_batches"), self._listed)

    def download(self, batch_id: str) -> None:
        """Add the job to this computer's jobs (through the backend's confirmation), then open its download."""
        self._send(AdoptCloudBatchRequest(action="adopt_cloud_batch", batch_id=batch_id))

    def _send(self, request: AdoptCloudBatchRequest) -> None:
        answered = partial(self._adopted, request)
        if self._adoptions.send(request.batch_id, request, answered) is not None:
            self.changed()

    def _listed(self, response: Response) -> None:
        self.loading = False
        if isinstance(response, ListCloudBatchesResponse):
            others = [batch for batch in response.batches if not batch.known]
            self.rows = tuple(cloud_job_row(batch) for batch in others)
            self.listed = True
            self.error = None
            self._adopted_ids.clear()
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
        self.changed()

    def _adopted(self, request: AdoptCloudBatchRequest, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            confirmed = partial(self._confirmed, request, response)
            self._outlet.confirm(_question(response), confirmed)
        elif isinstance(response, AdoptCloudBatchResponse):
            batch_id = response.batch.batch_id
            # Before the signals: the row stays disabled until the list loaded again shows it gone.
            self._adopted_ids.add(batch_id)
            self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
            self._outlet.emit(OPEN_DOWNLOAD_SIGNAL, batch_id)
            self._check_cloud()
            self.look()
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            resend = partial(self._send, request)
            self._outlet.show_error(error_view(response), resend)
            self.look()
        self.changed()

    def _confirmed(self, request: AdoptCloudBatchRequest, asked: NeedsConfirmation) -> None:
        try:
            confirmed = confirmed_request(request, asked)
        except ValidationError:
            logger.exception("The confirmation of adopt_cloud_batch does not fit the request")
            self._outlet.show_error(confirmation_mismatch(request), None)
            return
        if isinstance(confirmed, AdoptCloudBatchRequest):
            self._send(confirmed)
