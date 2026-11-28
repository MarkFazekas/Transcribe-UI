"""Display data of the jobs list and the job page (architecture §3.1 "GUI").

Qt-free. Everything shown comes from the backend's summaries and details: the stage, the
phase, the labels (rendered generically from the catalog, so a new label code needs only its
entry), the item and job states, the failure reasons, and the buttons, which follow the
allowed actions alone. Only presentation is decided here: texts, number and date formats, and
which button an action shows as.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from transcribe_ui.batches.payloads import RetryJob
from transcribe_ui.batches.responses import BatchDetail, BatchItem, BatchLabel, BatchStage, BatchSummary, JobView
from transcribe_ui.gui.jobs.constants import ACTION_BUTTONS, CELL_SEPARATOR, CLEANUP_BUTTONS, JobButtonKey
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.formats import format_datetime, format_number
from transcribe_ui.i18n.texts import code_text


@dataclass(frozen=True)
class JobButton:
    """A button of the job page (or of the list's selected job)."""

    key: JobButtonKey
    label: str
    # Whether it can be pressed now (Retry needs a ticked job).
    enabled: bool = True


@dataclass(frozen=True)
class JobRow:
    """A job (batch) as the jobs list and the job page's header show it."""

    batch_id: str
    created: str
    stage: BatchStage
    stage_text: str
    status: str
    files: str
    transcriptions: str
    languages: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class TranscriptionRow:
    """One transcription (job) of a file on the job page."""

    file_name: str
    path: str
    file_status: str
    # The file's problem (gone, not checked, changing), or "".
    problem: str
    language: str
    kind: str
    status: str
    # Why the transcription failed (the catalog text of its code, else the worker's own error), or "".
    reason: str
    # It failed or found no speech.
    flagged: bool
    # What a Retry takes when this row is ticked; None when the row has no tick box.
    retry: RetryJob | None


def created_text(moment: datetime) -> str:
    """Return a moment in this computer's time zone, as the active language writes it."""
    return format_datetime(moment.astimezone())


def label_texts(labels: tuple[BatchLabel, ...]) -> tuple[str, ...]:
    """Return the catalog text of each label (a code without an entry shows as itself)."""
    return tuple(code_text("batch_label", label) for label in labels)


def job_row(summary: BatchSummary) -> JobRow:
    """Return a batch summary as the list and the header show it."""
    return JobRow(
        batch_id=summary.batch_id,
        created=created_text(summary.created_at),
        stage=summary.stage,
        stage_text=code_text("batch_stage", summary.stage),
        status=code_text("batch_phase", summary.phase),
        files=format_number(summary.items),
        transcriptions=format_number(summary.jobs),
        languages=CELL_SEPARATOR.join(summary.languages),
        labels=label_texts(summary.labels),
    )


def reason_text(job: JobView) -> str:
    """Return why a transcription failed: the catalog text of the desktop's code, else the worker's error as it is."""
    if job.result_error_code is not None:
        return code_text("result_error", job.result_error_code, {}, job.result_error)
    return job.result_error or ""


def _problem_text(member: BatchItem) -> str:
    problem = member.problem
    if problem is None:
        return ""
    return code_text("item_problem", problem.code, {}, problem.message)


def _retry_job(member: BatchItem, job: JobView, *, retry_open: bool) -> RetryJob | None:
    """Return what a Retry of this job takes, or None when it gets no tick box (not flagged, or no retry allowed)."""
    if not retry_open or not job.flagged:
        return None
    return RetryJob(item_id=member.item_id, language=job.language)


def _transcription_row(member: BatchItem, job: JobView, *, retry_open: bool) -> TranscriptionRow:
    return TranscriptionRow(
        file_name=member.name,
        path=member.path,
        file_status=code_text("item_phase", member.phase),
        problem=_problem_text(member),
        language=job.language,
        kind=code_text("job_kind", job.kind),
        status=code_text("job_status", job.status),
        reason=reason_text(job),
        flagged=job.flagged,
        retry=_retry_job(member, job, retry_open=retry_open),
    )


def transcription_rows(detail: BatchDetail) -> tuple[TranscriptionRow, ...]:
    """Return every transcription of every file, in the batch's order; tick boxes only while a retry is allowed."""
    retry_open = "create_retry_batch" in detail.summary.actions
    return tuple(
        _transcription_row(member, job, retry_open=retry_open) for member in detail.items for job in member.jobs
    )


def button_label(key: JobButtonKey, summary: BatchSummary) -> str:
    """Return a button's label; a cleanup the backend reports interrupted reads "Repeat cleanup"."""
    pending = summary.cleanup_pending
    if pending is not None and CLEANUP_BUTTONS.get(key) == pending:
        return tr("Repeat cleanup")
    if key == "upload" and "upload_unfinished" in summary.labels:
        return tr("Resume upload")
    labels: dict[JobButtonKey, str] = {
        "edit_selection": tr("Edit selection"),
        "continue_review": tr("Continue to review"),
        "delete": tr("Delete"),
        "review": tr("Review"),
        "upload": tr("Upload"),
        "download": tr("Download"),
        "retry": tr("Retry ticked files"),
        "cleanup_audio": tr("Delete audio from the cloud"),
        "cleanup_everything": tr("Delete everything from the cloud"),
        "hide": tr("Hide"),
        "show": tr("Show"),
    }
    return labels[key]


def job_buttons(
    summary: BatchSummary,
    only: frozenset[JobButtonKey] | None = None,
) -> tuple[JobButton, ...]:
    """Return the buttons of the allowed actions in their order, each once (`only`: just these keys)."""
    keys: dict[JobButtonKey, None] = {}
    for action in summary.actions:
        key = ACTION_BUTTONS.get(action)
        wanted = only is None or key in only
        if key is not None and wanted:
            keys[key] = None
    return tuple(JobButton(key, button_label(key, summary)) for key in keys)


def with_retry_state(buttons: tuple[JobButton, ...], *, ticked: bool) -> tuple[JobButton, ...]:
    """Return the buttons with Retry enabled only while a job is ticked."""
    shown = []
    for button in buttons:
        retry = button.key == "retry"
        shown.append(replace(button, enabled=ticked) if retry else button)
    return tuple(shown)
