"""The review page's status lines, button states and the upload's progress (architecture §3.1 "GUI" "Review and upload").

Qt-free. The buttons follow the job's allowed actions (`BatchSummary.actions`); the page also
disables them while their precondition is in flight (a check, a save, the job's upload) or not
met (choices not applied), and the hint says why. The upload shows its files done and the bytes
of the current object from its progress events, and the outcome once it finished.
"""

from dataclasses import dataclass
from pathlib import PurePath

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.gui.jobs.display import button_label
from transcribe_ui.gui.review.constants import (
    EXCLUDE_ACTION,
    ITEMS_IN_REVIEW_OUTCOME,
    LIST_SEPARATOR,
    NAMES_SHOWN,
    PERCENT,
    RESOLVE_ACTION,
    REVIEW_ACTION,
    UPLOAD_ACTION,
    ReviewState,
)
from transcribe_ui.gui.review.display import file_names
from transcribe_ui.gui.review.uploads import UploadStatus
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.gui.selection.display import size_text
from transcribe_ui.gui.texts import activity_state_text
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.i18n.formats import format_number
from transcribe_ui.i18n.texts import code_text, progress_text


@dataclass(frozen=True)
class Buttons:
    """Which of the page's buttons show and which can be pressed, with the hint of what blocks them."""

    apply_shown: bool
    apply_enabled: bool
    remove_shown: bool
    remove_enabled: bool
    upload_shown: bool
    upload_enabled: bool
    upload_label: str
    # Choices can change (the combo boxes).
    choose_enabled: bool
    # "Transcribe all shown" and "Extract all shown" apply (choices can change, the embedded-subtitle view has rows).
    bulk_enabled: bool
    check_enabled: bool
    # The filters can change (not while choices wait for Apply).
    filters_enabled: bool
    hint: str


@dataclass(frozen=True)
class UploadView:
    """A job's upload on the review page."""

    state: str
    files: str
    part: str
    # The share of the current object sent, in percent (None: no object is being sent).
    percent: int | None
    can_cancel: bool
    can_resume: bool
    # Resume (and the error's retry) can be pressed now; else `blocked` says why.
    resume_enabled: bool
    blocked: str
    error: ErrorView | None


def _allowed(model: ReviewModel, action: str) -> bool:
    detail = model.loader.loaded.detail
    return detail is not None and action in detail.summary.actions


def _uploading(model: ReviewModel) -> bool:
    batch_id = model.loader.batch_id
    return batch_id is not None and model.uploads.busy(batch_id)


def changes_hint() -> str:
    """Return why the filters and the upload wait while choices are not applied."""
    return tr("Apply or discard your changes before changing the filters or uploading.")


def _hint(model: ReviewModel) -> str:
    """Return what blocks the page's actions now (the first that applies), or ""."""
    review = model.loader.loaded.review
    blocked = (
        (_uploading(model), tr("The job is uploading; its review waits until the upload stops.")),
        (model.sender.acting(), tr("Saving…")),
        (model.sender.reviewing(started_only=True), tr("Wait until the files are checked.")),
        (bool(model.editor.draft.keys()), changes_hint()),
        (
            review is not None and not review.complete,
            tr("Choose for the files that need attention, or remove them from the job, before the upload."),
        ),
    )
    return next((text for applies, text in blocked if applies), "")


def buttons(model: ReviewModel) -> Buttons:
    """Return the states of the page's buttons and the hint."""
    free = model.editable()
    pending = bool(model.editor.draft.keys())
    subtitles = model.editor.views.current().tabs["subtitles"].shapes
    review = model.loader.loaded.review
    detail = model.loader.loaded.detail
    upload_allowed = _allowed(model, UPLOAD_ACTION)
    incomplete = review is not None and not review.complete
    return Buttons(
        apply_shown=pending,
        apply_enabled=pending and free and _allowed(model, RESOLVE_ACTION),
        remove_shown=_allowed(model, EXCLUDE_ACTION),
        remove_enabled=bool(model.editor.ticked()) and free,
        upload_shown=upload_allowed or incomplete,
        upload_enabled=upload_allowed and not pending and free,
        upload_label="" if detail is None else button_label("upload", detail.summary),
        choose_enabled=review is not None and free and _allowed(model, RESOLVE_ACTION),
        bulk_enabled=bool(subtitles) and free and _allowed(model, RESOLVE_ACTION),
        check_enabled=free and _allowed(model, REVIEW_ACTION),
        filters_enabled=not pending,
        hint=_hint(model),
    )


def _counts_text(model: ReviewModel) -> str:
    review = model.loader.loaded.review
    if review is None:
        loaded_job = model.loader.loaded.detail is not None
        nothing = loaded_job and not _allowed(model, REVIEW_ACTION)
        return tr("No file of this job waits for review.") if nothing else ""
    if not review.items_in_review:
        return tr("No file of this job waits for review.")
    files = ntr("{count} file in review", "{count} files in review", review.items_in_review)
    in_review = files.format(count=format_number(review.items_in_review))
    if review.complete:
        return tr("{files}; every one is ready to upload.").format(files=in_review)
    unresolved = ntr("{count} needs attention", "{count} need attention", review.items_unresolved)
    return f"{in_review}; {unresolved.format(count=format_number(review.items_unresolved))}."


def _checking_text(event: ProgressEvent | None) -> str:
    if event is None:
        return tr("Checking the files…")
    if event.path is None:
        return progress_text(event)
    return f"{progress_text(event)}: {PurePath(event.path).name}"


def review_status(model: ReviewModel) -> str:
    """Return the line above the review: the check's progress, a stop, the upload's wait, or the counts."""
    loaded = model.loader.loaded
    texts: dict[ReviewState, str] = {
        "queued": tr("Waiting to check the files; you can keep choosing meanwhile, or stop checking."),
        "checking": _checking_text(loaded.probe),
        "stopped": tr("Checking stopped; continue any time with Check again."),
        "waiting_upload": tr("The job is uploading; its review continues once the upload stops."),
    }
    return texts.get(loaded.state) or _counts_text(model)


def _names(model: ReviewModel, item_ids: tuple[str, ...]) -> str:
    names = list(file_names(model, item_ids))
    shown = LIST_SEPARATOR.join(names[:NAMES_SHOWN])
    more = len(names) - NAMES_SHOWN
    if more > 0:
        return tr("{names} and {count} more").format(names=shown, count=format_number(more))
    return shown


def _outcome_text(model: ReviewModel, status: UploadStatus) -> str:
    finished = status.finished
    if finished is None:
        return activity_state_text(status.entry.state)
    if finished.outcome == ITEMS_IN_REVIEW_OUTCOME:
        count = len(finished.items_in_review)
        text = ntr(
            "{count} file is back in review (it changed, or needs a choice): {names}. Review it, then resume the upload.",
            "{count} files are back in review (they changed, or need a choice): {names}. "
            "Review them, then resume the upload.",
            count,
        )
        names = _names(model, finished.items_in_review)
        return text.format(count=format_number(count), names=names)
    texts = {
        "ready": tr("Upload complete: the job is ready for transcription."),
        "held": code_text("batch_label", "manifest_held"),
        "transcribed": tr("Done: the embedded subtitles are extracted; download them from the job page."),
    }
    return texts.get(finished.outcome, activity_state_text(status.entry.state))


def _files_text(status: UploadStatus) -> str:
    files = status.files
    if files is None:
        return tr("Preparing the upload…") if status.part is None else ""
    total = files.total or 0
    done = {"done": format_number(files.completed), "total": format_number(total)}
    return tr("Files uploaded: {done} of {total}").format_map(done)


def _part(status: UploadStatus) -> tuple[str, int | None]:
    part = status.part
    if part is None or not part.total:
        return "", None
    sent = {"sent": size_text(part.completed), "size": size_text(part.total)}
    percent = part.completed * PERCENT // part.total
    return tr("Current file: {sent} of {size} sent").format_map(sent), percent


def _state_text(model: ReviewModel, status: UploadStatus) -> str:
    entry = status.entry
    if entry.state == "finished":
        return _outcome_text(model, status)
    if entry.error is not None:
        return entry.error.message
    return activity_state_text(entry.state)


def upload_view(model: ReviewModel) -> UploadView | None:
    """Return the job's upload as the page shows it, or None when it has none in this session."""
    batch_id = model.loader.batch_id
    if batch_id is None:
        return None
    status = model.uploads.status(batch_id)
    if status is None:
        return None
    entry = status.entry
    running = entry.state == "running"
    part, percent = _part(status) if running else ("", None)
    state = buttons(model)
    return UploadView(
        state=_state_text(model, status),
        files=_files_text(status) if running else "",
        part=part,
        percent=percent,
        can_cancel=entry.can_cancel,
        can_resume=entry.can_resume,
        resume_enabled=state.upload_enabled,
        blocked="" if state.upload_enabled else state.hint,
        error=entry.error,
    )
