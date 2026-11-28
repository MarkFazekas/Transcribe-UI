"""Results of `ready` batches: job results and `_done.json` (plan m5 §1 "Run scope", architecture §3.3).

For every `ready` batch (not only the run's: another computer's run may list it), each pending
transcribe job whose result JSON is listed gets its status (`ok`, `no_speech` or `failed`, read
through `reading.read_job_result`, with the result's `error`, its run and the worker's times on the
job for the remaining-time estimate), and a batch whose `_done.json`
exists moves to `transcribed` by compare-and-set. `_done.json` is looked for before the results
are listed: the worker writes it after the batch's last result, so a listing made after seeing
it holds every result (one written in between is seen at the next refresh, before the batch can
move on). A result that cannot be read stays pending (logged) until `_done.json` is there; then
the job is `failed` with the reason `unreadable result`, and a job with no result JSON at all
`failed` with `missing result` (the worker never comes back to a done batch), so a retry can
take them. A batch with neither `_done.json` nor its manifest in R2 was removed from the cloud by
another computer (this database's own cleanup moves a batch on first): it is marked
`removed_from_cloud`, no longer checked, and its history stays (architecture §3.9). A HEAD of a
missing bucket also finds nothing, so before the first mark of a refresh the bucket is listed
(`check_access`): a missing bucket (a mistyped name) is the refresh's error, never a removal. With
`recheck` the marked batches are looked for first, and one that is back is followed again
(`runs/ready_batches.py`). A batch that moves to `transcribed` moves on to `downloaded` when every job already
has a placement outcome (`placement/recording.py`: downloaded in full while it was `ready`); the
rule is also applied to every `transcribed` batch (a download killed after its last outcome). Each
batch's lock is only tried (lock order runs → batch); a busy batch is checked at the next refresh. R2
is read outside any transaction.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import done_key, manifest_key
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.payloads import JobPayload
from transcribe_ui.placement.recording import mark_transcribed, settle_transcribed
from transcribe_ui.runs.constants import READY_PHASE, TRANSCRIBE_KIND
from transcribe_ui.runs.ready_batches import awaiting_results, followed_batches, mark_removed, recheck_cloud
from transcribe_ui.runs.responses import BatchResults
from transcribe_ui.runs.result_reads import (
    MISSING_RESULT,
    UNREADABLE_RESULT,
    PendingJobs,
    ResultRead,
    read_results,
)
from transcribe_ui.state.models import BatchPhase, BatchRow, JobRow
from transcribe_ui.storage.r2 import ObjectStore


@dataclass(frozen=True)
class ReadyCheck:
    """What the check of the `ready` batches found."""

    batches: tuple[BatchResults, ...] = ()
    # Skipped because another request holds the batch's lock.
    busy: tuple[str, ...] = ()
    # Whether a followed batch still waits for its results after the check.
    awaiting: bool = False
    # Other computers' jobs whose interrupted cleanup was finished (`recheck`, `cleanup/pending.py`).
    finished_cleanups: tuple[str, ...] = ()


class _Bucket:
    """The bucket of one refresh, listed once before the first batch is marked removed."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store
        self._proven = False

    def holds(self, batch_id: str, *, done_known_missing: bool = False) -> bool:
        """Return whether R2 holds the batch's `_done.json` or manifest.

        `done_known_missing`: the caller has just found no `_done.json`, so only the manifest is
        looked for.

        Raises:
            StorageError: R2 could not be read, or the bucket does not exist.
        """
        store = self.store
        keys = [manifest_key(batch_id)]
        if not done_known_missing:
            keys.insert(0, done_key(batch_id))
        if any(store.head(key) is not None for key in keys):
            return True
        if not self._proven:
            store.check_access()
            self._proven = True
        return False


def check_ready_batches(context: RequestContext, store: ObjectStore, *, recheck: bool = False) -> ReadyCheck:
    """Record the job results of every `ready` batch and move those with `_done.json` to `transcribed`.

    `recheck`: first finish this computer's interrupted cleanups of other computers' jobs, and follow
    again the batches marked removed that are back in the cloud.

    Raises:
        StorageError: R2 could not be read, or the bucket does not exist (what came before is recorded).
    """
    state = context.ports.state
    bucket = _Bucket(store)
    finished = recheck_cloud(context, store, bucket.holds) if recheck else ()
    checked: list[BatchResults] = []
    busy: list[str] = []
    for batch_id in _followed(context):
        with state.locks.try_batch(batch_id) as taken:
            if taken:
                checked.append(_check_batch(context, bucket, batch_id))
            else:
                busy.append(batch_id)
    settle_transcribed(context)
    awaiting = awaiting_results(state)
    return ReadyCheck(tuple(checked), tuple(busy), awaiting=awaiting, finished_cleanups=finished)


def _followed(context: RequestContext) -> list[str]:
    """Return the followed `ready` batches, by id."""
    query = select(BatchRow.batch_id).where(*followed_batches())
    with context.ports.state.session() as session:
        return list(session.scalars(query.order_by(BatchRow.batch_id)))


def _check_batch(context: RequestContext, bucket: _Bucket, batch_id: str) -> BatchResults:
    """Check one `ready` batch whose lock this thread holds."""
    state = context.ports.state
    done = bucket.store.head(done_key(batch_id)) is not None
    if not done and not bucket.holds(batch_id, done_known_missing=True):
        return _removed(context, batch_id)
    with state.session() as session:
        pending = _pending_jobs(session, batch_id)
    found = read_results(bucket.store, batch_id, pending)
    outcomes = _outcomes(found, pending, done=done)
    with state.session() as session:
        for job_id, outcome in outcomes.items():
            _record_result(session, job_id, outcome)
        phase: BatchPhase = READY_PHASE
        if done:
            phase = mark_transcribed(session, batch_id, context.ports.clock.now())
        statuses = _transcribe_statuses(session, batch_id)
    return _batch_results(batch_id, statuses, phase)


def _removed(context: RequestContext, batch_id: str) -> BatchResults:
    """Mark a `ready` batch another computer removed from the cloud; return it as it stands."""
    mark_removed(context, batch_id)
    with context.ports.state.session() as session:
        statuses = _transcribe_statuses(session, batch_id)
    return _batch_results(batch_id, statuses, READY_PHASE)


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
    """Set a job's status, failure reason, run and times from its result, unless it is no longer pending."""
    row = session.get(JobRow, job_id)
    if row is None or row.status != JOB_PENDING:
        return
    stored = JobPayload.model_validate(row.payload)
    worked = {
        "result_error": outcome.error,
        "result_error_code": outcome.error_code,
        "result_run_id": outcome.run_id,
        "result_started_at": outcome.started_at,
        "result_finished_at": outcome.finished_at,
    }
    payload = stored.model_copy(update=worked)
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


def _batch_results(batch_id: str, statuses: list[str], phase: BatchPhase) -> BatchResults:
    counted = Counter(statuses)
    return BatchResults(
        batch_id=batch_id,
        phase=phase,
        ok=counted["ok"],
        no_speech=counted["no_speech"],
        failed=counted["failed"],
        pending=counted[JOB_PENDING],
    )
