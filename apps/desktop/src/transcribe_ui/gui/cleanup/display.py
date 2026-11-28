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
from transcribe_ui.gui.cleanup.constants import RETRY_BATCH_KEY, WARNING_CONFIRMATIONS
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


def retry_title(batch_id: str) -> str:
    """Return the activity panel's title of a retry (it copies the audio of the ticked files)."""
    return tr("Retry of job {batch_id}: copying the audio").format(batch_id=batch_id)


def cleanup_title(target: CleanupTarget) -> str:
    """Return the activity panel's title of a cleanup."""
    if target.known and target.scope == "audio":
        text = tr("Deleting the audio of job {batch_id} from the cloud")
    elif target.known:
        text = tr("Deleting everything of job {batch_id} from the cloud")
    else:
        text = tr("Deleting another computer's job {batch_id} from the cloud")
    return text.format(batch_id=target.batch_id)


def _heading(target: CleanupTarget) -> str:
    if target.known and target.scope == "audio":
        text = tr("Delete the audio of job {batch_id} from the cloud?")
    elif target.known:
        text = tr("Delete everything of job {batch_id} from the cloud?")
    else:
        text = tr("Delete another computer's job {batch_id} from the cloud?")
    return text.format(batch_id=target.batch_id)


def _accept_label(target: CleanupTarget) -> str:
    if target.known and target.scope == "audio":
        return tr("Delete audio")
    if target.known:
        return tr("Delete everything")
    return tr("Delete from the cloud")


def cleanup_question(target: CleanupTarget, response: NeedsConfirmation) -> CleanupQuestion:
    """Return the confirmation dialog of a cleanup: its heading, the backend's confirmations, then that it is final."""
    shown = confirmation_view(response).confirmations
    lines = [
        QuestionLine(confirmation.message, warning=asked.code in WARNING_CONFIRMATIONS)
        for confirmation, asked in zip(shown, response.confirmations, strict=True)
    ]
    lines.append(QuestionLine(tr("This cannot be undone."), warning=False))
    return CleanupQuestion(
        title=tr("Delete from the cloud"),
        heading=_heading(target),
        lines=tuple(lines),
        accept_label=_accept_label(target),
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
    return tr("A cloud step of job {batch_id} is still running; it is shown below the pages.").format(
        batch_id=batch_id,
    )


def not_asked_text(batch_id: str) -> str:
    """Return the note shown when a cleanup's question arrives after the user went elsewhere."""
    return tr("Nothing was deleted from job {batch_id}; start the cleanup again when you need it.").format(
        batch_id=batch_id,
    )


def retry_ready_text(batch_id: str) -> str:
    """Return the note shown when a retry job is ready after the user went elsewhere."""
    return tr("The retry job {batch_id} is ready; it is on the Jobs page.").format(batch_id=batch_id)
