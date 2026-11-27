"""Carrying out a validated placement plan (`run_download`, `import_custom_plan`; plan m5 §1 "Download").

The caller holds the batch's lock. A plan with errors is refused before anything is written; a plan
that would overwrite files needs `confirm_overwrite` (answered with `needs_confirmation`, nothing
changed). Then job by job, in plan order, with the cancel token checked between jobs:

1. a job the plan writes gets its subtitle: an extracted one from SQLite, a transcribed one from
   R2 (opened on first use, so extracted subtitles need no R2 credentials), an empty result as an
   empty file;
2. it is written through a temp file and `os.replace` (`placement/writer.py`), the target checked
   again first (an existing target is skipped unless overwriting, a vanished original folder is
   skipped as missing);
3. its outcome is recorded in its own transaction (`placement/recording.py`), skipped jobs' too.

No R2 call and no file write happens inside a transaction. A cancel, a failure or a kill keeps the
outcomes recorded so far; sending the request again places the rest (files already placed are
then existing targets, skipped unless overwriting). Finally the `downloaded` rule is applied.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from transcribe_contracts.layout import result_srt_key
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation, ProgressEvent
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.placement.classes import DownloadOutcome, PlacementOutcome, PlacementPlan, PlannedPlacement
from transcribe_ui.placement.constants import DOWNLOAD_STAGE, EXTRACT_KIND, SUBTITLE_ENCODING, WRITING_OUTCOMES
from transcribe_ui.placement.errors import DownloadPlanInvalidError, DownloadWriteFailedError, SubtitleMissingError
from transcribe_ui.placement.recording import Placed, mark_downloaded, record_placement
from transcribe_ui.placement.sources import DownloadJob, DownloadSource
from transcribe_ui.placement.writer import place_file
from transcribe_ui.runs.cloud import LazyStore
from transcribe_ui.state.models import ExtractedSubtitleRow

# Request field that confirms overwriting existing files.
CONFIRM_OVERWRITE_FIELD = "confirm_overwrite"


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
    """Place the plan's subtitles and record each job's outcome; return what was done.

    Raises:
        DownloadPlanInvalidError: The plan has errors (nothing was written).
        DownloadWriteFailedError: A subtitle could not be written (the jobs before it are recorded).
        SubtitleMissingError: A finished job's subtitle is not in R2 (the jobs before it are recorded).
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
    for entry in plan.entries:
        context.check_cancelled()
        outcome = placer.place(jobs[entry.item_id, entry.language], entry)
        done.append(entry.model_copy(update={"outcome": outcome}))
        placer.report(len(done), len(plan.entries), entry.target_path)
    with context.ports.state.session() as session:
        mark_downloaded(session, source.batch_id, context.ports.clock.now())
    return _outcome(done)


def _overwrite_confirmation(action: str, entries: Sequence[PlannedPlacement]) -> NeedsConfirmation | None:
    # Targets named by file name only: confirmation details are not path-normalised.
    overwriting = [entry.target_path or "" for entry in entries if entry.outcome == "overwrite"]
    replaced = [Path(target).name for target in overwriting]
    if not replaced:
        return None
    confirmation = Confirmation(
        field=CONFIRM_OVERWRITE_FIELD,
        message=f"{len(replaced)} existing subtitle file(s) will be replaced.",
        details={"targets": list(replaced)},
    )
    return NeedsConfirmation(action=action, confirmations=(confirmation,))


@dataclass(frozen=True)
class _JobPlacer:
    """Carries out one job of a plan at a time."""

    context: RequestContext
    store: LazyStore
    batch_id: str
    carrying: Carrying

    def place(self, job: DownloadJob, entry: PlannedPlacement) -> PlacementOutcome:
        """Write the job's subtitle if the plan says so, record the outcome and return it.

        Raises:
            DownloadWriteFailedError: The subtitle could not be written.
            SubtitleMissingError: A transcribed subtitle is not in R2.
            DomainError: R2 or its credentials failed.
        """
        outcome = self._written(job, entry)
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
        body = self._body(job)
        overwrite = entry.outcome == "overwrite"
        next_to_media = self.carrying.next_to_media
        try:
            return place_file(Path(target), body, overwrite=overwrite, next_to_media=next_to_media)
        except OSError as error:
            raise DownloadWriteFailedError(job.job, error) from None

    def _body(self, job: DownloadJob) -> bytes:
        if job.job.subtitle == "empty":
            return b""
        if job.kind == EXTRACT_KIND:
            return self._extracted(job)
        key = result_srt_key(self.batch_id, job.job.item_id, job.job.language)
        body = self.store.get().read(key)
        if body is None:
            raise SubtitleMissingError(job.job)
        return body

    def _extracted(self, job: DownloadJob) -> bytes:
        with self.context.ports.state.session() as session:
            row = session.get(ExtractedSubtitleRow, job.job_id)
            text = None if row is None else row.srt_text
        if text is None:
            raise SubtitleMissingError(job.job)
        return text.encode(SUBTITLE_ENCODING)


def _outcome(done: Sequence[PlannedPlacement]) -> DownloadOutcome:
    counted = Counter(entry.outcome for entry in done)
    written = counted["place"] + counted["overwrite"]
    not_finished = counted["not_finished"]
    return DownloadOutcome(
        placements=tuple(done),
        written=written,
        skipped=len(done) - written - not_finished,
        not_finished=not_finished,
    )
