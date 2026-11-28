"""Carrying out a validated placement plan (`run_download`, `import_custom_plan`; plan m5 §1 "Download").

The caller holds the batch's lock. A plan with errors is refused before anything is written; a plan
that would overwrite files needs `confirm_overwrite` (answered with `needs_confirmation`, nothing
changed). Then job by job, in plan order, with the cancel token checked between jobs:

1. the target is checked again (`placement/writer.py`): an existing target is skipped unless
   overwriting, a vanished original folder is skipped as missing; nothing is read for a skip;
2. a job to write gets its subtitle: an extracted one from SQLite, a transcribed one from R2
   (opened on first use, so extracted subtitles need no R2 credentials), an empty result as an
   empty file;
3. it is written through a temp file and moved into place (`placement/writer.py`);
4. its outcome is recorded in its own transaction (`placement/recording.py`), skipped jobs' too.

A job that cannot be placed (its subtitle is gone from R2, or the OS refuses the write, such as a
Windows target held open by a player) is listed in `failures` with nothing recorded, and the other
jobs go on; sending the request again tries it again. Failures that concern every job (R2 or its
credentials) fail the request. No R2 call and no file write happens inside a transaction. A cancel,
a failure or a kill keeps the outcomes recorded so far; sending the request again places the rest
(files already placed are then existing targets, skipped unless overwriting). Finally the
`downloaded` rule is applied.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation, ProgressEvent
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.placement.classes import (
    DownloadFailure,
    DownloadOutcome,
    PlacementOutcome,
    PlacementPlan,
    PlannedPlacement,
)
from transcribe_ui.placement.constants import (
    CONFIRM_OVERWRITE_FIELD,
    CONFIRMATION_OVERWRITE,
    DOWNLOAD_STAGE,
    ERROR_DOWNLOAD_WRITE_FAILED,
    ERROR_SUBTITLE_MISSING,
    EXTRACT_KIND,
    SUBTITLE_ENCODING,
    WRITING_OUTCOMES,
)
from transcribe_ui.placement.errors import DownloadPlanInvalidError
from transcribe_ui.placement.planner import job_file, job_label
from transcribe_ui.placement.recording import Placed, mark_downloaded, record_placement
from transcribe_ui.placement.sources import DownloadJob, DownloadSource
from transcribe_ui.placement.writer import check_target, place_file
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.state.models import ExtractedSubtitleRow


@dataclass(frozen=True)
class Carrying:
    """How a plan is carried out."""

    # The request's action (progress events and the confirmation name it).
    action: str
    # Next to the media, a folder is never created (a missing one is skipped).
    next_to_media: bool
    confirm_overwrite: bool


def carry_out(
    context: RequestContext,
    source: DownloadSource,
    plan: PlacementPlan,
    carrying: Carrying,
) -> DownloadOutcome | NeedsConfirmation:
    """Place the plan's subtitles and record each job's outcome; return what was done and what failed.

    Raises:
        DownloadPlanInvalidError: The plan has errors (nothing was written).
        CancelledError: Cancelled between jobs (the jobs before are recorded).
        DomainError: R2 or its credentials failed (the jobs before are recorded).
    """
    if plan.errors:
        raise DownloadPlanInvalidError(plan.errors)
    if not carrying.confirm_overwrite:
        confirmation = _overwrite_confirmation(carrying.action, plan.entries)
        if confirmation is not None:
            return confirmation
    placer = _JobPlacer(context, LazyStore(context), source.batch_id, carrying)
    jobs = source.by_key()
    done: list[PlannedPlacement] = []
    failures: list[DownloadFailure] = []
    for index, entry in enumerate(plan.entries, start=1):
        context.check_cancelled()
        outcome = placer.place(jobs[entry.item_id, entry.language], entry)
        if isinstance(outcome, DownloadFailure):
            failures.append(outcome)
        else:
            done.append(entry.model_copy(update={"outcome": outcome}))
        placer.report(index, len(plan.entries), entry.target_path)
    with context.ports.state.session() as session:
        mark_downloaded(session, source.batch_id, context.ports.clock.now())
    return _outcome(done, failures)


def _overwrite_confirmation(action: str, entries: Sequence[PlannedPlacement]) -> NeedsConfirmation | None:
    # Targets named by file name only: confirmation details are not path-normalised.
    overwriting = [entry.target_path or "" for entry in entries if entry.outcome == "overwrite"]
    replaced = [Path(target).name for target in overwriting]
    if not replaced:
        return None
    confirmation = Confirmation(
        code=CONFIRMATION_OVERWRITE,
        field=CONFIRM_OVERWRITE_FIELD,
        set_to=True,
        message=f"{len(replaced)} existing subtitle file(s) will be replaced.",
        details={"count": len(replaced), "targets": list(replaced)},
    )
    return NeedsConfirmation(action=action, confirmations=(confirmation,))


class _JobFailedError(Exception):
    """One job cannot be placed; the download goes on with the others."""

    def __init__(self, failure: DownloadFailure) -> None:
        """Keep the failure."""
        super().__init__(failure.message)
        self.failure = failure


def _failure(job: DownloadJob, entry: PlannedPlacement, error: OSError | None = None) -> _JobFailedError:
    label = job_label(job.job)
    failure = DownloadFailure(
        item_id=job.job.item_id,
        language=job.job.language,
        target_path=entry.target_path,
        code=ERROR_SUBTITLE_MISSING,
        message=f"{label}: the subtitle is not in the cloud any more.",
        details={"file": job_file(job.job), "language": job.job.language},
    )
    if error is not None:
        message = f"{label}: the subtitle cannot be written; send the request again once the target is writable."
        update = {"code": ERROR_DOWNLOAD_WRITE_FAILED, "message": message, "reason": type(error).__name__}
        failure = failure.model_copy(update=update)
    return _JobFailedError(failure)


@dataclass(frozen=True)
class _JobPlacer:
    """Carries out one job of a plan at a time."""

    context: RequestContext
    store: LazyStore
    batch_id: str
    carrying: Carrying

    def place(self, job: DownloadJob, entry: PlannedPlacement) -> PlacementOutcome | DownloadFailure:
        """Write the job's subtitle if the plan says so and record the outcome; return it, or the job's failure.

        Raises:
            DomainError: R2 or its credentials failed.
        """
        try:
            outcome = self._written(job, entry)
        except _JobFailedError as failed:
            return failed.failure
        placed = Placed(job_id=job.job_id, outcome=outcome, target_path=entry.target_path)
        ports = self.context.ports
        with ports.state.session() as session:
            record_placement(session, self.batch_id, placed, ports.clock.now())
        return outcome

    def report(self, completed: int, total: int, target_path: str | None) -> None:
        """Send the progress after a job."""
        action = self.carrying.action
        event = ProgressEvent(action=action, stage=DOWNLOAD_STAGE, completed=completed, total=total)
        self.context.progress.emit(event.model_copy(update={"path": target_path}))

    def _written(self, job: DownloadJob, entry: PlannedPlacement) -> PlacementOutcome:
        target = entry.target_path
        if entry.outcome not in WRITING_OUTCOMES or target is None:
            return entry.outcome
        overwrite = entry.outcome == "overwrite"
        next_to_media = self.carrying.next_to_media
        skip = check_target(Path(target), overwrite=overwrite, next_to_media=next_to_media)
        if skip is not None:
            return skip
        body = self._body(job, entry)
        try:
            return place_file(Path(target), body, overwrite=overwrite, next_to_media=next_to_media)
        except OSError as error:
            raise _failure(job, entry, error) from None

    def _body(self, job: DownloadJob, entry: PlannedPlacement) -> bytes:
        if job.job.subtitle == "empty":
            return b""
        if job.kind == EXTRACT_KIND:
            text = self._extracted(job)
            if text is None:
                raise _failure(job, entry)
            return text.encode(SUBTITLE_ENCODING)
        key = result_srt_key(self.batch_id, job.job.item_id, job.job.language)
        body = self.store.get().read(key)
        if body is None:
            raise _failure(job, entry)
        return body

    def _extracted(self, job: DownloadJob) -> str | None:
        with self.context.ports.state.session() as session:
            row = session.get(ExtractedSubtitleRow, job.job_id)
            return None if row is None else row.srt_text


def _outcome(done: Sequence[PlannedPlacement], failures: Sequence[DownloadFailure]) -> DownloadOutcome:
    written = sum(entry.outcome in WRITING_OUTCOMES for entry in done)
    not_finished = sum(entry.outcome == "not_finished" for entry in done)
    return DownloadOutcome(
        placements=tuple(done),
        failures=tuple(failures),
        written=written,
        skipped=len(done) - written - not_finished,
        not_finished=not_finished,
    )
