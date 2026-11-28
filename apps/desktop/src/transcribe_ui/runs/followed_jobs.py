"""The followed jobs of a run, read from the database (`runs/remaining.py`).

The transcribe jobs of the run's covered batches: those still pending, each with its audio's
duration (the verified audio object's, as in the manifest, so it matches the launch scope), and
those whose result this run wrote, with the time the worker spent on them.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ui.batches.constants import JOB_PENDING
from transcribe_ui.batches.payloads import JobPayload
from transcribe_ui.runs.classes import PendingJob
from transcribe_ui.runs.constants import PACED_JOB_STATUSES, TRANSCRIBE_KIND
from transcribe_ui.runs.estimate import job_weight
from transcribe_ui.runs.manifests import pending_of_batches
from transcribe_ui.state.models import AudioObjectRow, BatchRow, JobRow
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import AUDIO_VERIFIED
from transcribe_ui.upload.payloads import AudioObjectPayload

# Durations of the verified audio objects, by item id and audio track (None: an audio file).
type AudioDurations = dict[tuple[str, int | None], float]


class WorkedJob(NamedTuple):
    """A job whose result the run wrote: its weighted audio and the worker's time on it (worker clock)."""

    weighted_s: float
    started_at: datetime
    finished_at: datetime
    # Whether its time measures the worker's pace (`ok` or `no_speech`; a failure may have taken none).
    paced: bool

    @property
    def spent_s(self) -> float:
        """Return the seconds the worker spent on the job."""
        return (self.finished_at - self.started_at).total_seconds()


class FollowedJobs(NamedTuple):
    """A run's followed jobs still pending, those whose result the run wrote, and the covered batches not here."""

    pending: tuple[PendingJob, ...] = ()
    worked: tuple[WorkedJob, ...] = ()
    # Covered batches this database does not know (another computer's; their jobs are in R2 only).
    unknown_batch_ids: tuple[str, ...] = ()
    # The pending jobs of those batches, read from R2 (`with_others`).
    others: tuple[PendingJob, ...] = ()

    def with_others(self, store: ObjectStore) -> "FollowedJobs":
        """Return the jobs with the pending jobs of the covered batches this database does not know, read from R2.

        Raises:
            StorageError: R2 could not be read.
        """
        if not self.unknown_batch_ids:
            return self
        return self._replace(others=tuple(pending_of_batches(store, self.unknown_batch_ids)))


def followed_jobs(
    session: Session,
    covered: Sequence[str],
    known: Sequence[str] | None,
    run_id: str,
) -> FollowedJobs:
    """Return the pending transcribe jobs of the run's known covered batches and those whose result the run wrote.

    `known`: the covered batches the database knew at the launch (`RunPayload.known_batch_ids`),
    so a batch adopted during the run stays counted from R2, as the launch counted it; None for a
    run launched before they were kept: the covered batches the database has.
    """
    if known is None:
        known = _in_database(session, covered)
    durations = _durations(session, known)
    of_batches = JobRow.batch_id.in_(known)
    query = select(JobRow).where(of_batches, JobRow.kind == TRANSCRIBE_KIND)
    pending: list[PendingJob] = []
    worked: list[WorkedJob] = []
    for row in session.scalars(query):
        stored = JobPayload.model_validate(row.payload)
        job = _job(row, stored, durations)
        if row.status == JOB_PENDING:
            pending.append(job)
        elif stored.result_run_id == run_id:
            done = _worked_job(job, stored, paced=row.status in PACED_JOB_STATUSES)
            if done is not None:
                worked.append(done)
    return FollowedJobs(tuple(pending), tuple(worked), _others(covered, known))


def _in_database(session: Session, batch_ids: Sequence[str]) -> tuple[str, ...]:
    """Return the batches the database has."""
    query = select(BatchRow.batch_id).where(BatchRow.batch_id.in_(batch_ids))
    return tuple(session.scalars(query))


def _others(covered: Sequence[str], known: Sequence[str]) -> tuple[str, ...]:
    """Return the covered batches not known, in order."""
    here = frozenset(known)
    return tuple(batch_id for batch_id in covered if batch_id not in here)


def _job(row: JobRow, stored: JobPayload, durations: AudioDurations) -> PendingJob:
    """Return the job with its audio's duration (0 when no verified audio object is recorded)."""
    duration = durations.get((row.item_id, stored.track_index), 0)
    mode = stored.mode
    return PendingJob(item_id=row.item_id, language=row.language, mode=mode, duration_s=duration)


def _worked_job(job: PendingJob, stored: JobPayload, *, paced: bool) -> WorkedJob | None:
    started = stored.result_started_at
    finished = stored.result_finished_at
    if started is None or finished is None:
        return None
    return WorkedJob(job.duration_s * job_weight(job), started, finished, paced=paced)


def _durations(session: Session, batch_ids: Sequence[str]) -> AudioDurations:
    of_batches = AudioObjectRow.batch_id.in_(batch_ids)
    query = select(AudioObjectRow).where(of_batches, AudioObjectRow.status == AUDIO_VERIFIED)
    durations: AudioDurations = {}
    for row in session.scalars(query):
        stored = AudioObjectPayload.model_validate(row.payload)
        durations[row.item_id, stored.track_index] = stored.duration_s
    return durations
