"""The handler of `retry_jobs`: the ticked failed and no-speech jobs into a retry job (architecture §3.1 "GUI").

Qt-free. Retry sends `create_retry_batch` with the ticked jobs in the activity panel, which
shows the copy's progress, Cancel and Resume (re-sending resumes the same retry job). Its answer
reloads the jobs (a retry changes the list) and opens the retry job, unless the user went to
another page or job meanwhile; then a note says where it is. An error (`audio_deleted`: the
audio was deleted since the page showed Retry) stays in the panel with its explanation, and the
jobs reload, so the page shows what is possible now.
"""

import logging
from functools import partial

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.cleanup.classes import CreateRetryBatchRequest, CreateRetryBatchResponse
from transcribe_ui.gui.cleanup.constants import OPEN_JOB_SIGNAL
from transcribe_ui.gui.cleanup.display import retry_created_text, retry_title
from transcribe_ui.gui.cleanup.panel import JobSteps
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL

logger = logging.getLogger(__name__)


def _retry_jobs(jobs: object) -> tuple[RetryJob, ...]:
    if not isinstance(jobs, tuple):
        return ()
    return tuple(job for job in jobs if isinstance(job, RetryJob))


class RetryModel:
    """Retry of a job's ticked failed and no-speech transcriptions."""

    def __init__(self, steps: JobSteps) -> None:
        """Run the retry as a step of the job in the panel."""
        self._steps = steps

    def build_request(self, batch_id: str, jobs: object) -> CreateRetryBatchRequest | None:
        """Return the retry request of the ticked jobs, or None without any."""
        picked = _retry_jobs(jobs)
        if not picked:
            return None
        try:
            return CreateRetryBatchRequest(action="create_retry_batch", batch_id=batch_id, jobs=picked)
        except ValidationError:
            logger.exception("The jobs ticked for a retry of %s do not form a request", batch_id)
            return None

    def retry(self, batch_id: str, jobs: object) -> None:
        """Retry the ticked jobs (`retry_jobs`): copy their audio into a new retry job, then open it."""
        request = self.build_request(batch_id, jobs)
        if request is not None:
            retried = partial(self._retried, batch_id, self._steps.generation)
            self._steps.start(batch_id, request, retry_title(batch_id), retried)

    def _retried(self, batch_id: str, generation: int, response: Response) -> None:
        outlet = self._steps.outlet
        if not isinstance(response, CreateRetryBatchResponse):
            if isinstance(response, ErrorResponse):
                self._steps.explain(batch_id, response)
            # A retry job may exist now (an interrupted copy) and the source may have changed: reload every job.
            outlet.emit(BATCHES_CHANGED_SIGNAL, ())
            return
        retry_id = response.batch.batch_id
        outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id, retry_id))
        if generation == self._steps.generation:
            outlet.emit(OPEN_JOB_SIGNAL, retry_id)
        else:
            outlet.notify(retry_created_text(retry_id))
