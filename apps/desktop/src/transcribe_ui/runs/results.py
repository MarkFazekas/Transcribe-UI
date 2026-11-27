"""Results of `ready` batches: job results and `_done.json` (plan m5 §1 "Run scope", architecture §3.3).

For every `ready` batch (not only the run's: the worker processes every manifest), each pending
transcribe job whose result JSON is listed gets its status (`ok`, `no_speech` or `failed`, read
through `reading.read_job_result`, with the result's `error`), and a batch whose `_done.json`
exists moves to `transcribed` by compare-and-set. `_done.json` is looked for before the results
are listed: the worker writes it after the batch's last result, so a listing made after seeing
it holds every result (one written in between is seen at the next refresh, before the batch can
move on). A result that cannot be read stays pending (logged) until `_done.json` is there; then
the job is `failed` with the reason `unreadable result`, and a job with no result JSON at all
`failed` with `missing result` (the worker never comes back to a done batch), so a retry can
take them. Each batch's
lock is only tried (lock order runs → batch); a busy batch is checked at the next refresh. R2
is read outside any transaction.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import done_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.payloads import JobPayload
from transcribe_ui.runs.constants import READY_PHASE, TRANSCRIBE_KIND, TRANSCRIBED_PHASE
from transcribe_ui.runs.responses import BatchResults
from transcribe_ui.runs.result_reads import (
    MISSING_RESULT,
    UNREADABLE_RESULT,
    PendingJobs,
    ResultRead,
    read_results,
)
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.state.models import BatchRow, JobRow
from transcribe_ui.storage.r2 import ObjectStore


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
    done = store.head(done_key(batch_id)) is not None
    found = read_results(store, batch_id, pending)
    outcomes = _outcomes(found, pending, done=done)
    with state.session() as session:
        for job_id, outcome in outcomes.items():
            _record_result(session, job_id, outcome)
        if done:
            now = context.ports.clock.now()
            compare_and_set_phase(session, batch_id, READY_PHASE, TRANSCRIBED_PHASE, now=now)
        statuses = _transcribe_statuses(session, batch_id)
    return _batch_results(batch_id, statuses, done=done)


# Results read per job id (None: unreadable).
type FoundResults = dict[int, ResultRead | None]


def _outcomes(found: FoundResults, pending: PendingJobs, *, done: bool) -> dict[int, ResultRead]:
    """Return what to record per job: the readable results; once done, the unreadable and missing ones as failed.

    With `_done.json` the worker never comes back to the batch, so a job without a readable result
    would stay pending for good.
    """
    outcomes: dict[int, ResultRead] = {}
    for job_id, outcome in found.items():
        if outcome is not None:
            outcomes[job_id] = outcome
        elif done:
            outcomes[job_id] = UNREADABLE_RESULT
    if done:
        for job_id in pending.values():
            outcomes.setdefault(job_id, MISSING_RESULT)
    return outcomes


def _record_result(session: Session, job_id: int, outcome: ResultRead) -> None:
    """Set a job's status (and its failure reason) from its result, unless it is no longer pending."""
    row = session.get(JobRow, job_id)
    if row is None or row.status != JOB_PENDING:
        return
    stored = JobPayload.model_validate(row.payload)
    payload = stored.model_copy(update={"result_error": outcome.error})
    row.status = outcome.status
    row.payload = payload.model_dump(mode="json")


def _transcribe_jobs(batch_id: str) -> tuple[ColumnElement[bool], ...]:
    return (JobRow.batch_id == batch_id, JobRow.kind == TRANSCRIBE_KIND)


def _pending_jobs(session: Session, batch_id: str) -> PendingJobs:
    """Return the batch's pending transcribe jobs: job id by (item id, language)."""
    columns = select(JobRow.item_id, JobRow.language, JobRow.job_id)
    query = columns.where(*_transcribe_jobs(batch_id), JobRow.status == JOB_PENDING)
    rows = session.execute(query).all()
    return {(item_id, language): job_id for item_id, language, job_id in rows}


def _transcribe_statuses(session: Session, batch_id: str) -> list[str]:
    query = select(JobRow.status).where(*_transcribe_jobs(batch_id))
    return list(session.scalars(query))


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
