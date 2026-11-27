"""Reading the job results a `ready` batch has in R2 (`<batch_id>/results/<item>.<language>.json`)."""

import logging
from dataclasses import dataclass

from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import parse_result_key, results_prefix
from transcribe_contracts.reading import read_job_result
from transcribe_ui.batches.payloads import ResultErrorCode
from transcribe_ui.runs.constants import (
    JOB_FAILED,
    MISSING_RESULT_CODE,
    MISSING_RESULT_ERROR,
    RESULT_SUFFIX,
    UNREADABLE_RESULT_CODE,
    UNREADABLE_RESULT_ERROR,
)
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)

# Pending transcribe job ids by (item id, language).
type PendingJobs = dict[tuple[str, str], int]


@dataclass(frozen=True)
class ResultRead:
    """A job's outcome as its result JSON reports it."""

    # `ok`, `no_speech` or `failed`.
    status: str
    # The result's `error` (set for `failed`).
    error: str | None = None
    # The code of a failure the desktop decided (None for the worker's own errors).
    error_code: ResultErrorCode | None = None


# The outcome recorded for a job whose result cannot be read once the batch has `_done.json`.
UNREADABLE_RESULT = ResultRead(JOB_FAILED, UNREADABLE_RESULT_ERROR, UNREADABLE_RESULT_CODE)
# The outcome recorded for a job without a result JSON once the batch has `_done.json`.
MISSING_RESULT = ResultRead(JOB_FAILED, MISSING_RESULT_ERROR, MISSING_RESULT_CODE)


def read_results(store: ObjectStore, batch_id: str, pending: PendingJobs) -> dict[int, ResultRead | None]:
    """Return the outcome of each pending job whose result JSON is listed, by job id (None: unreadable).

    Raises:
        StorageError: R2 could not be listed or read.
    """
    found: dict[int, ResultRead | None] = {}
    for listed in store.list_objects(results_prefix(batch_id)):
        job_id = _pending_job_of(listed.key, pending)
        if job_id is None:
            continue
        body = store.read(listed.key)
        if body is not None:  # else deleted since the listing
            found[job_id] = _result_of(listed.key, body)
    return found


def _pending_job_of(key: str, pending: PendingJobs) -> int | None:
    """Return the pending job a result JSON key belongs to, or None."""
    parts = parse_result_key(key)
    if parts is None or parts.suffix != RESULT_SUFFIX:
        return None
    return pending.get((parts.item_id, parts.language))


def _result_of(key: str, body: bytes) -> ResultRead | None:
    try:
        job_result = read_job_result(key, body)
    except ContractError:
        logger.warning("The job result %s cannot be read", key)
        return None
    return ResultRead(job_result.status, job_result.error)
