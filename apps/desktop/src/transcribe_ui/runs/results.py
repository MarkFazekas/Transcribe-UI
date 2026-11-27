"""Results of `ready` batches: job results and `_done.json` (plan m5 §1 "Run scope", architecture §3.3).

For every `ready` batch (not only the run's: the worker processes every manifest), each pending
transcribe job whose result JSON is listed gets its status (`ok`, `no_speech` or `failed`, read
through `reading.read_job_result`; one that cannot be read stays pending and is logged), and a
batch whose `_done.json` exists moves to `transcribed` by compare-and-set. Each batch's lock is
only tried (lock order runs → batch); a busy batch is checked at the next refresh. R2 is read
outside any transaction.
"""

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.orm import Session

from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import done_key, parse_result_key, results_prefix
from transcribe_contracts.reading import read_job_result
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.runs.constants import READY_PHASE, TRANSCRIBED_PHASE
from transcribe_ui.runs.responses import BatchResults
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.models import BatchRow, JobRow
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)

# Kind of the jobs the worker runs.
TRANSCRIBE_KIND = "transcribe"
# Suffix of a job's result JSON (its "done" marker).
RESULT_SUFFIX = ".json"

# Pending transcribe job ids by (item id, language).
type PendingJobs = dict[tuple[str, str], int]


@dataclass(frozen=True)
class ReadyCheck:
    """What the check of the `ready` batches found."""

    batches: tuple[BatchResults, ...] = ()
    # Skipped because another request holds the batch's lock.
    busy: tuple[str, ...] = ()


def check_ready_batches(context: RequestContext, store: ObjectStore) -> ReadyCheck:
    """Record the job results of every `ready` batch and move those with `_done.json` to `transcribed`.

    Raises:
        StorageError: R2 could not be read (what came before is recorded).
    """
    state = context.ports.state
    is_ready = BatchRow.phase == READY_PHASE
    with state.session() as session:
        query = select(BatchRow.batch_id).where(is_ready).order_by(BatchRow.batch_id)
        ready = list(session.scalars(query))
    checked: list[BatchResults] = []
    busy: list[str] = []
    for batch_id in ready:
        with state.locks.try_batch(batch_id) as taken:
            if taken:
                checked.append(_check_batch(context, store, batch_id))
            else:
                busy.append(batch_id)
    return ReadyCheck(tuple(checked), tuple(busy))


def _check_batch(context: RequestContext, store: ObjectStore, batch_id: str) -> BatchResults:
    """Check one `ready` batch whose lock this thread holds."""
    state = context.ports.state
    with state.session() as session:
        pending = _pending_jobs(session, batch_id)
    found = _read_results(store, batch_id, pending)
    done = store.head(done_key(batch_id)) is not None
    with state.session() as session:
        for job_id, job_status in found.items():
            _record_result(session, job_id, job_status)
        if done:
            now = context.ports.clock.now()
            compare_and_set_phase(session, batch_id, READY_PHASE, TRANSCRIBED_PHASE, now=now)
        statuses = _transcribe_statuses(session, batch_id)
    return _batch_results(batch_id, statuses, done=done)


def _record_result(session: Session, job_id: int, job_status: str) -> None:
    """Set a job's status from its result, unless it is no longer pending."""
    still_pending = (JobRow.job_id == job_id, JobRow.status == JOB_PENDING)
    statement = update(JobRow).where(*still_pending)
    session.execute(statement.values(status=job_status))


def _transcribe_jobs(batch_id: str) -> tuple[ColumnElement[bool], ...]:
    return (JobRow.batch_id == batch_id, JobRow.kind == TRANSCRIBE_KIND)


def _pending_jobs(session: Session, batch_id: str) -> dict[tuple[str, str], int]:
    """Return the batch's pending transcribe jobs: job id by (item id, language)."""
    columns = select(JobRow.item_id, JobRow.language, JobRow.job_id)
    query = columns.where(*_transcribe_jobs(batch_id), JobRow.status == JOB_PENDING)
    rows = session.execute(query).all()
    return {(item_id, language): job_id for item_id, language, job_id in rows}


def _transcribe_statuses(session: Session, batch_id: str) -> list[str]:
    query = select(JobRow.status).where(*_transcribe_jobs(batch_id))
    return list(session.scalars(query))


def _read_results(store: ObjectStore, batch_id: str, pending: PendingJobs) -> dict[int, str]:
    """Return the status of each pending job whose result JSON is listed and readable, by job id."""
    found: dict[int, str] = {}
    for listed in store.list_objects(results_prefix(batch_id)):
        job_id = _pending_job_of(listed.key, pending)
        job_status = None if job_id is None else _result_status(store, listed.key)
        if job_id is not None and job_status is not None:
            found[job_id] = job_status
    return found


def _pending_job_of(key: str, pending: PendingJobs) -> int | None:
    """Return the pending job a result JSON key belongs to, or None."""
    parts = parse_result_key(key)
    if parts is None or parts.suffix != RESULT_SUFFIX:
        return None
    return pending.get((parts.item_id, parts.language))


def _result_status(store: ObjectStore, key: str) -> str | None:
    body = store.read(key)
    if body is None:
        return None  # deleted since the listing
    try:
        return read_job_result(key, body).status
    except ContractError:
        logger.warning("The job result %s cannot be read; the job stays pending", key)
        return None


def _batch_results(batch_id: str, statuses: list[str], *, done: bool) -> BatchResults:
    counted = Counter(statuses)
    return BatchResults(
        batch_id=batch_id,
        phase=TRANSCRIBED_PHASE if done else READY_PHASE,
        ok=counted["ok"],
        no_speech=counted["no_speech"],
        failed=counted["failed"],
        pending=counted[JOB_PENDING],
    )
