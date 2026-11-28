"""Texts and display data of the retry and cloud cleanup handlers (architecture §3.1 "GUI").

Qt-free. The cleanup question shows the backend's confirmations (each the catalog text of its
code; the one about subtitles not downloaded yet as a warning) under a heading that names the
job and what is deleted, with an accept button that says it. An error is shown from the catalog
like every other; `retry_copying` also gets a button that opens the retry job it names.
"""

from dataclasses import dataclass, replace

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation
from transcribe_ui.batches.payloads import CleanupScope
from transcribe_ui.cleanup.constants import ERROR_RETRY_COPYING
from transcribe_ui.gui.cleanup.constants import (
    CONFIRMATION_KINDS,
    RETRY_BATCH_KEY,
    WARNING_CONFIRMATIONS,
    CleanupKind,
)
from transcribe_ui.gui.feedback import ErrorView, Fix, confirmation_view, error_view
from transcribe_ui.i18n.catalog import tr


@dataclass(frozen=True)
class CleanupTarget:
    """What a cleanup deletes: a job of this computer (audio only or everything), or another computer's job."""

    batch_id: str
    scope: CleanupScope
    # False: another computer's job (Q28), which the start dialog lists.
    known: bool = True


@dataclass(frozen=True)
class QuestionLine:
    """One confirmation the backend asks for."""

    text: str
    # Shown as a warning (the subtitles may not be downloaded yet).
    warning: bool


@dataclass(frozen=True)
class CleanupQuestion:
    """The cleanup's confirmation dialog: what is deleted, the backend's confirmations, the accept button."""

    title: str
    heading: str
    lines: tuple[QuestionLine, ...]
    accept_label: str
    # What the confirmations say is deleted (the panel's title once confirmed).
    kind: CleanupKind


def retry_title(batch_id: str) -> str:
    """Return the activity panel's title of a retry (it copies the audio of the ticked files)."""
    return tr("Retry of job {batch_id}: copying the audio").format(batch_id=batch_id)


def target_kind(target: CleanupTarget) -> CleanupKind:
    """Return what the signal asked to delete."""
    return target.scope if target.known else "unknown"


def asked_kind(target: CleanupTarget, response: NeedsConfirmation, previous: CleanupKind | None = None) -> CleanupKind:
    """Return what the backend's confirmations say is deleted.

    Without such a code (a later round asking only what is left), what an earlier round's codes
    said (`previous`), else what the signal asked for.
    """
    codes = {confirmation.code for confirmation in response.confirmations}
    for code, kind in CONFIRMATION_KINDS.items():
        if code in codes:
            return kind
    return previous or target_kind(target)


def cleanup_title(batch_id: str, kind: CleanupKind | None) -> str:
    """Return the activity panel's title of a cleanup (`kind` None: the question's request, which deletes nothing)."""
    if kind is None:
        text = tr("Checking the cleanup of job {batch_id}")
    elif kind == "audio":
        text = tr("Deleting the audio of job {batch_id} from the cloud")
    elif kind == "everything":
        text = tr("Deleting everything of job {batch_id} from the cloud")
    else:
        text = tr("Deleting another computer's job {batch_id} from the cloud")
    return text.format(batch_id=batch_id)


def _heading(batch_id: str, kind: CleanupKind) -> str:
    if kind == "audio":
        text = tr("Delete the audio of job {batch_id} from the cloud?")
    elif kind == "everything":
        text = tr("Delete everything of job {batch_id} from the cloud?")
    else:
        text = tr("Delete another computer's job {batch_id} from the cloud?")
    return text.format(batch_id=batch_id)


def _accept_label(kind: CleanupKind) -> str:
    if kind == "audio":
        return tr("Delete audio")
    if kind == "everything":
        return tr("Delete everything")
    return tr("Delete from the cloud")


def cleanup_question(
    target: CleanupTarget,
    response: NeedsConfirmation,
    previous: CleanupKind | None = None,
) -> CleanupQuestion:
    """Return the confirmation dialog of a cleanup: its heading, the backend's confirmations, then that it is final.

    The heading and the accept button follow the backend's confirmation codes (without such a
    code, an earlier round's `previous` kind, else what the signal asked for).
    """
    shown = confirmation_view(response).confirmations
    lines = [
        QuestionLine(confirmation.message, warning=asked.code in WARNING_CONFIRMATIONS)
        for confirmation, asked in zip(shown, response.confirmations, strict=True)
    ]
    lines.append(QuestionLine(tr("This cannot be undone."), warning=False))
    kind = asked_kind(target, response, previous)
    return CleanupQuestion(
        title=tr("Delete from the cloud"),
        heading=_heading(target.batch_id, kind),
        lines=tuple(lines),
        accept_label=_accept_label(kind),
        kind=kind,
    )


def explained_error(response: ErrorResponse) -> ErrorView:
    """Return how an error of a retry or a cleanup is shown: `retry_copying` opens the retry job it names."""
    view = error_view(response)
    retry_batch_id = response.error.details.get(RETRY_BATCH_KEY)
    if response.error.code == ERROR_RETRY_COPYING and isinstance(retry_batch_id, str):
        return replace(view, fix=Fix("open_batch", tr("Open the retry job"), retry_batch_id))
    return view


def busy_text(batch_id: str) -> str:
    """Return the note shown when a job's retry or cleanup is asked for while another one of it runs."""
    return tr("A cloud step of job {batch_id} is still running in the activity panel below.").format(
        batch_id=batch_id,
    )


def not_asked_text(batch_id: str) -> str:
    """Return the note shown when a cleanup's question arrives after the user went elsewhere."""
    return tr("Nothing was deleted from job {batch_id}; start the cleanup again when you need it.").format(
        batch_id=batch_id,
    )


def retry_created_text(batch_id: str) -> str:
    """Return the note shown when a retry job was created after the user went elsewhere."""
    return tr("The retry job {batch_id} was created; it is on the Jobs page.").format(batch_id=batch_id)
