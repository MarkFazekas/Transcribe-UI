"""The handlers of `clean_up` and `delete_unknown_batch`: cloud cleanup of a job (architecture §3.1 "GUI").

Qt-free. A cleanup (audio only or everything, also the repeat of an interrupted one, and
another computer's job from the start dialog, Q28) first sends `cleanup_cloud` without a
confirmation, in the activity panel; the backend answers with what it needs confirmed
(`confirm`, and `confirm_not_downloaded` when the subtitles may not be downloaded yet; one
confirmation for another computer's job), which the cleanup question shows. That request is
titled "Checking the cleanup" and leaves the panel once answered: it deletes nothing. Confirm
re-sends the same request with each `set_to`, which deletes in the panel with progress, Cancel
and Resume (re-sending finishes an interrupted cleanup); its title says what is deleted. A
refusal of the unconfirmed request opens the error dialog (`retry_copying` with a button to the
retry job). A question or a refusal that arrives after the user went to another page, job or
step (the start dialog closed) opens nothing: a note says that nothing was deleted.

After the deletes (also a failed or stopped one, which may have deleted some) the job pages
reload, so the labels show what is left (`cleanup_pending` until a repeat finishes); for
another computer's job, `plan_invalidated` makes the start dialog plan again.
"""

import logging
from dataclasses import dataclass
from functools import partial

from pydantic import ValidationError

from transcribe_ui.api.classes import ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.gui.cleanup.constants import PLAN_INVALIDATED_SIGNAL, SCOPES, UNKNOWN_SCOPE, CleanupKind
from transcribe_ui.gui.cleanup.display import (
    CleanupTarget,
    asked_kind,
    cleanup_question,
    cleanup_title,
    explained_error,
    not_asked_text,
    target_kind,
)
from transcribe_ui.gui.cleanup.panel import JobSteps
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.feedback import confirmation_mismatch, confirmed_request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Sent:
    """A cleanup request in flight: what it deletes, the request, and the navigation count when it was asked for."""

    target: CleanupTarget
    request: CleanupCloudRequest
    generation: int
    # What the confirmed request deletes (its panel title); None for the question's request.
    kind: CleanupKind | None = None


def cleanup_request(target: CleanupTarget) -> CleanupCloudRequest:
    """Return the unconfirmed cleanup request of `target` (the backend answers what it needs confirmed)."""
    return CleanupCloudRequest(action="cleanup_cloud", batch_id=target.batch_id, scope=target.scope)


class CleanupModel:
    """Cloud cleanup of a job (audio only or everything), and deletion of another computer's job."""

    def __init__(self, steps: JobSteps) -> None:
        """Run the cleanups as steps of their job in the panel."""
        self._steps = steps

    def clean_up(self, batch_id: str, scope: str) -> None:
        """Ask what a cleanup of the job (`clean_up`: audio only or everything) needs confirmed."""
        known_scope = SCOPES.get(scope)
        if known_scope is None:
            logger.error("Unknown cleanup scope %r for %s", scope, batch_id)
            return
        self._ask(CleanupTarget(batch_id, known_scope))

    def delete_unknown(self, batch_id: str) -> None:
        """Ask what deleting another computer's job (`delete_unknown_batch`, Q28) from the cloud needs confirmed."""
        self._ask(CleanupTarget(batch_id, UNKNOWN_SCOPE, known=False))

    def _ask(self, target: CleanupTarget) -> None:
        asked = _Sent(target, cleanup_request(target), self._steps.generation)
        self._send(asked)

    def _send(self, sent: _Sent) -> None:
        target = sent.target
        answered = partial(self._answered, sent)
        title = cleanup_title(target.batch_id, sent.kind)
        self._steps.start(target.batch_id, sent.request, title, answered)

    def _answered(self, sent: _Sent, response: Response) -> None:
        if isinstance(response, NeedsConfirmation):
            self._steps.dismiss(sent.target.batch_id)
            self._question(sent, response)
        elif sent.request.confirm:
            if isinstance(response, ErrorResponse):
                self._steps.explain(sent.target.batch_id, response)
            self._changed(sent)
        else:
            self._not_asked(sent, response)

    def _question(self, sent: _Sent, response: NeedsConfirmation) -> None:
        if sent.generation != self._steps.generation:
            self._steps.outlet.notify(not_asked_text(sent.target.batch_id))
            return
        confirmed = partial(self._confirmed, sent, response)
        self._steps.outlet.ask(cleanup_question(sent.target, response), confirmed)

    def _confirmed(self, sent: _Sent, asked: NeedsConfirmation) -> None:
        try:
            request = confirmed_request(sent.request, asked)
        except ValidationError:
            logger.exception("The confirmation of cleanup_cloud does not fit the request")
            self._steps.outlet.show_error(confirmation_mismatch(sent.request), None)
            return
        if isinstance(request, CleanupCloudRequest):
            # Confirmed just now: the user is here.
            kind = asked_kind(sent.target, asked)
            self._send(_Sent(sent.target, request, self._steps.generation, kind))

    def _not_asked(self, sent: _Sent, response: Response) -> None:
        """The unconfirmed request answered without a question: nothing was deleted, so it leaves the panel."""
        batch_id = sent.target.batch_id
        self._steps.dismiss(batch_id)
        if not isinstance(response, ErrorResponse):
            # It deleted without asking (the backend always asks today): show what is left.
            self._changed(sent)
            return
        if response.error.code == ERROR_CANCELLED:
            return
        if sent.generation == self._steps.generation:
            resend = partial(self._send, sent)
            self._steps.outlet.show_error(explained_error(response), resend)
        else:
            self._steps.outlet.notify(not_asked_text(batch_id))

    def _changed(self, sent: _Sent) -> None:
        # Another computer's job as the question's codes named it (else as the signal did).
        kind = sent.kind or target_kind(sent.target)
        if kind == "unknown":
            self._steps.outlet.emit(PLAN_INVALIDATED_SIGNAL)
        else:
            self._steps.outlet.emit(BATCHES_CHANGED_SIGNAL, (sent.target.batch_id,))
